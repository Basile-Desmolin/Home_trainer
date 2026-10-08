"""Bilan d'une sortie : durée, distance, puissance (moyenne, normalisée, max), TSS, FC,
cadence, temps par zone de puissance et meilleures puissances sur 5 s, 1 min, 20 min…

Les mesures arrivent une fois par seconde de sortie (pauses exclues) : une mesure
vaut une seconde pour les meilleures puissances et la puissance normalisée.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from dataclasses import asdict, dataclass, field, fields
from typing import Iterable, Protocol

# Bornes hautes des zones de Coggan en % FTP (la dernière n'a pas de borne).
ZONE_LIMITS = [55, 76, 91, 106, 121, 151]
ZONE_NAMES = ["Récup", "Endurance", "Tempo", "Seuil", "VO2max", "Anaérobie", "Neuromusculaire"]
# Durées des meilleures puissances (courbe de puissance), en secondes.
CURVE_S = [1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600]
# Celles montrées dans le bilan.
HIGHLIGHT_S = [5, 60, 300, 1200, 3600]
NP_WINDOW_S = 30
MAX_GAP_S = 5.0  # un trou plus long entre deux mesures ne compte pas comme du temps roulé


class _Sample(Protocol):
    t: float
    power_w: float
    cadence_rpm: float | None
    heart_rate_bpm: float | None
    speed_kmh: float | None


def duration_label(seconds: int) -> str:
    """« 5 s », « 1 min », « 1 h »."""
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


@dataclass
class RideSummary:
    start: float  # heure du départ (secondes depuis 1970)
    title: str
    duration_s: float
    ftp: float
    avg_power_w: float
    max_power_w: float
    normalized_power_w: float
    work_kj: float
    distance_km: float | None = None
    avg_hr_bpm: float | None = None
    max_hr_bpm: float | None = None
    avg_cadence_rpm: float | None = None
    zone_s: list[float] = field(default_factory=lambda: [0.0] * len(ZONE_NAMES))
    best_w: dict[int, float] = field(default_factory=dict)  # durée (s) -> meilleure puissance moyenne
    file: str | None = None  # fichier enregistré
    # Rempli au moment du bilan, pas enregistré : meilleures puissances battues (durée -> ancien record).
    records: dict[int, float | None] = field(default_factory=dict)

    @property
    def intensity(self) -> float:
        """Facteur d'intensité (IF) : puissance normalisée / FTP."""
        return self.normalized_power_w / self.ftp if self.ftp else 0.0

    @property
    def tss(self) -> float:
        """Training Stress Score : 100 = une heure à la FTP."""
        return self.duration_s / 3600 * self.intensity ** 2 * 100

    @property
    def id(self) -> str:
        return f"{self.start:.0f}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("records")
        data["best_w"] = {str(k): v for k, v in self.best_w.items()}
        return data

    @classmethod
    def from_dict(cls, data: dict) -> RideSummary:
        known = {f.name for f in fields(cls)} - {"records"}
        values = {k: v for k, v in data.items() if k in known}
        values["best_w"] = {int(k): float(v) for k, v in (data.get("best_w") or {}).items()}
        zones = list(data.get("zone_s") or [])
        values["zone_s"] = (zones + [0.0] * len(ZONE_NAMES))[:len(ZONE_NAMES)]
        return cls(**values)


def zone_of(watts: float, ftp: float) -> int:
    pct = watts / ftp * 100 if ftp else 0
    return next((i for i, limit in enumerate(ZONE_LIMITS) if pct < limit), len(ZONE_LIMITS))


def best_average(powers: list[float], window: int) -> float | None:
    """Meilleure moyenne sur `window` mesures consécutives, None si la sortie est plus courte."""
    if window <= 0 or len(powers) < window:
        return None
    total = sum(powers[:window])
    best = total
    for i in range(window, len(powers)):
        total += powers[i] - powers[i - window]
        best = max(best, total)
    return best / window


def normalized_power(powers: list[float]) -> float:
    """Puissance normalisée : moyenne glissante sur 30 s, puissance 4, moyenne, racine 4."""
    if not powers:
        return 0.0
    if len(powers) < NP_WINDOW_S:
        return sum(powers) / len(powers)
    total = sum(powers[:NP_WINDOW_S])
    rolled = [total / NP_WINDOW_S]
    for i in range(NP_WINDOW_S, len(powers)):
        total += powers[i] - powers[i - NP_WINDOW_S]
        rolled.append(total / NP_WINDOW_S)
    return (sum(p ** 4 for p in rolled) / len(rolled)) ** 0.25


def _mean(values: Iterable[float | None]) -> float | None:
    kept = [v for v in values if v]
    return sum(kept) / len(kept) if kept else None


def summarize(samples: list[_Sample], ftp: float, title: str = "", start: float = 0.0) -> RideSummary:
    """Bilan des mesures d'une sortie."""
    powers = [max(0.0, float(s.power_w)) for s in samples]
    steps, distance, has_speed = [], 0.0, False
    previous = None
    for s in samples:
        dt = 1.0 if previous is None else min(max(s.t - previous, 0.0), MAX_GAP_S)
        previous = s.t
        steps.append(dt)
        if s.speed_kmh is not None:
            has_speed = True
            distance += s.speed_kmh / 3.6 * dt
    duration = float(sum(steps))
    zones = [0.0] * len(ZONE_NAMES)
    for p, dt in zip(powers, steps):
        zones[zone_of(p, ftp)] += dt
    best = {d: round(w, 1) for d in CURVE_S if (w := best_average(powers, d)) is not None}
    rates = [s.heart_rate_bpm for s in samples if s.heart_rate_bpm]
    return RideSummary(
        start=start, title=title, duration_s=duration, ftp=float(ftp),
        avg_power_w=sum(powers) / len(powers) if powers else 0.0,
        max_power_w=max(powers, default=0.0),
        normalized_power_w=normalized_power(powers),
        work_kj=sum(p * dt for p, dt in zip(powers, steps)) / 1000,
        distance_km=distance / 1000 if has_speed else None,
        avg_hr_bpm=_mean(rates), max_hr_bpm=max(rates) if rates else None,
        avg_cadence_rpm=_mean(s.cadence_rpm for s in samples),
        zone_s=zones, best_w=best)


def fitness(daily_tss: dict[date, float], today: date) -> tuple[float, float]:
    """Condition (CTL, moyenne sur 42 jours) et fatigue (ATL, 7 jours) au soir de `today`,
    à partir du TSS de chaque jour. La forme est CTL - ATL."""
    if not daily_tss:
        return 0.0, 0.0
    day = min(daily_tss)
    ctl = atl = 0.0
    k_ctl, k_atl = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    while day <= today:
        tss = daily_tss.get(day, 0.0)
        ctl += (tss - ctl) * k_ctl
        atl += (tss - atl) * k_atl
        day += timedelta(days=1)
    return ctl, atl
