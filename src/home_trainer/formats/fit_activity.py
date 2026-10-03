"""Écriture d'une sortie en .fit d'activité (type de fichier « activity »).

C'est le fichier qu'attendent Strava, Nolio, Garmin Connect, intervals.icu… :
une mesure par seconde (puissance, cadence, cardio, vitesse, distance, pente),
les pauses, un tour par brique de séance, et le résumé de la sortie.

    points = [ActivityPoint(at=1759480000 + i, power_w=200, cadence_rpm=90, speed_kmh=32) for i in range(60)]
    write_activity(points, "sortie.fit")

`at` est l'heure de la mesure (secondes depuis 1970, UTC). Un trou de plus de
`PAUSE_GAP_S` secondes entre deux mesures est une pause : le chronomètre
s'arrête, et la distance n'avance pas.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .fit import FIT_EPOCH, MANUFACTURER_DEVELOPMENT, SPORT_CYCLING, SUB_SPORT_INDOOR_CYCLING
from .fit_encoder import ENUM, SINT16, UINT8, UINT16, UINT32, UINT32Z, FieldDef, FitWriter

PAUSE_GAP_S = 5.0
SAMPLE_S = 1.0  # durée attribuée à une mesure qui suit une pause

MESG_FILE_ID = 0
MESG_SESSION = 18
MESG_LAP = 19
MESG_RECORD = 20
MESG_EVENT = 21
MESG_ACTIVITY = 34

FILE_TYPE_ACTIVITY = 4
EVENT_TIMER, EVENT_SESSION, EVENT_LAP, EVENT_ACTIVITY = 0, 8, 9, 26
EVENT_START, EVENT_STOP, EVENT_STOP_ALL = 0, 1, 4

# Champs : (numéro, type)
F_TIMESTAMP = FieldDef(253, UINT32)
F_MESSAGE_INDEX = FieldDef(254, UINT16)
F_EVENT = FieldDef(0, ENUM)
F_EVENT_TYPE = FieldDef(1, ENUM)


@dataclass(frozen=True)
class ActivityPoint:
    at: float  # heure de la mesure, secondes depuis 1970 (UTC)
    power_w: float
    cadence_rpm: float | None = None
    heart_rate_bpm: float | None = None
    speed_kmh: float | None = None
    grade_pct: float | None = None
    lap: int = 0  # numéro de tour (brique de la séance) ; un changement ouvre un nouveau tour
    timer_s: float | None = None  # temps de pédalage au moment de la mesure (chronomètre, pauses exclues)


@dataclass(frozen=True)
class ActivitySummary:
    start: float
    elapsed_s: float  # du départ à la dernière mesure, pauses comprises
    timer_s: float  # temps de pédalage, pauses exclues
    distance_m: float
    avg_power_w: float
    max_power_w: float
    normalized_power_w: float
    work_j: float
    avg_heart_rate: float | None
    max_heart_rate: float | None
    avg_cadence: float | None
    max_cadence: float | None
    max_speed_kmh: float

    @property
    def avg_speed_kmh(self) -> float:
        return self.distance_m / self.timer_s * 3.6 if self.timer_s else 0.0


def _durations(points: list[ActivityPoint]) -> list[float]:
    """Durée couverte par chaque mesure : l'écart avec la précédente au chronomètre (à défaut à
    l'horloge), ou `SAMPLE_S` pour la première et après une pause."""
    out = []
    for i, p in enumerate(points):
        prev = points[i - 1] if i else None
        if prev is None or p.at - prev.at > PAUSE_GAP_S:
            gap = SAMPLE_S
        elif p.timer_s is not None and prev.timer_s is not None:
            gap = p.timer_s - prev.timer_s
        else:
            gap = p.at - prev.at
        out.append(gap if 0 < gap <= PAUSE_GAP_S else SAMPLE_S)
    return out


def _distances(points: list[ActivityPoint], durations: list[float]) -> list[float]:
    total, out = 0.0, []
    for p, dt in zip(points, durations):
        total += (p.speed_kmh or 0.0) / 3.6 * dt
        out.append(total)
    return out


def normalized_power(powers: list[float], window: int = 30) -> float:
    """Puissance normalisée (moyenne glissante sur 30 s, puissance 4, moyenne, racine 4)."""
    if not powers:
        return 0.0
    if len(powers) < window:
        return sum(powers) / len(powers)
    rolling, acc = [], sum(powers[:window])
    rolling.append(acc / window)
    for i in range(window, len(powers)):
        acc += powers[i] - powers[i - window]
        rolling.append(acc / window)
    return (sum(r ** 4 for r in rolling) / len(rolling)) ** 0.25


def summarize(points: list[ActivityPoint]) -> ActivitySummary:
    if not points:
        raise ValueError("sortie vide : aucune mesure")
    durations = _durations(points)
    distances = _distances(points, durations)
    powers = [p.power_w for p in points]
    hearts = [p.heart_rate_bpm for p in points if p.heart_rate_bpm]
    cadences = [p.cadence_rpm for p in points if p.cadence_rpm]
    return ActivitySummary(
        start=points[0].at,
        elapsed_s=points[-1].at - points[0].at + SAMPLE_S,
        timer_s=sum(durations),
        distance_m=distances[-1],
        avg_power_w=sum(powers) / len(powers),
        max_power_w=max(powers),
        normalized_power_w=normalized_power(powers),
        work_j=sum(p * dt for p, dt in zip(powers, durations)),
        avg_heart_rate=sum(hearts) / len(hearts) if hearts else None,
        max_heart_rate=max(hearts) if hearts else None,
        avg_cadence=sum(cadences) / len(cadences) if cadences else None,
        max_cadence=max(cadences) if cadences else None,
        max_speed_kmh=max((p.speed_kmh or 0.0) for p in points),
    )


def _fit_time(at: float) -> int:
    return int(round(at - FIT_EPOCH.timestamp()))


def _opt(value: float | None, scale: float = 1.0) -> int | None:
    return None if value is None else int(round(value * scale))


def _summary_fields(s: ActivitySummary, lap: bool) -> list[tuple[FieldDef, object]]:
    """Champs communs aux messages lap (19) et session (18), dont les numéros diffèrent un peu."""
    n = {  # numéro du champ : (lap, session)
        "avg_speed": (13, 14), "max_speed": (14, 15), "avg_hr": (15, 16), "max_hr": (16, 17),
        "avg_cad": (17, 18), "max_cad": (18, 19), "avg_power": (19, 20), "max_power": (20, 21),
        "np": (33, 34), "work": (41, 48),
    }
    i = 0 if lap else 1
    return [
        (FieldDef(2, UINT32), _fit_time(s.start)),  # start_time
        (FieldDef(7, UINT32), _opt(s.elapsed_s, 1000)),  # total_elapsed_time
        (FieldDef(8, UINT32), _opt(s.timer_s, 1000)),  # total_timer_time
        (FieldDef(9, UINT32), _opt(s.distance_m, 100)),  # total_distance
        (FieldDef(11, UINT16), _opt(s.work_j / 1000)),  # total_calories ≈ travail en kJ
        (FieldDef(n["avg_speed"][i], UINT16), _opt(s.avg_speed_kmh / 3.6, 1000)),
        (FieldDef(n["max_speed"][i], UINT16), _opt(s.max_speed_kmh / 3.6, 1000)),
        (FieldDef(n["avg_hr"][i], UINT8), _opt(s.avg_heart_rate)),
        (FieldDef(n["max_hr"][i], UINT8), _opt(s.max_heart_rate)),
        (FieldDef(n["avg_cad"][i], UINT8), _opt(s.avg_cadence)),
        (FieldDef(n["max_cad"][i], UINT8), _opt(s.max_cadence)),
        (FieldDef(n["avg_power"][i], UINT16), _opt(s.avg_power_w)),
        (FieldDef(n["max_power"][i], UINT16), _opt(s.max_power_w)),
        (FieldDef(n["np"][i], UINT16), _opt(s.normalized_power_w)),
        (FieldDef(n["work"][i], UINT32), _opt(s.work_j)),
    ]


def encode_activity(points: list[ActivityPoint], serial_number: int | None = None) -> bytes:
    """Fichier .fit d'activité complet (vélo d'intérieur) à partir des mesures, dans l'ordre."""
    points = sorted(points, key=lambda p: p.at)
    summary = summarize(points)
    durations = _durations(points)
    distances = _distances(points, durations)
    w = FitWriter()
    start = _fit_time(points[0].at)
    end = _fit_time(points[-1].at)
    w.write(MESG_FILE_ID, [
        (FieldDef(0, ENUM), FILE_TYPE_ACTIVITY),
        (FieldDef(1, UINT16), MANUFACTURER_DEVELOPMENT),
        (FieldDef(2, UINT16), 0),
        (FieldDef(3, UINT32Z), serial_number or (start & 0xFFFFFFFF) or 1),
        (FieldDef(4, UINT32), start),
    ])

    def event(at: int, kind: int, event_type: int) -> None:
        w.write(MESG_EVENT, [(F_TIMESTAMP, at), (F_EVENT, kind), (F_EVENT_TYPE, event_type)])

    event(start, EVENT_TIMER, EVENT_START)
    for i, (p, distance) in enumerate(zip(points, distances)):
        if i and p.at - points[i - 1].at > PAUSE_GAP_S:  # pause : chronomètre arrêté pendant le trou
            event(_fit_time(points[i - 1].at), EVENT_TIMER, EVENT_STOP)
            event(_fit_time(p.at), EVENT_TIMER, EVENT_START)
        w.write(MESG_RECORD, [
            (F_TIMESTAMP, _fit_time(p.at)),
            (FieldDef(3, UINT8), _opt(p.heart_rate_bpm)),
            (FieldDef(4, UINT8), _opt(p.cadence_rpm)),
            (FieldDef(5, UINT32), _opt(distance, 100)),
            (FieldDef(6, UINT16), _opt(None if p.speed_kmh is None else p.speed_kmh / 3.6, 1000)),
            (FieldDef(7, UINT16), _opt(max(p.power_w, 0))),
            (FieldDef(9, SINT16), _opt(p.grade_pct, 100)),
        ])
    event(end, EVENT_TIMER, EVENT_STOP_ALL)

    laps: list[list[ActivityPoint]] = []
    for p in points:
        if not laps or p.lap != laps[-1][-1].lap:
            laps.append([])
        laps[-1].append(p)
    for index, lap_points in enumerate(laps):
        lap = summarize(lap_points)
        w.write(MESG_LAP, [(F_MESSAGE_INDEX, index), (F_TIMESTAMP, _fit_time(lap_points[-1].at)),
                           (F_EVENT, EVENT_LAP), (F_EVENT_TYPE, EVENT_STOP),
                           (FieldDef(25, ENUM), SPORT_CYCLING), (FieldDef(39, ENUM), SUB_SPORT_INDOOR_CYCLING),
                           *_summary_fields(lap, lap=True)])
    w.write(MESG_SESSION, [(F_MESSAGE_INDEX, 0), (F_TIMESTAMP, end), (F_EVENT, EVENT_SESSION),
                           (F_EVENT_TYPE, EVENT_STOP), (FieldDef(5, ENUM), SPORT_CYCLING),
                           (FieldDef(6, ENUM), SUB_SPORT_INDOOR_CYCLING),
                           (FieldDef(25, UINT16), 0), (FieldDef(26, UINT16), len(laps)),
                           *_summary_fields(summary, lap=False)])
    offset = datetime.fromtimestamp(points[-1].at).astimezone().utcoffset()
    local = end + int(offset.total_seconds()) if offset else end
    w.write(MESG_ACTIVITY, [(F_TIMESTAMP, end), (FieldDef(0, UINT32), _opt(summary.timer_s, 1000)),
                            (FieldDef(1, UINT16), 1), (FieldDef(2, ENUM), 0),  # manuelle
                            (FieldDef(3, ENUM), EVENT_ACTIVITY), (FieldDef(4, ENUM), EVENT_STOP),
                            (FieldDef(5, UINT32), local)])
    return w.to_bytes()


def write_activity(points: list[ActivityPoint], dest: str | os.PathLike) -> Path:
    path = Path(dest)
    path.write_bytes(encode_activity(points))
    return path


def activity_file_name(start: float, title: str) -> str:
    """« 2026-10-03_0815_Sweet-spot.fit » : triable, sans caractère interdit sous Windows."""
    stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime(start))
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in title.strip()).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{stamp}_{safe[:40] or 'sortie'}.fit"
