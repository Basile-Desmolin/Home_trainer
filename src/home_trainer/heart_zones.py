"""Séances dont les consignes sont en fréquence cardiaque : conversion en puissance.

Une séance en FC peut se rouler de deux façons :

* sans ERG : le home trainer reste en résistance libre et l'on tient la FC
  cible avec les vitesses (la séance n'a pas besoin d'être convertie) ;
* en ERG : chaque cible de FC devient une cible en % FTP, par correspondance
  entre zones de FC et zones de puissance de Coggan.

La correspondance part de la FC max : la FC au seuil (celle qui va avec la
FTP) est prise à 90 % de la FC max, puis les bornes des zones de Coggan en
% de la FC au seuil sont reliées à celles des zones de puissance. C'est une
estimation : elle varie d'un cycliste à l'autre.
"""

from __future__ import annotations

from .workout import HeartRateTarget, Item, PowerTarget, Repeat, Step, Workout

THRESHOLD_PCT_OF_MAX = 90.0  # FC au seuil ≈ 90 % de la FC max
MIN_HR_MAX = 100
MAX_HR_MAX = 230
DEFAULT_HR_MAX = 190

# (% FC au seuil, % FTP) : bornes des zones de Coggan, reliées en ligne droite.
_ANCHORS = [(56.0, 35.0), (68.0, 55.0), (83.0, 75.0), (94.0, 90.0), (100.0, 100.0), (105.0, 105.0),
            (111.0, 120.0)]


def ftp_percent_for(bpm: float, hr_max: float) -> float:
    """% FTP qui correspond à `bpm` pour cette FC max (borné aux zones extrêmes)."""
    if hr_max <= 0:
        raise ValueError("la FC max doit être positive")
    pct = bpm / (hr_max * THRESHOLD_PCT_OF_MAX / 100) * 100
    if pct <= _ANCHORS[0][0]:
        return _ANCHORS[0][1]
    for (x0, y0), (x1, y1) in zip(_ANCHORS, _ANCHORS[1:]):
        if pct <= x1:
            return round(y0 + (y1 - y0) * (pct - x0) / (x1 - x0), 1)
    return _ANCHORS[-1][1]


def to_power(workout: Workout, hr_max: float) -> Workout:
    """Copie de la séance où chaque cible de FC devient une cible en % FTP.

    Les cibles de FC restent sur les briques, pour être affichées à côté.
    """
    return Workout(workout.name, [_convert(item, hr_max) for item in workout.steps],
                   workout.description, workout.ftp)


def _convert(item: Item, hr_max: float) -> Item:
    if isinstance(item, Repeat):
        return Repeat(item.count, [_convert(i, hr_max) for i in item.steps])
    if item.heart_rate is None or item.power is not None:
        return item
    return Step(item.duration_s, _target(item.heart_rate, hr_max), item.name, item.intensity, item.notes,
                _target(item.heart_rate_end, hr_max) if item.heart_rate_end else None,
                item.heart_rate, item.heart_rate_end)


def _target(hr: HeartRateTarget, hr_max: float) -> PowerTarget:
    return PowerTarget.ftp_percent(ftp_percent_for(hr.low, hr_max), ftp_percent_for(hr.high, hr_max))
