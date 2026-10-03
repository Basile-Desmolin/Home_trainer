"""Champs de l'éditeur de briques ↔ modèle de séance, sans Qt (testé).

Une brique s'édite avec quatre champs texte et une unité :

    durée        « 10:00 », « 1:30:00 », « 10 » (minutes), « 10m », « 30s », « tour »
    puissance    « 150 », « 200-220 » (plage), vide = libre
    fin rampe    vide, ou la puissance d'arrivée (« 250 »)
    unité        W ou % FTP
    nom          texte libre

Une répétition s'édite avec son nombre de fois : « 3 », « 3x », « ×3 ».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..bricks import BrickSyntaxError, parse_duration, parse_power
from ..workout import PowerTarget, PowerUnit, Step

UNIT_LABELS = {PowerUnit.WATTS: "W", PowerUnit.FTP_PERCENT: "% FTP"}
OPEN_WORDS = ("tour", "open", "ouvert", "libre")

_CLOCK = re.compile(r"^(?:(\d+):)?(\d+):(\d{1,2})$")
_COUNT = re.compile(r"^[x×]?\s*(\d+)\s*(?:x|×|fois)?$", re.IGNORECASE)


@dataclass
class StepFields:
    duration: str = "10:00"
    power: str = "150"
    power_end: str = ""
    unit: PowerUnit = PowerUnit.WATTS
    name: str = ""


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "tour"
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_duration_field(text: str) -> float | None:
    """Durée en secondes ; None pour une brique « jusqu'au tour »."""
    text = text.strip().lower().replace(" ", "")
    if text in OPEN_WORDS:
        return None
    m = _CLOCK.match(text)
    if m:
        h, mn, s = (int(g) if g else 0 for g in m.groups())
        if s >= 60:
            raise BrickSyntaxError(f"durée invalide : {text!r} (secondes ≥ 60)")
        seconds = h * 3600 + mn * 60 + s
        if seconds <= 0:
            raise BrickSyntaxError("durée nulle")
        return float(seconds)
    duration = parse_duration(text)
    if duration is None:  # « open »
        return None
    return duration


def parse_count_field(text: str) -> int:
    m = _COUNT.match(text.strip())
    if not m or int(m.group(1)) < 1:
        raise BrickSyntaxError(f"nombre de répétitions invalide : {text!r} (ex. 3)")
    return int(m.group(1))


def format_count(count: int) -> str:
    return f"{count}×"


def _power(text: str, unit: PowerUnit, what: str) -> PowerTarget | None:
    text = text.strip().replace(",", ".")
    for suffix in ("%ftp", "%", "w"):
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    if not text:
        return None
    try:
        target = parse_power(text)
    except BrickSyntaxError:
        raise BrickSyntaxError(f"{what} invalide : {text!r} (ex. 150 ou 200-220)") from None
    return PowerTarget(target.low, target.high, unit)


def step_from_fields(fields: StepFields) -> Step:
    """Brique décrite par les champs ; `BrickSyntaxError` avec un message lisible sinon."""
    duration = parse_duration_field(fields.duration)
    power = _power(fields.power, fields.unit, "puissance")
    power_end = _power(fields.power_end, fields.unit, "fin de rampe")
    if power_end is not None:
        if power is None:
            raise BrickSyntaxError("une rampe a besoin d'une puissance de départ")
        if duration is None:
            raise BrickSyntaxError("une rampe doit avoir une durée (pas « tour »)")
    try:
        return Step(duration_s=duration, power=power, power_end=power_end, name=fields.name.strip())
    except ValueError as e:
        raise BrickSyntaxError(str(e)) from None


def fields_from_step(step: Step) -> StepFields:
    unit = step.power.unit if step.power is not None else PowerUnit.WATTS
    return StepFields(
        duration=format_duration(step.duration_s),
        power=_format_power(step.power),
        power_end=_format_power(step.power_end),
        unit=unit,
        name=step.name,
    )


def convert_fields(fields: StepFields, unit: PowerUnit, ftp: float) -> StepFields:
    """Mêmes champs avec les puissances exprimées dans `unit` (arrondies)."""
    if unit is fields.unit:
        return fields
    try:
        step = step_from_fields(fields)
    except BrickSyntaxError:
        return StepFields(fields.duration, fields.power, fields.power_end, unit, fields.name)
    out = fields_from_step(Step(
        duration_s=step.duration_s,
        power=step.power.in_unit(unit, ftp) if step.power else None,
        power_end=step.power_end.in_unit(unit, ftp) if step.power_end else None,
        name=step.name,
    ))
    out.unit = unit
    out.power = _round_field(out.power)
    out.power_end = _round_field(out.power_end)
    return out


def _format_power(p: PowerTarget | None) -> str:
    if p is None:
        return ""
    return _num(p.low) if p.low == p.high else f"{_num(p.low)}-{_num(p.high)}"


def _round_field(text: str) -> str:
    return "-".join(str(round(float(v))) for v in text.split("-")) if text else text


def _num(x: float) -> str:
    x = round(float(x), 1)
    return str(int(x)) if x.is_integer() else str(x)
