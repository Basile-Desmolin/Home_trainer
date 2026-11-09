"""Catalogue d'indicateurs commun aux trois écrans (séance, mode libre, parcours GPX).

Un indicateur dit quoi afficher dans une carte (`Metric`) à partir d'un instantané de la sortie
(`RideView`), construit une fois par rafraîchissement par la fenêtre. Chaque écran choisit ses
indicateurs et leur place (voir `MetricGrid` dans `metric.py`) : un nouvel indicateur s'écrit ici,
une seule fois, et sert aux trois écrans.
Les blocs de commande (cible, pente, intensité, boutons) restent propres à chaque écran.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..sensors import HeartRateReading
from .chart import CADENCE, HEART, TEXT, hms
from .power import Reading
from .session import FreeMode, FreeRideSession, RouteSession, WorkoutSession
from .theme import zone_index


class Mode(str, Enum):
    WORKOUT = "séance"
    FREE = "libre"
    ROUTE = "parcours"


ALL_MODES = frozenset(Mode)


def km_text(meters: float, decimals: int = 1) -> str:
    return f"{meters / 1000:.{decimals}f} km".replace(".", ",")


def kmh_text(speed_kmh: float) -> str:
    return f"{speed_kmh:.1f}".replace(".", ",")


@dataclass(frozen=True)
class RideView:
    """Ce que montrent les cartes à un instant donné."""

    session: WorkoutSession | FreeRideSession | RouteSession
    reading: Reading | None  # dernière mesure (puissance, cadence)
    ftp: float
    speed_kmh: float | None = None  # vitesse affichée : home trainer, estimée, ou celle du parcours
    gear: str | None = None  # braquet virtuel en cours (« 14 / 24 »), s'il s'applique
    heart: HeartRateReading | None = None
    heart_off: str = ""  # sans fréquence reçue : pourquoi (sous « Off »)

    @property
    def mode(self) -> Mode:
        if isinstance(self.session, RouteSession):
            return Mode.ROUTE
        return Mode.FREE if isinstance(self.session, FreeRideSession) else Mode.WORKOUT


@dataclass(frozen=True)
class Shown:
    """Contenu d'une carte."""

    value: str
    sub: str = ""
    unit: str | None = None  # None : unité détachée de la valeur (« 238 W » → « 238 » + « W »)
    zone: int | None = None  # zone de puissance (barre de zones)
    progress: float | None = None  # avancement de 0 à 1 (barre d'avancement)


@dataclass(frozen=True)
class Indicator:
    key: str
    title: str
    show: Callable[[RideView], Shown]
    color: str = TEXT
    modes: frozenset[Mode] = ALL_MODES  # écrans où l'indicateur a un sens
    gauge: str | None = None  # « zones » ou « progress » : barre sous la valeur


# --- indicateurs --------------------------------------------------------------

def _power(v: RideView) -> Shown:
    r = v.reading
    sub = f"{r.power_w / v.ftp * 100:.0f} % FTP" if r else ""
    s = v.session
    average = None if isinstance(s, WorkoutSession) else s.average_power()
    if average is not None:
        sub += f"{' · ' if sub else ''}moy. {average:.0f} W"
    return Shown(f"{r.power_w:.0f} W" if r else "—", sub, zone=zone_index(r.power_w if r else None, v.ftp))


def _cadence(v: RideView) -> Shown:
    r, s = v.reading, v.session
    sub = "tr/min"
    if not isinstance(s, RouteSession) and v.speed_kmh is not None:  # le parcours a sa carte vitesse
        sub += f" · {kmh_text(v.speed_kmh)} km/h"
    # En mode libre, le braquet ne sert qu'en pente.
    erg = isinstance(s, FreeRideSession) and s.mode is FreeMode.ERG
    if v.gear and not erg:
        sub += f" · braquet {v.gear}"
    return Shown(f"{r.cadence_rpm:.0f}" if r and r.cadence_rpm is not None else "—", sub)


def _heart(v: RideView) -> Shown:
    heart = v.heart
    if heart is None:
        return Shown("Off", v.heart_off)
    average = v.session.average_heart_rate()
    sub = f"bpm · moy. {average:.0f}" if average else "bpm"
    if heart.battery_pct is not None and heart.battery_pct <= 20:
        sub += f" · batterie {heart.battery_pct} %"
    if heart.contact is False:
        sub += " · mauvais contact"
    return Shown(f"{heart.bpm}", sub)


def _elapsed(v: RideView) -> Shown:
    s = v.session
    return Shown(hms(s.elapsed_s), f"D+ fait {s.climb_done_m:.0f} m" if isinstance(s, RouteSession) else "")


def _workout_remaining(v: RideView) -> Shown:
    s = v.session
    assert isinstance(s, WorkoutSession)
    return Shown(hms(s.total_remaining_s), f"écoulé : {hms(s.elapsed_s)}",
                 progress=s.position_s / s.total_s if s.total_s else 0.0)


def _route_remaining(v: RideView) -> Shown:
    s = v.session
    assert isinstance(s, RouteSession)
    return Shown(km_text(s.remaining_m), f"D+ restant {s.climb_remaining_m:.0f} m · fait {km_text(s.distance_m)}")


def _speed(v: RideView) -> Shown:
    s = v.session
    average = s.average_speed_kmh() if isinstance(s, RouteSession) else None
    return Shown("—" if v.speed_kmh is None else kmh_text(v.speed_kmh),
                 "km/h" + (f" · moy. {kmh_text(average)}" if average else ""))


CATALOG: dict[str, Indicator] = {i.key: i for i in (
    Indicator("power", "PUISSANCE", _power, gauge="zones"),
    Indicator("cadence", "CADENCE", _cadence, CADENCE),
    Indicator("heart", "CARDIO", _heart, HEART),
    Indicator("elapsed", "TEMPS", _elapsed),
    Indicator("workout_remaining", "RESTE AU TOTAL", _workout_remaining, modes=frozenset({Mode.WORKOUT}),
              gauge="progress"),
    Indicator("route_remaining", "RESTE", _route_remaining, modes=frozenset({Mode.ROUTE})),
    Indicator("speed", "VITESSE", _speed),
)}
