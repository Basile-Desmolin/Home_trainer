"""Formats ERG (watts) et MRC (% FTP).

Certains .erg donnent la cible en fréquence cardiaque (`MINUTES HR`, `BPM`,
`HEARTRATE`…) : les valeurs sont alors des bpm, gardées comme cibles de FC
(`Step.heart_rate`) et non lues comme des watts.

Fichiers texte utilisés par TrainerRoad, Golden Cheetah, PerfPRO, Wahoo
SYSTM, etc. La séance est une suite de points (minute, puissance) reliés par
des segments : deux points au même instant font une marche, deux valeurs
différentes à des instants différents font une rampe.

    [COURSE HEADER]
    VERSION = 2
    UNITS = ENGLISH
    FILE NAME = Séance
    FTP = 250
    MINUTES WATTS
    [END COURSE HEADER]
    [COURSE DATA]
    0.00	100
    10.00	100
    10.00	200
    ...
    [END COURSE DATA]
"""

from __future__ import annotations

import re

from ..workout import HeartRateTarget, PowerTarget, PowerUnit, Step, Workout
from .common import FormatError, compress_repeats, decode_text, require_durations

_HEADER_KV = re.compile(r"^\s*([A-Za-z ]+?)\s*=\s*(.*?)\s*$")
_UNITS_LINE = re.compile(r"^\s*MINUTES\s+(WATTS|PERCENT|HR|BPM|HEART[\s_-]*RATE|FC)\b", re.IGNORECASE)
HEART_RATE = "bpm"  # unité des fichiers dont les consignes sont en fréquence cardiaque
_NUMBER = re.compile(r"[-+]?\d*\.?\d+")


def encode(workout: Workout, unit: PowerUnit, ftp: float | None = None) -> bytes:
    fmt = ".erg" if unit is PowerUnit.WATTS else ".mrc"
    ftp = ftp or workout.ftp
    steps = list(workout.flatten())
    require_durations(steps, fmt)
    if workout.uses_heart_rate and not workout.has_power_targets:
        if unit is not PowerUnit.WATTS:
            raise FormatError("séance en fréquence cardiaque : enregistrez-la en .erg")
        return _encode_heart_rate(workout, steps)
    needs_ftp = workout.uses_ftp_percent if unit is PowerUnit.WATTS else workout.uses_watts
    if needs_ftp and not ftp:
        raise FormatError(f"une FTP est nécessaire pour écrire cette séance en {fmt}")

    lines = [
        "[COURSE HEADER]",
        "VERSION = 2",
        "UNITS = ENGLISH",
    ]
    if workout.description:
        lines.append(f"DESCRIPTION = {_one_line(workout.description)}")
    lines.append(f"FILE NAME = {_one_line(workout.name)}")
    if ftp:
        lines.append(f"FTP = {_num(ftp)}")
    lines += [
        f"MINUTES {'WATTS' if unit is PowerUnit.WATTS else 'PERCENT'}",
        "[END COURSE HEADER]",
        "[COURSE DATA]",
    ]
    t = 0.0
    for step in steps:
        start = _value(step.power, unit, ftp)
        end = _value(step.power_end, unit, ftp) if step.is_ramp else start
        lines.append(f"{t / 60:.2f}\t{_num(start)}")
        t += step.duration_s
        lines.append(f"{t / 60:.2f}\t{_num(end)}")
    lines.append("[END COURSE DATA]")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def decode(data: bytes, default_unit: PowerUnit, warnings: list[str] | None = None,
           default_name: str = "Séance") -> Workout:
    if warnings is None:
        warnings = []
    header: dict[str, str] = {}
    unit = None
    points: list[tuple[float, float]] = []
    section = ""
    for lineno, raw in enumerate(decode_text(data).splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            section = line.strip("[]").strip().upper()
            continue
        if section == "COURSE HEADER":
            m = _UNITS_LINE.match(line)
            if m:
                word = m.group(1).upper()
                unit = (PowerUnit.WATTS if word == "WATTS" else PowerUnit.FTP_PERCENT if word == "PERCENT"
                        else HEART_RATE)
            elif (kv := _HEADER_KV.match(line)):
                header[kv.group(1).upper()] = kv.group(2)
        elif section == "COURSE DATA":
            numbers = _NUMBER.findall(line)
            if len(numbers) < 2:
                raise FormatError(f"ligne {lineno} illisible : {raw.strip()!r}")
            minutes, value = float(numbers[0]), float(numbers[1])
            if points and minutes < points[-1][0]:
                raise FormatError(f"ligne {lineno} : le temps recule ({raw.strip()!r})")
            if value < 0:
                raise FormatError(f"ligne {lineno} : consigne négative")
            points.append((minutes, value))

    if len(points) < 2:
        raise FormatError("aucune donnée de séance trouvée ([COURSE DATA] vide ?)")
    unit = unit or default_unit
    ftp = _float_or_none(header.get("FTP"))

    steps: list[Step] = []
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        # Les temps sont souvent arrondis au centième de minute (20 s = 0.33).
        duration = round(t1 * 60) - round(t0 * 60)
        if duration <= 0:
            continue
        steps.append(_make_step(duration, v0, v1, unit))
    if not steps:
        raise FormatError("la séance ne dure aucune seconde")

    name = header.get("FILE NAME") or header.get("DESCRIPTION") or default_name
    description = header.get("DESCRIPTION", "") if header.get("FILE NAME") else ""
    return Workout(name=name, steps=compress_repeats(steps),
                   description=description, ftp=ftp)


def _make_step(duration: float, v0: float, v1: float, unit: PowerUnit | str) -> Step:
    if v0 == v1 == 0:
        return Step(duration)  # 0 W : pas de consigne, roue libre
    if unit == HEART_RATE:
        if v0 == 0 or v1 == 0:
            raise FormatError("fréquence cardiaque cible à 0 au milieu d'une rampe")
        end = HeartRateTarget.bpm(v1) if v1 != v0 else None
        return Step(duration, heart_rate=HeartRateTarget.bpm(v0), heart_rate_end=end)
    start = PowerTarget(v0, v0, unit)
    end = PowerTarget(v1, v1, unit) if v1 != v0 else None
    return Step(duration, start, power_end=end)


def _encode_heart_rate(workout: Workout, steps: list[Step]) -> bytes:
    lines = ["[COURSE HEADER]", "VERSION = 2", "UNITS = ENGLISH"]
    if workout.description:
        lines.append(f"DESCRIPTION = {_one_line(workout.description)}")
    lines += [f"FILE NAME = {_one_line(workout.name)}", "MINUTES HR", "[END COURSE HEADER]", "[COURSE DATA]"]
    t = 0.0
    for step in steps:
        start = step.heart_rate.mid if step.heart_rate else 0.0
        end = step.heart_rate_end.mid if step.heart_rate_end else start
        lines.append(f"{t / 60:.2f}\t{_num(start)}")
        t += step.duration_s
        lines.append(f"{t / 60:.2f}\t{_num(end)}")
    lines.append("[END COURSE DATA]")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _value(target: PowerTarget | None, unit: PowerUnit, ftp: float | None) -> float:
    if target is None:
        return 0.0
    return target.in_unit(unit, ftp).mid


def _num(x: float) -> str:
    x = round(float(x), 1)
    return str(int(x)) if x.is_integer() else str(x)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _float_or_none(text: str | None) -> float | None:
    try:
        return float(text) if text else None
    except ValueError:
        return None
