"""Lecture et écriture des fichiers de séance .fit (type de fichier « workout »).

Le format FIT ne connaît pas les rampes : une rampe est écrite comme une
suite de paliers d'au plus `RAMP_STEP_S` secondes.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import BinaryIO, Union

from garmin_fit_sdk import Decoder, Stream

from ..workout import Intensity, Item, PowerTarget, PowerUnit, Repeat, Step, Workout
from .common import FormatError
from .fit_encoder import ENUM, STRING, UINT16, UINT32, UINT32Z, FieldDef, FitWriter

PathOrFile = Union[str, os.PathLike, BinaryIO]

# Numéros de messages globaux (profil FIT)
MESG_FILE_ID = 0
MESG_WORKOUT = 26
MESG_WORKOUT_STEP = 27

FILE_TYPE_WORKOUT = 5
MANUFACTURER_DEVELOPMENT = 255
SPORT_CYCLING = 2
SUB_SPORT_INDOOR_CYCLING = 6

# wkt_step_duration
DUR_TIME = 0
DUR_DISTANCE = 1
DUR_OPEN = 5
DUR_REPEAT_UNTIL_STEPS_CMPLT = 6
DUR_REPEAT_FIRST, DUR_REPEAT_LAST = 6, 13
DUR_REPEAT_EXTRA = {18, 19, 20, 21, 22, 23, 24, 25, 26, 27}  # répétitions jusqu'à seuil

# wkt_step_target
TGT_OPEN = 2
TGT_POWER = 4
TGT_POWER_VARIANTS = {4, 7, 8, 9, 10, 11, 12, 13, 14}  # power, power_3s/10s/30s/lap…

# En FIT, une puissance « custom » vaut 0..1000 en % FTP, ou watts + 1000.
POWER_WATTS_OFFSET = 1000

# Plages Coggan en % FTP, utilisées quand le fichier ne donne qu'une zone.
POWER_ZONES = {1: (0, 55), 2: (56, 75), 3: (76, 90), 4: (91, 105),
               5: (106, 120), 6: (121, 150), 7: (151, 200)}

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)

_INTENSITY_CODES = {
    Intensity.ACTIVE: 0, Intensity.REST: 1, Intensity.WARMUP: 2, Intensity.COOLDOWN: 3,
    Intensity.RECOVERY: 4, Intensity.INTERVAL: 5, Intensity.OTHER: 6,
}
_INTENSITY_BY_CODE = {v: k for k, v in _INTENSITY_CODES.items()}

_F_TYPE = FieldDef(0, ENUM)
_F_MANUFACTURER = FieldDef(1, UINT16)
_F_PRODUCT = FieldDef(2, UINT16)
_F_TIME_CREATED = FieldDef(4, UINT32)

_F_SPORT = FieldDef(4, ENUM)
_F_NUM_VALID_STEPS = FieldDef(6, UINT16)
_F_SUB_SPORT = FieldDef(11, ENUM)

_F_MESSAGE_INDEX = FieldDef(254, UINT16)
_F_DURATION_TYPE = FieldDef(1, ENUM)
_F_DURATION_VALUE = FieldDef(2, UINT32)
_F_TARGET_TYPE = FieldDef(3, ENUM)
_F_TARGET_VALUE = FieldDef(4, UINT32)
_F_CUSTOM_LOW = FieldDef(5, UINT32)
_F_CUSTOM_HIGH = FieldDef(6, UINT32)
_F_INTENSITY = FieldDef(7, ENUM)


RAMP_STEP_S = 60


class FitWorkoutError(FormatError):
    pass


# --------------------------------------------------------------------- écriture

def encode_workout(workout: Workout, created: datetime | None = None) -> bytes:
    rows = _step_rows(workout.steps)
    if not rows:
        raise FitWorkoutError("une séance doit contenir au moins une brique")
    if len(rows) > 0xFFFE:
        raise FitWorkoutError("trop de briques pour un fichier FIT")

    created = created or datetime.now(timezone.utc)
    writer = FitWriter()
    writer.write(MESG_FILE_ID, [
        (_F_TYPE, FILE_TYPE_WORKOUT),
        (_F_MANUFACTURER, MANUFACTURER_DEVELOPMENT),
        (_F_PRODUCT, 0),
        (_F_TIME_CREATED, int((created - FIT_EPOCH).total_seconds())),
    ])
    writer.write(MESG_WORKOUT, [
        (_F_SPORT, SPORT_CYCLING),
        (_F_SUB_SPORT, SUB_SPORT_INDOOR_CYCLING),
        (_F_NUM_VALID_STEPS, len(rows)),
        (_string_field(8, [workout.name]), workout.name),
    ])

    # Taille de chaîne commune pour ne produire qu'une seule définition.
    name_field = _string_field(0, [r.get("name", "") for r in rows])
    notes_field = _string_field(8, [r.get("notes", "") for r in rows])
    for index, row in enumerate(rows):
        fields = [
            (_F_MESSAGE_INDEX, index),
            (_F_DURATION_TYPE, row["duration_type"]),
            (_F_DURATION_VALUE, row.get("duration_value")),
            (_F_TARGET_TYPE, row.get("target_type")),
            (_F_TARGET_VALUE, row.get("target_value")),
            (_F_CUSTOM_LOW, row.get("low")),
            (_F_CUSTOM_HIGH, row.get("high")),
            (_F_INTENSITY, row.get("intensity")),
        ]
        if name_field:
            fields.append((name_field, row.get("name", "")))
        if notes_field:
            fields.append((notes_field, row.get("notes", "")))
        writer.write(MESG_WORKOUT_STEP, fields)
    return writer.to_bytes()


def write_workout(workout: Workout, dest: PathOrFile, created: datetime | None = None) -> None:
    data = encode_workout(workout, created)
    if hasattr(dest, "write"):
        dest.write(data)
    else:
        with open(dest, "wb") as f:
            f.write(data)


def _string_field(num: int, values: list[str]) -> FieldDef | None:
    longest = max((len(v.encode("utf-8")) for v in values), default=0)
    if longest == 0:
        return None
    return FieldDef(num, STRING, min(longest + 1, 255))


def _step_rows(items: list[Item]) -> list[dict]:
    rows: list[dict] = []

    def emit(items: list[Item]) -> None:
        for item in items:
            if isinstance(item, Repeat):
                start = len(rows)
                if not item.steps:
                    raise FitWorkoutError("une répétition doit contenir au moins une brique")
                emit(item.steps)
                rows.append({
                    "duration_type": DUR_REPEAT_UNTIL_STEPS_CMPLT,
                    "duration_value": start,
                    "target_value": item.count,
                })
            elif item.is_ramp:
                rows.extend(_step_row(s) for s in _ramp_plateaus(item))
            else:
                rows.append(_step_row(item))

    emit(items)
    return rows


def _ramp_plateaus(step: Step) -> list[Step]:
    n = max(1, math.ceil(step.duration_s / RAMP_STEP_S))
    a, b, unit = step.power.mid, step.power_end.mid, step.power.unit
    bounds = [round(step.duration_s * i / n) for i in range(n + 1)]
    return [
        Step(bounds[i + 1] - bounds[i],
             PowerTarget(round(a + (b - a) * (i + 0.5) / n), round(a + (b - a) * (i + 0.5) / n), unit),
             name=step.name if i == 0 else "", intensity=step.intensity,
             notes=step.notes if i == 0 else "")
        for i in range(n)
    ]


def _step_row(step: Step) -> dict:
    row: dict = {
        "name": step.name,
        "notes": step.notes,
        "intensity": _INTENSITY_CODES[step.intensity],
    }
    if step.duration_s is None:
        row["duration_type"] = DUR_OPEN
    else:
        row["duration_type"] = DUR_TIME
        row["duration_value"] = round(step.duration_s * 1000)
    if step.power is None:
        row["target_type"] = TGT_OPEN
        row["target_value"] = 0
    else:
        row["target_type"] = TGT_POWER
        row["target_value"] = 0  # 0 = plage personnalisée (pas une zone)
        row["low"] = _encode_power(step.power.low, step.power.unit)
        row["high"] = _encode_power(step.power.high, step.power.unit)
    return row


def _encode_power(value: float, unit: PowerUnit) -> int:
    value = round(value)
    if unit is PowerUnit.FTP_PERCENT:
        if value > POWER_WATTS_OFFSET:
            raise FitWorkoutError("cible en % FTP limitée à 1000 %")
        return value
    # 0 W ne peut pas s'écrire « watts + 1000 » sans devenir 1000 % FTP ;
    # 0 % FTP est équivalent.
    return value + POWER_WATTS_OFFSET if value > 0 else 0


# ---------------------------------------------------------------------- lecture

def read_workout(source: PathOrFile, warnings: list[str] | None = None) -> Workout:
    """Lit un fichier de séance .fit.

    Les éléments que le modèle ne sait pas représenter exactement (durée en
    distance, cible en zone, répétition « jusqu'à un temps »…) sont
    approximés ; une explication est ajoutée à `warnings` si fourni.
    """
    if warnings is None:
        warnings = []
    if hasattr(source, "read"):
        data = source.read()
    else:
        with open(source, "rb") as f:
            data = f.read()
    return decode_workout(data, warnings)


def decode_workout(data: bytes, warnings: list[str] | None = None) -> Workout:
    if warnings is None:
        warnings = []
    decoder = Decoder(Stream.from_byte_array(bytearray(data)))
    if not decoder.is_fit():
        raise FitWorkoutError("ce n'est pas un fichier FIT")
    messages, errors = decoder.read(
        convert_types_to_strings=False,
        expand_sub_fields=False,
        expand_components=False,
        convert_datetimes_to_dates=False,
        merge_heart_rates=False,
    )
    if errors:
        raise FitWorkoutError(f"fichier FIT illisible : {errors[0]}")

    raw_steps = messages.get("workout_step_mesgs", [])
    if not raw_steps:
        file_type = (messages.get("file_id_mesgs") or [{}])[0].get("type")
        hint = " (fichier d'activité ?)" if file_type == 4 else ""
        raise FitWorkoutError(f"ce fichier ne contient pas de séance{hint}")

    workout_msg = (messages.get("workout_mesgs") or [{}])[0]
    name = workout_msg.get("wkt_name") or "Séance"
    if workout_msg.get("sport") not in (None, SPORT_CYCLING):
        warnings.append("séance prévue pour un autre sport que le vélo")

    raw_steps = sorted(
        enumerate(raw_steps), key=lambda p: (p[1].get("message_index", p[0]), p[0])
    )
    # Pile de (index du message, élément) ; une répétition englobe les
    # éléments déjà empilés à partir de l'index qu'elle désigne.
    stack: list[tuple[int, Item]] = []
    for fallback_index, msg in raw_steps:
        index = msg.get("message_index", fallback_index)
        if _is_repeat(msg.get("duration_type")):
            start = msg.get("duration_value") or 0
            body: list[Item] = []
            while stack and stack[-1][0] >= start:
                body.insert(0, stack.pop()[1])
            if not body:
                warnings.append(f"répétition vide ignorée (étape {index})")
                continue
            stack.append((start, Repeat(_repeat_count(msg, index, warnings), body)))
        else:
            stack.append((index, _decode_step(msg, index, warnings)))
    return Workout(name=name, steps=[item for _, item in stack])


def _is_repeat(duration_type: int | None) -> bool:
    return duration_type is not None and (
        DUR_REPEAT_FIRST <= duration_type <= DUR_REPEAT_LAST or duration_type in DUR_REPEAT_EXTRA
    )


def _repeat_count(msg: dict, index: int, warnings: list[str]) -> int:
    if msg.get("duration_type") == DUR_REPEAT_UNTIL_STEPS_CMPLT:
        return max(1, int(msg.get("target_value") or 1))
    warnings.append(f"répétition conditionnelle remplacée par un seul passage (étape {index})")
    return 1


def _decode_step(msg: dict, index: int, warnings: list[str]) -> Step:
    dtype = msg.get("duration_type")
    dvalue = msg.get("duration_value")
    duration_s: float | None
    if dtype == DUR_TIME and dvalue:
        duration_s = dvalue / 1000
    else:
        duration_s = None
        if dtype not in (DUR_OPEN, None, DUR_TIME):
            what = "distance" if dtype == DUR_DISTANCE else f"condition de type {dtype}"
            warnings.append(f"étape {index} : durée en {what} traitée comme ouverte")

    power = None
    if msg.get("target_type") in TGT_POWER_VARIANTS:
        power = _decode_power(msg, index, warnings)

    return Step(
        duration_s=duration_s,
        power=power,
        name=msg.get("wkt_step_name") or "",
        intensity=_INTENSITY_BY_CODE.get(msg.get("intensity"), Intensity.ACTIVE),
        notes=msg.get("notes") or "",
    )


def _decode_power(msg: dict, index: int, warnings: list[str]) -> PowerTarget | None:
    zone = msg.get("target_value")
    low, high = msg.get("custom_target_value_low"), msg.get("custom_target_value_high")
    if zone:
        if zone in POWER_ZONES:
            warnings.append(f"étape {index} : zone de puissance {zone} convertie en % FTP (Coggan)")
            return PowerTarget.ftp_percent(*POWER_ZONES[zone])
        warnings.append(f"étape {index} : zone de puissance {zone} inconnue, cible ignorée")
        return None
    if low is None and high is None:
        return None
    low = high if low is None else low
    high = low if high is None else high
    if (low > POWER_WATTS_OFFSET) != (high > POWER_WATTS_OFFSET):
        warnings.append(f"étape {index} : bornes de puissance d'unités différentes")
        low = high = max(low, high)
    if low > POWER_WATTS_OFFSET:
        low, high = low - POWER_WATTS_OFFSET, high - POWER_WATTS_OFFSET
        unit = PowerUnit.WATTS
    else:
        unit = PowerUnit.FTP_PERCENT
    return PowerTarget(min(low, high), max(low, high), unit)
