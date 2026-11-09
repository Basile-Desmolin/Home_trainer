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

from PySide6.QtGui import QColor

from ..heart_zones import ESIE_NAMES, esie_zone
from ..sensors import HeartRateReading
from .chart import CADENCE, HEART, TEXT, hms, zone_color
from .power import Reading
from .session import FreeMode, FreeRideSession, RouteSession, State, WorkoutSession
from .theme import WAIT as ALERT, zone_index


class Mode(str, Enum):
    WORKOUT = "séance"
    FREE = "libre"
    ROUTE = "parcours"


ALL_MODES = frozenset(Mode)


def km_text(meters: float, decimals: int = 1) -> str:
    return f"{meters / 1000:.{decimals}f} km".replace(".", ",")


def kmh_text(speed_kmh: float) -> str:
    return f"{speed_kmh:.1f}".replace(".", ",")


# Pente : du vert (plat) au rouge (mur) ; en parcours, les descentes en bleu.
GRADES = [(1, "#3fb37f"), (4, "#e7c43a"), (8, "#f08a2c"), (1_000, "#e5484d")]
DESCENT = "#3d8bd9"


def grade_text(grade_pct: float) -> str:
    """« 4,5 % », « −2 % » : à la française."""
    text = f"{grade_pct:.1f}".rstrip("0").rstrip(".").replace(".", ",").replace("-", "−")
    return f"{text} %"


def grade_color(grade_pct: float) -> QColor:
    return QColor(next(color for limit, color in GRADES if grade_pct < limit))


def route_grade_color(grade_pct: float) -> QColor:
    """Comme le mode libre (vert → rouge en montée), avec les descentes en bleu."""
    return QColor(DESCENT) if grade_pct <= -1 else grade_color(grade_pct)


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
    weight_kg: float | None = None  # poids du cycliste seul (W/kg)
    on_screen: frozenset[str] = frozenset()  # indicateurs affichés sur l'écran (rempli par la grille)
    smoothing_s: int = 0  # puissance de `reading` : instantanée (0) ou moyenne sur n secondes
    hr_max: int | None = None  # FC max du profil (zones de FC ESIE)
    cadence_low: int | None = None  # seuil passé par en dessous depuis 5 s (alerte de cadence basse), sinon None

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
    title: str | None = None  # titre de la carte, s'il diffère de celui de l'indicateur
    color: str | None = None  # couleur de la valeur, si elle dépend de la mesure (zone de la cible, pente)
    tint: str | None = None  # teinte de la carte (alerte de cadence basse)


@dataclass(frozen=True)
class Indicator:
    key: str
    title: str
    show: Callable[[RideView], Shown]
    color: str = TEXT
    modes: frozenset[Mode] = ALL_MODES  # écrans où l'indicateur a un sens
    gauge: str | None = None  # « zones » (puissance), « heart_zones » (FC, ESIE) ou « progress » : barre sous la valeur
    label: str = ""  # nom dans le menu du clic droit (par défaut, le titre en minuscules)
    on_grid: bool = True  # proposé aux cartes des écrans ; sinon, vignette seulement

    @property
    def menu_name(self) -> str:
        return self.label or self.title.capitalize()

    def value_color(self, shown: Shown) -> str:
        return shown.color or self.color


# --- indicateurs --------------------------------------------------------------

def _join(*parts: object) -> str:
    """Morceaux de la ligne de détail ; ceux qui ne sont pas du texte (None, False, 0) sont omis."""
    return " · ".join(p for p in parts if isinstance(p, str) and p)


def _pct_ftp(watts: float, ftp: float) -> str | None:
    return f"{watts / ftp * 100:.0f} % FTP" if ftp else None


def _power(v: RideView) -> Shown:
    r = v.reading
    average = v.session.average_power()
    return Shown(f"{r.power_w:.0f} W" if r else "—",
                 _join(_pct_ftp(r.power_w, v.ftp) if r else None, average is not None and f"moy. {average:.0f} W"),
                 zone=zone_index(r.power_w if r else None, v.ftp),
                 title=f"PUISSANCE · {v.smoothing_s} S" if v.smoothing_s else None)


def _per_kg(watts: float, kg: float) -> str:
    return f"{watts / kg:.1f}".replace(".", ",")


def _watts_per_kg(v: RideView) -> Shown:
    r, kg = v.reading, v.weight_kg
    if not kg:
        return Shown("—", "poids du cycliste : en haut")
    average = v.session.average_power()
    return Shown(f"{_per_kg(r.power_w, kg)} W/kg" if r else "—",
                 _join(average is not None and f"moy. {_per_kg(average, kg)}", f"{kg:.1f} kg".replace(".", ",")))


def _normalized_power(v: RideView) -> Shown:
    np = v.session.stats.normalized_power
    if np is None:
        return Shown("—", "après 30 s")
    return Shown(f"{np:.0f} W", _join(_pct_ftp(np, v.ftp), "puissance normalisée"))


def _max_power(v: RideView) -> Shown:
    top = v.session.stats.power_max
    return Shown("—") if top is None else Shown(f"{top:.0f} W", _pct_ftp(top, v.ftp) or "")


def _cadence(v: RideView) -> Shown:
    r, s = v.reading, v.session
    average = s.stats.average_cadence
    # Vitesse sous la cadence tant qu'elle n'a pas sa carte sur l'écran.
    speed = None if "speed" in v.on_screen or v.speed_kmh is None else f"{kmh_text(v.speed_kmh)} km/h"
    # En mode libre, le braquet ne sert qu'en pente.
    erg = isinstance(s, FreeRideSession) and s.mode is FreeMode.ERG
    gear = f"braquet {v.gear}" if v.gear and not erg else None
    value = f"{r.cadence_rpm:.0f} tr/min" if r and r.cadence_rpm is not None else "—"
    if v.cadence_low is not None:  # alerte : en orange, et dit en premier
        return Shown(value, _join(f"cadence basse (< {v.cadence_low})", average is not None and f"moy. {average:.0f}"),
                     color=ALERT, tint=ALERT)
    return Shown(value, _join(average is not None and f"moy. {average:.0f}", speed, gear))


def _heart(v: RideView) -> Shown:
    heart = v.heart
    if heart is None:
        return Shown("Off", v.heart_off)
    stats = v.session.stats
    average, top = stats.average_heart_rate, stats.heart_rate_max
    battery = heart.battery_pct is not None and heart.battery_pct <= 20 and f"batterie {heart.battery_pct} %"
    zone = esie_zone(heart.bpm, v.hr_max)
    return Shown(f"{heart.bpm} bpm", _join(zone is not None and ESIE_NAMES[zone], average and f"moy. {average:.0f}",
                                          top and f"max {top:.0f}", battery,
                                          heart.contact is False and "mauvais contact"), zone=zone)


def _balance(v: RideView) -> Shown:
    left = v.reading.balance_left_pct if v.reading else None
    average = v.session.stats.average_balance_left_pct
    avg = average is not None and f"moy. {average:.0f} / {100 - average:.0f}"
    if left is None:
        return Shown("—", avg or "capteur double (gauche et droite)")
    return Shown(f"{left:.0f} / {100 - left:.0f}", avg or "", unit="%")


def _brick(v: RideView) -> Shown:
    s = v.session
    assert isinstance(s, WorkoutSession)
    stats = s.stats
    if stats.lap != s.index or stats.lap_power is None:
        return Shown("—", "moyenne de la brique")
    average, target = stats.lap_power, stats.lap_target
    if not target:
        return Shown(f"{average:.0f} W", "moyenne de la brique")
    return Shown(f"{average:.0f} W", f"cible {target:.0f} W · {average / target * 100:.0f} %")


def _tour(v: RideView) -> Shown:
    """Tour en cours, comme dans le .fit : il change avec la brique ou la touche Tab."""
    s = v.session
    stats = s.stats
    if stats.tour_key != s.lap_key or not stats.tour_seconds:  # tour ouvert, pas encore de mesure
        return Shown("0:00", f"tour {stats.tour_number + 1} · Tab : nouveau tour")
    return Shown(hms(stats.tour_seconds), f"tour {stats.tour_number} · moy. {stats.tour_power:.0f} W")


def _elapsed(v: RideView) -> Shown:
    s = v.session
    if isinstance(s, RouteSession):
        return Shown(hms(s.elapsed_s), f"D+ fait {s.climb_done_m:.0f} m")
    if isinstance(s, WorkoutSession):  # en dessous : reste de la brique et de la séance
        if s.state is State.FINISHED:
            return Shown(hms(s.elapsed_s), "séance finie")
        brick = s.step_remaining_s
        return Shown(hms(s.elapsed_s), _join("brique ouverte" if brick is None else f"brique {hms(brick)}",
                                             f"reste {hms(s.total_remaining_s)}"))
    return Shown(hms(s.elapsed_s))


def _workout_remaining(v: RideView) -> Shown:
    s = v.session
    assert isinstance(s, WorkoutSession)
    return Shown(hms(s.total_remaining_s), f"écoulé : {hms(s.elapsed_s)}",
                 progress=s.position_s / s.total_s if s.total_s else 0.0)


def _route_remaining(v: RideView) -> Shown:
    s = v.session
    assert isinstance(s, RouteSession)
    return Shown(km_text(s.remaining_m), f"D+ restant {s.climb_remaining_m:.0f} m · fait {km_text(s.distance_m)}")


def _target(v: RideView) -> Shown:
    """Ce que demande la sortie : cible en W (ou en bpm, séance en FC), ou pente. Couleur de sa zone."""
    s = v.session
    if isinstance(s, RouteSession):
        return Shown(grade_text(s.grade_pct), title="PENTE", color=route_grade_color(s.grade_pct).name())
    if isinstance(s, FreeRideSession):
        if s.mode is FreeMode.ERG:
            return Shown(f"{s.target_w} W", color=zone_color(s.target_w, v.ftp).name())
        return Shown(grade_text(s.grade_pct), title="PENTE", color=grade_color(s.grade_pct).name())
    target, bpm = s.target_w, s.target_bpm
    if s.state is State.FINISHED:
        return Shown("fin", "séance terminée")
    if target is None and bpm is not None:  # séance en FC roulée aux vitesses
        return Shown(f"{bpm:.0f} bpm", title="CIBLE FC", color=HEART)
    if target is None:
        return Shown("libre")
    return Shown(f"{target:.0f} W", color=None if zone_index(target, v.ftp) == 0 else zone_color(target, v.ftp).name())


def _brick_remaining(v: RideView) -> Shown:
    """Temps restant dans la brique en cours (séance finie ou sans brique : reste de la séance)."""
    s = v.session
    assert isinstance(s, WorkoutSession)
    brick = s.step_remaining_s
    return Shown(hms(s.total_remaining_s if brick is None else brick),
                 f"brique {min(s.index + 1, len(s.segments))} / {len(s.segments)}")


def _speed(v: RideView) -> Shown:
    s = v.session
    # Parcours : distance / temps ; ailleurs, moyenne des vitesses enregistrées.
    average = s.average_speed_kmh() if isinstance(s, RouteSession) else s.stats.average_speed_kmh
    top = s.stats.speed_max
    return Shown("—" if v.speed_kmh is None else kmh_text(v.speed_kmh),
                 _join(average and f"moy. {kmh_text(average)}", top and f"max {kmh_text(top)}"), unit="km/h")


CATALOG: dict[str, Indicator] = {i.key: i for i in (
    Indicator("power", "PUISSANCE", _power, gauge="zones"),
    Indicator("watts_per_kg", "W/KG", _watts_per_kg, label="W/kg"),
    Indicator("normalized_power", "NP", _normalized_power, label="NP (puissance normalisée)"),
    Indicator("max_power", "PUISSANCE MAX", _max_power),
    Indicator("brick", "BRIQUE", _brick, modes=frozenset({Mode.WORKOUT}), label="Brique (moyenne et cible)"),
    Indicator("cadence", "CADENCE", _cadence, CADENCE),
    Indicator("heart", "CARDIO", _heart, HEART, gauge="heart_zones"),
    Indicator("speed", "VITESSE", _speed),
    Indicator("balance", "ÉQUILIBRE G / D", _balance, label="Équilibre gauche / droite"),
    Indicator("lap", "TOUR", _tour, label="Tour (touche Tab)"),
    Indicator("elapsed", "TEMPS", _elapsed),
    Indicator("workout_remaining", "RESTE AU TOTAL", _workout_remaining, modes=frozenset({Mode.WORKOUT}),
              gauge="progress"),
    Indicator("route_remaining", "RESTE", _route_remaining, modes=frozenset({Mode.ROUTE})),
    # Vignette seulement : les écrans montrent déjà la cible et la brique dans leur bloc de commande.
    Indicator("target", "CIBLE", _target, label="Cible (ou pente)", on_grid=False),
    Indicator("brick_remaining", "RESTE", _brick_remaining, modes=frozenset({Mode.WORKOUT}),
              label="Reste de la brique", on_grid=False),
)}
