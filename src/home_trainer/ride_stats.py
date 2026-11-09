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

from .heart_zones import ESIE_NAMES, esie_zone

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
    # FC max du profil au moment de la sortie, et temps dans chaque zone de FC ESIE (i1 à i5) ;
    # vides sans FC max ou sans cardio (sorties d'avant la version 0.2 aussi).
    hr_max: float | None = None
    hr_zone_s: list[float] = field(default_factory=list)

    @property
    def calories_kcal(self) -> float:
        """Calories dépensées ≈ travail fourni : 1 kJ ≈ 1 kcal (rendement du corps ≈ 24 %, compensé
        par la conversion 1 kcal = 4,184 kJ)."""
        return self.work_kj

    def hr_pct(self, bpm: float | None) -> float | None:
        """`bpm` en % de la FC max du profil, None sans l'un ou l'autre."""
        return bpm / self.hr_max * 100 if bpm and self.hr_max else None
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
        heart = data.get("hr_zone_s")
        values["hr_zone_s"] = ([float(x) for x in heart][:len(ESIE_NAMES)]
                               if isinstance(heart, list) and len(heart) == len(ESIE_NAMES) else [])
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


class LiveStats:
    """Moyennes, max et puissance normalisée d'une sortie en cours, tenus au fil des mesures.

    `sync(samples)` ne lit que les mesures arrivées depuis l'appel précédent : l'affichage
    (5 fois par seconde) ne reparcourt pas toute la sortie. Même calcul que `normalized_power` et les
    moyennes de la sortie, aux arrondis près (`sum()` compense les erreurs d'arrondi, pas une addition
    au fil de l'eau : écart relatif de l'ordre de 10⁻¹³).
    Une liste remplacée ou raccourcie (sortie reprise, recommencée) est relue depuis le début.
    """

    def __init__(self) -> None:
        self._samples: list | None = None
        self._last = None  # dernière mesure lue : détecte une liste modifiée
        self.count = 0
        self.power_sum = 0.0
        self.power_max: float | None = None
        self._hr_sum = 0.0
        self._hr_count = 0
        self.heart_rate_max: float | None = None
        self._cadence_sum = 0.0
        self._cadence_count = 0
        self._speed_sum = 0.0
        self._speed_count = 0
        self.speed_max: float | None = None
        self._balance_sum = 0.0
        self._balance_count = 0
        self._window: list[float] = []  # 30 dernières puissances (NP)
        self._window_sum = 0.0
        self._rolled4_sum = 0.0  # somme des moyennes glissantes sur 30 s, à la puissance 4
        self._rolled_count = 0
        self.lap: int | None = None  # brique (tour) de la dernière mesure
        self._lap_sum = 0.0
        self._lap_count = 0
        self._lap_target_sum = 0.0
        self._lap_target_count = 0
        self.tour_key: tuple[int, int] | None = None  # tour (brique, tours manuels) de la dernière mesure
        self.tour_number = 0  # 1 pour le premier tour
        self.tour_seconds = 0  # une mesure par seconde
        self._tour_sum = 0.0

    def sync(self, samples: list) -> LiveStats:
        n = self.count
        if samples is not self._samples or len(samples) < n or (n and samples[n - 1] is not self._last):
            self.__init__()
            self._samples = samples
        for x in samples[self.count:]:
            self._add(x)
        return self

    def _add(self, x) -> None:
        self._last = x
        self.count += 1
        p = x.power_w
        self.power_sum += p
        self.power_max = p if self.power_max is None else max(self.power_max, p)
        if x.heart_rate_bpm:
            self._hr_sum += x.heart_rate_bpm
            self._hr_count += 1
            self.heart_rate_max = max(self.heart_rate_max or 0.0, x.heart_rate_bpm)
        if x.cadence_rpm:
            self._cadence_sum += x.cadence_rpm
            self._cadence_count += 1
        if x.speed_kmh is not None:
            self._speed_sum += x.speed_kmh
            self._speed_count += 1
            self.speed_max = max(self.speed_max or 0.0, x.speed_kmh)
        balance = getattr(x, "balance_left_pct", None)
        if balance is not None:
            self._balance_sum += balance
            self._balance_count += 1
        # NP : même calcul que `normalized_power`, une moyenne glissante de plus par mesure.
        self._window.append(p)
        if len(self._window) < NP_WINDOW_S:
            self._window_sum += p
        elif len(self._window) == NP_WINDOW_S:
            self._window_sum = sum(self._window)
            self._roll()
        else:
            self._window_sum += p - self._window.pop(0)
            self._roll()
        lap = getattr(x, "lap", 0)
        if lap != self.lap:
            self.lap, self._lap_sum, self._lap_count = lap, 0.0, 0
            self._lap_target_sum, self._lap_target_count = 0.0, 0
        self._lap_sum += p
        self._lap_count += 1
        target = getattr(x, "target_w", None)
        if target is not None:
            self._lap_target_sum += target
            self._lap_target_count += 1
        tour = (lap, getattr(x, "manual_lap", 0))  # même découpage que les tours du .fit
        if tour != self.tour_key:
            self.tour_key, self.tour_seconds, self._tour_sum = tour, 0, 0.0
            self.tour_number += 1
        self.tour_seconds += 1
        self._tour_sum += p

    def _roll(self) -> None:
        self._rolled4_sum += (self._window_sum / NP_WINDOW_S) ** 4
        self._rolled_count += 1

    @staticmethod
    def _ratio(total: float, count: int) -> float | None:
        return total / count if count else None

    @property
    def average_power(self) -> float | None:
        return self._ratio(self.power_sum, self.count)

    @property
    def normalized_power(self) -> float | None:
        """NP de la sortie, None avant 30 s (en dessous, `normalized_power` rend la moyenne)."""
        if not self._rolled_count:
            return None
        return (self._rolled4_sum / self._rolled_count) ** 0.25

    @property
    def average_heart_rate(self) -> float | None:
        return self._ratio(self._hr_sum, self._hr_count)

    @property
    def average_cadence(self) -> float | None:
        return self._ratio(self._cadence_sum, self._cadence_count)

    @property
    def average_speed_kmh(self) -> float | None:
        return self._ratio(self._speed_sum, self._speed_count)

    @property
    def average_balance_left_pct(self) -> float | None:
        return self._ratio(self._balance_sum, self._balance_count)

    @property
    def lap_power(self) -> float | None:
        """Puissance moyenne de la brique (du tour) de la dernière mesure."""
        return self._ratio(self._lap_sum, self._lap_count)

    @property
    def tour_power(self) -> float | None:
        """Puissance moyenne du tour de la dernière mesure."""
        return self._ratio(self._tour_sum, self.tour_seconds)

    @property
    def lap_target(self) -> float | None:
        """Cible moyenne sur les mesures de cette brique qui en avaient une."""
        return self._ratio(self._lap_target_sum, self._lap_target_count)


def _mean(values: Iterable[float | None]) -> float | None:
    kept = [v for v in values if v]
    return sum(kept) / len(kept) if kept else None


def summarize(samples: list[_Sample], ftp: float, title: str = "", start: float = 0.0,
              hr_max: float | None = None) -> RideSummary:
    """Bilan des mesures d'une sortie ; avec la FC max du profil, temps par zone de FC (ESIE)."""
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
    heart_zones: list[float] = []
    if hr_max and rates:  # secondes sans FC (ceinture décrochée) : dans aucune zone
        heart_zones = [0.0] * len(ESIE_NAMES)
        for s, dt in zip(samples, steps):
            zone = esie_zone(s.heart_rate_bpm, hr_max)
            if zone is not None:
                heart_zones[zone] += dt
    return RideSummary(
        start=start, title=title, duration_s=duration, ftp=float(ftp),
        avg_power_w=sum(powers) / len(powers) if powers else 0.0,
        max_power_w=max(powers, default=0.0),
        normalized_power_w=normalized_power(powers),
        work_kj=sum(p * dt for p, dt in zip(powers, steps)) / 1000,
        distance_km=distance / 1000 if has_speed else None,
        avg_hr_bpm=_mean(rates), max_hr_bpm=max(rates) if rates else None,
        avg_cadence_rpm=_mean(s.cadence_rpm for s in samples),
        zone_s=zones, best_w=best, hr_max=float(hr_max) if hr_max else None, hr_zone_s=heart_zones)


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
