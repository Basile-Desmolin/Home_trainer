"""Format ZWO (Zwift, repris par MyWhoosh, TrainerDay, intervals.icu…).

Fichier XML où toutes les puissances sont des fractions de la FTP :

    <workout_file>
      <name>Sweet spot</name>
      <sportType>bike</sportType>
      <workout>
        <Warmup Duration="600" PowerLow="0.5" PowerHigh="0.75"/>
        <IntervalsT Repeat="3" OnDuration="600" OffDuration="180" OnPower="0.9" OffPower="0.55"/>
        <SteadyState Duration="300" Power="0.65"/>
        <FreeRide Duration="300"/>
        <Cooldown Duration="600" PowerLow="0.65" PowerHigh="0.4"/>
      </workout>
    </workout_file>
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..workout import Intensity, Item, PowerTarget, PowerUnit, Repeat, Step, Workout
from .common import FormatError, compress_repeats, require_durations

_RAMPS = {"warmup": Intensity.WARMUP, "cooldown": Intensity.COOLDOWN, "ramp": Intensity.ACTIVE}
_STEADY = {"steadystate", "solidstate"}
_FREE = {"freeride", "maxeffort"}


# --------------------------------------------------------------------- écriture

def encode(workout: Workout, ftp: float | None = None) -> bytes:
    ftp = ftp or workout.ftp
    require_durations(list(workout.flatten()), ".zwo")
    if workout.uses_watts and not ftp:
        raise FormatError("une FTP est nécessaire pour écrire des watts en .zwo (Zwift ne "
                          "connaît que le % FTP)")

    root = ET.Element("workout_file")
    ET.SubElement(root, "author").text = "home-trainer"
    ET.SubElement(root, "name").text = workout.name
    ET.SubElement(root, "description").text = workout.description
    ET.SubElement(root, "sportType").text = "bike"
    ET.SubElement(root, "tags")
    body = ET.SubElement(root, "workout")
    _emit(body, workout.steps, ftp)
    ET.indent(root, space="    ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=False) + b"\n"


def _emit(parent: ET.Element, items: list[Item], ftp: float | None) -> None:
    for item in items:
        if isinstance(item, Repeat):
            if _is_simple_interval(item):
                on, off = item.steps
                el = ET.SubElement(parent, "IntervalsT", {
                    "Repeat": str(item.count),
                    "OnDuration": _secs(on.duration_s),
                    "OffDuration": _secs(off.duration_s),
                    "OnPower": _frac(on.power, ftp),
                    "OffPower": _frac(off.power, ftp),
                })
                _text_event(el, on.notes or on.name)
            else:
                for _ in range(item.count):
                    _emit(parent, item.steps, ftp)
            continue

        step = item
        attrs = {"Duration": _secs(step.duration_s)}
        if step.power is None:
            tag = "FreeRide"
            attrs["FlatRoad"] = "1"
        elif step.is_ramp:
            tag = {Intensity.WARMUP: "Warmup", Intensity.COOLDOWN: "Cooldown"}.get(
                step.intensity, "Ramp")
            attrs["PowerLow"] = _frac(step.power, ftp)
            attrs["PowerHigh"] = _frac(step.power_end, ftp)
        else:
            tag = "SteadyState"
            attrs["Power"] = _frac(step.power, ftp)
        _text_event(ET.SubElement(parent, tag, attrs), step.notes or step.name)


def _is_simple_interval(rep: Repeat) -> bool:
    return (len(rep.steps) == 2 and all(
        isinstance(s, Step) and s.power is not None and not s.is_ramp for s in rep.steps)
        and not rep.steps[1].notes and not rep.steps[1].name)


def _text_event(el: ET.Element, message: str) -> None:
    if message:
        ET.SubElement(el, "textevent", {"timeoffset": "0", "message": message})


def _secs(seconds: float | None) -> str:
    return str(round(seconds))


def _frac(target: PowerTarget, ftp: float | None) -> str:
    pct = target.in_unit(PowerUnit.FTP_PERCENT, ftp).mid
    return f"{pct / 100:.4f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------- lecture

def decode(data: bytes, warnings: list[str] | None = None, default_name: str = "Séance") -> Workout:
    if warnings is None:
        warnings = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FormatError(f"XML invalide : {e}") from None
    if _tag(root) != "workout_file":
        raise FormatError("ce n'est pas un fichier Zwift (.zwo)")

    sport = (_child_text(root, "sporttype") or "bike").strip().lower()
    if sport != "bike":
        warnings.append(f"séance prévue pour « {sport} », pas pour le vélo")

    body = next((c for c in root if _tag(c) == "workout"), None)
    if body is None or len(body) == 0:
        raise FormatError("le fichier ne contient pas de séance (<workout> vide)")

    items: list[Item] = []
    for el in body:
        items.extend(_decode_element(el, warnings))
    if not items:
        raise FormatError("aucune brique reconnue dans la séance")

    # Les briques à la suite (hors IntervalsT) peuvent cacher des répétitions.
    steps_run: list[Step] = []
    result: list[Item] = []
    for item in items + [None]:
        if isinstance(item, Step):
            steps_run.append(item)
            continue
        result.extend(compress_repeats(steps_run))
        steps_run = []
        if item is not None:
            result.append(item)

    return Workout(
        name=(_child_text(root, "name") or "").strip() or default_name,
        steps=result,
        description=(_child_text(root, "description") or "").strip(),
    )


def _decode_element(el: ET.Element, warnings: list[str]) -> list[Item]:
    tag = _tag(el)
    a = {k.lower(): v for k, v in el.attrib.items()}
    notes = " / ".join(
        t.attrib.get("message", "") for t in el if _tag(t) == "textevent" and t.attrib.get("message"))

    if tag == "textevent":
        return []
    if tag == "intervalst":
        count = int(_num(a, "repeat", 1))
        on = _step(a, "onduration", _pct(a, "onpower", "poweronlow"),
                   _pct(a, "poweronhigh") if "poweronhigh" in a else None, Intensity.INTERVAL, notes)
        off = _step(a, "offduration", _pct(a, "offpower", "powerofflow"),
                    _pct(a, "poweroffhigh") if "poweroffhigh" in a else None, Intensity.RECOVERY, "")
        steps = [s for s in (on, off) if s is not None]
        if not steps or count < 1:
            warnings.append("bloc IntervalsT vide ignoré")
            return []
        return [Repeat(count, steps)]

    if "duration" not in a:
        warnings.append(f"élément <{el.tag}> sans durée ignoré")
        return []
    if tag in _RAMPS:
        step = _step(a, "duration", _pct(a, "powerlow", "power"), _pct(a, "powerhigh"),
                     _RAMPS[tag], notes)
    elif tag in _STEADY:
        low, high = _pct(a, "power", "powerlow"), _pct(a, "power", "powerhigh")
        target = PowerTarget.ftp_percent(min(low, high), max(low, high)) if low is not None else None
        step = _make(a, "duration", target, None, Intensity.ACTIVE, notes)
    elif tag in _FREE:
        if tag == "maxeffort":
            notes = notes or "effort maximal"
        step = _make(a, "duration", None, None, Intensity.ACTIVE, notes)
    else:
        warnings.append(f"élément <{el.tag}> inconnu, traité comme roue libre")
        step = _make(a, "duration", None, None, Intensity.ACTIVE, notes)
    return [step] if step is not None else []


def _step(a: dict, dur_key: str, start: float | None, end: float | None,
          intensity: Intensity, notes: str) -> Step | None:
    power = PowerTarget.ftp_percent(start) if start is not None else None
    power_end = PowerTarget.ftp_percent(end) if (end is not None and power is not None) else None
    return _make(a, dur_key, power, power_end, intensity, notes)


def _make(a: dict, dur_key: str, power, power_end, intensity: Intensity, notes: str) -> Step | None:
    duration = _num(a, dur_key, 0)
    if duration <= 0:
        return None
    return Step(duration, power, intensity=intensity, notes=notes, power_end=power_end)


def _pct(a: dict, *keys: str) -> float | None:
    for key in keys:
        if key in a:
            return round(_num(a, key, 0) * 100, 2)
    return None


def _num(a: dict, key: str, default: float) -> float:
    try:
        return float(a[key])
    except (KeyError, ValueError):
        return default


def _tag(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1].lower()


def _child_text(root: ET.Element, name: str) -> str | None:
    el = next((c for c in root if _tag(c) == name), None)
    return el.text if el is not None else None
