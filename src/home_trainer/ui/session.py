"""Déroulé d'une séance en cours, indépendant de l'interface graphique.

`WorkoutSession` avance l'horloge (`tick`), sait sur quelle brique on se
trouve, combien de temps il reste, et calcule la consigne ERG à envoyer au
home trainer. L'intensité (`intensity_pct`, 100 % par défaut) multiplie
toutes les consignes : c'est le réglage « +1 % / −1 % » de l'interface.
ERG désactivé (`erg = False`), le home trainer passe en résistance libre
(route plate) : la séance continue de dérouler et la cible s'affiche, à
suivre à la main avec les vitesses.
`FreeRideSession` est le mode libre : pas de séance, consigne réglée à la main
(puissance ERG, ou pente simulée qui tient compte du poids).
`RouteSession` roule un parcours GPX : la pente envoyée suit la route, à la
distance parcourue d'après la puissance pédalée.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum

from ..formats.fit_activity import ActivityPoint
from ..route import Route
from ..sensors.trainer import DEFAULT_BIKE_KG, DEFAULT_WEIGHT_KG, Slope
from ..workout import Segment, Workout
from .power import road_speed_kmh

DEFAULT_RIDER_KG = DEFAULT_WEIGHT_KG - DEFAULT_BIKE_KG
MIN_INTENSITY = 1
MAX_INTENSITY = 200


class State(str, Enum):
    READY = "prête"
    RUNNING = "en cours"
    PAUSED = "en pause"
    FINISHED = "terminée"


@dataclass(frozen=True)
class Sample:
    """Une mesure enregistrée pendant la séance."""

    t: float  # secondes depuis le début de la séance
    power_w: float
    target_w: float | None
    cadence_rpm: float | None = None
    heart_rate_bpm: float | None = None
    grade_pct: float | None = None  # pente simulée (mode libre en pente)
    speed_kmh: float | None = None
    at: float | None = None  # heure de la mesure (secondes depuis 1970), pour le .fit de la sortie
    lap: int = 0  # brique de la séance : un tour par brique dans le .fit


class WorkoutSession:
    def __init__(self, workout: Workout, ftp: float | None = None, rider_kg: float = DEFAULT_RIDER_KG) -> None:
        self.workout = workout
        self.ftp = ftp
        self.segments: list[Segment] = workout.timeline(ftp)
        self.elapsed_s = 0.0  # temps écoulé depuis le début (pauses exclues)
        self.index = 0  # brique en cours
        self.step_elapsed_s = 0.0  # temps écoulé dans la brique en cours
        self.intensity_pct = 100
        self.erg = True  # le home trainer impose la cible ; sinon résistance libre, cible suivie à la main
        self.rider_kg = rider_kg  # poids pour la résistance libre
        self.state = State.READY if self.segments else State.FINISHED
        self.samples: list[Sample] = []
        self.exported = False  # sortie déjà enregistrée en .fit (et envoyée)

    # --- commandes -------------------------------------------------------

    def start(self) -> None:
        if self.state in (State.READY, State.PAUSED):
            self.state = State.RUNNING

    def pause(self) -> None:
        if self.state is State.RUNNING:
            self.state = State.PAUSED

    def toggle(self) -> None:
        self.pause() if self.state is State.RUNNING else self.start()

    def adjust_intensity(self, delta_pct: int) -> int:
        self.intensity_pct = max(MIN_INTENSITY, min(MAX_INTENSITY, self.intensity_pct + delta_pct))
        return self.intensity_pct

    def toggle_erg(self) -> bool:
        self.erg = not self.erg
        return self.erg

    def next_step(self) -> None:
        """Passe à la brique suivante (« tour »), indispensable pour une brique ouverte."""
        if self.state is State.FINISHED:
            return
        self.index += 1
        self.step_elapsed_s = 0.0
        if self.index >= len(self.segments):
            self._finish()

    def tick(self, dt: float) -> None:
        """Fait avancer la séance de `dt` secondes (sans effet hors « en cours »)."""
        if self.state is not State.RUNNING:
            return
        while dt > 0 and self.state is State.RUNNING:
            remaining = self.step_remaining_s
            if remaining is None or dt < remaining:
                self.step_elapsed_s += dt
                self.elapsed_s += dt
                return
            self.elapsed_s += remaining
            dt -= remaining
            self.next_step()

    def record(self, power_w: float, cadence_rpm: float | None = None,
               heart_rate_bpm: float | None = None, speed_kmh: float | None = None,
               at: float | None = None) -> None:
        self.samples.append(Sample(self.elapsed_s, power_w, self.target_w, cadence_rpm, heart_rate_bpm,
                                   None, speed_kmh, time.time() if at is None else at, self.index))

    # --- lecture ---------------------------------------------------------

    @property
    def segment(self) -> Segment | None:
        return self.segments[self.index] if self.index < len(self.segments) else None

    @property
    def next_segment(self) -> Segment | None:
        i = self.index + 1
        return self.segments[i] if i < len(self.segments) else None

    @property
    def step_remaining_s(self) -> float | None:
        seg = self.segment
        if seg is None:
            return 0.0
        if seg.duration_s is None:
            return None
        return max(seg.duration_s - self.step_elapsed_s, 0.0)

    def average_heart_rate(self) -> float | None:
        values = [x.heart_rate_bpm for x in self.samples if x.heart_rate_bpm]
        return sum(values) / len(values) if values else None

    @property
    def total_s(self) -> float:
        return self.workout.total_duration_s

    @property
    def position_s(self) -> float:
        """Position sur l'axe du temps de la séance (les briques ouvertes n'y occupent pas de place)."""
        seg = self.segment
        if seg is None:
            return self.total_s
        return seg.start_s + min(self.step_elapsed_s, seg.duration_s or 0.0)

    @property
    def total_remaining_s(self) -> float:
        return max(self.total_s - self.position_s, 0.0)

    @property
    def base_target_w(self) -> float | None:
        """Consigne de la séance, avant réglage d'intensité."""
        seg = self.segment
        return None if seg is None else seg.target_w(self.step_elapsed_s)

    @property
    def target_w(self) -> float | None:
        base = self.base_target_w
        return None if base is None else base * self.intensity_pct / 100

    @property
    def command(self) -> float | Slope | None:
        """Consigne pour le home trainer : la cible en ERG, sinon une route plate (résistance libre)."""
        return self.target_w if self.erg else Slope(0.0, self.rider_kg)

    def _finish(self) -> None:
        self.index = len(self.segments)
        self.step_elapsed_s = 0.0
        self.state = State.FINISHED


MIN_FREE_TARGET_W = 25
MAX_FREE_TARGET_W = 1500
FREE_STEP_W = 5
GRADE_STEP_PCT = 0.5
MIN_GRADE_PCT = -10.0
MAX_GRADE_PCT = 20.0


class FreeMode(str, Enum):
    ERG = "ERG"  # puissance imposée, quelle que soit la vitesse
    SLOPE = "pente"  # résistance d'une route en pente, selon le poids et la vitesse


def _snap(watts: float) -> int:
    """Consigne du mode libre : multiple de 5 W, dans les bornes."""
    w = round(watts / FREE_STEP_W) * FREE_STEP_W
    return int(max(MIN_FREE_TARGET_W, min(MAX_FREE_TARGET_W, w)))


class FreeRideSession:
    """Mode libre : pas de séance, on règle la consigne à la main, en direct.

    En ERG, une puissance (`target_w`, par pas de 5 W) ; en pente, une pente
    (`grade_pct`, par pas de 0,5 %) que le home trainer simule avec le poids
    du cycliste (`rider_kg`). `command` est la consigne à lui envoyer.
    Même interface de déroulé que `WorkoutSession` (état, `tick`, `record`,
    `samples`) pour que la fenêtre les pilote de la même façon.
    """

    def __init__(self, ftp: float | None = None, target_w: float | None = None,
                 rider_kg: float = DEFAULT_RIDER_KG, mode: FreeMode = FreeMode.ERG,
                 grade_pct: float = 0.0) -> None:
        self.ftp = ftp
        self.target_w: int = _snap(target_w if target_w is not None else (ftp or 250) * 0.6)
        self.rider_kg = rider_kg
        self.mode = mode
        self.grade_pct = 0.0
        self.set_grade(grade_pct)
        self.state = State.READY
        self.elapsed_s = 0.0
        self.samples: list[Sample] = []
        self.exported = False

    def start(self) -> None:
        if self.state in (State.READY, State.PAUSED):
            self.state = State.RUNNING

    def pause(self) -> None:
        if self.state is State.RUNNING:
            self.state = State.PAUSED

    def toggle(self) -> None:
        self.pause() if self.state is State.RUNNING else self.start()

    def set_target(self, watts: float) -> int:
        self.target_w = _snap(watts)
        return self.target_w

    def adjust_target(self, delta_w: float) -> int:
        return self.set_target(self.target_w + delta_w)

    def set_grade(self, grade_pct: float) -> float:
        g = round(grade_pct / GRADE_STEP_PCT) * GRADE_STEP_PCT
        self.grade_pct = max(MIN_GRADE_PCT, min(MAX_GRADE_PCT, g)) + 0.0  # pas de « −0,0 % »
        return self.grade_pct

    def adjust_grade(self, delta_pct: float) -> float:
        return self.set_grade(self.grade_pct + delta_pct)

    @property
    def command(self) -> float | Slope:
        """Consigne pour le home trainer : des watts en ERG, une `Slope` en pente."""
        return self.target_w if self.mode is FreeMode.ERG else Slope(self.grade_pct, self.rider_kg)

    def tick(self, dt: float) -> None:
        if self.state is State.RUNNING:
            self.elapsed_s += dt

    def record(self, power_w: float, cadence_rpm: float | None = None,
               heart_rate_bpm: float | None = None, speed_kmh: float | None = None,
               at: float | None = None) -> None:
        erg = self.mode is FreeMode.ERG
        self.samples.append(Sample(self.elapsed_s, power_w, self.target_w if erg else None, cadence_rpm,
                                   heart_rate_bpm, None if erg else self.grade_pct, speed_kmh,
                                   time.time() if at is None else at))

    @property
    def target_pct(self) -> float | None:
        return self.target_w / self.ftp * 100 if self.ftp else None

    def average_power(self) -> float | None:
        return sum(x.power_w for x in self.samples) / len(self.samples) if self.samples else None

    def average_heart_rate(self) -> float | None:
        values = [x.heart_rate_bpm for x in self.samples if x.heart_rate_bpm]
        return sum(values) / len(values) if values else None


MIN_DIFFICULTY = 0
MAX_DIFFICULTY = 100
DIFFICULTY_STEP = 10
SPEED_RESPONSE_S = 4.0  # inertie du vélo : la vitesse rejoint celle permise par la puissance


class RouteSession:
    """Parcours GPX : le home trainer simule la pente de la route là où l'on se trouve.

    La distance avance selon la vitesse qu'aurait le vélo sur la route
    (puissance, pente réelle, poids), comme dans les applis de route virtuelle :
    la même pour tous les home trainers. La difficulté (100 % par défaut)
    adoucit la pente envoyée au home trainer, pas la vitesse.
    """

    def __init__(self, route: Route, ftp: float | None = None, rider_kg: float = DEFAULT_RIDER_KG,
                 difficulty_pct: int = 100) -> None:
        self.route = route
        self.ftp = ftp
        self.rider_kg = rider_kg
        self.difficulty_pct = difficulty_pct
        self.state = State.READY
        self.elapsed_s = 0.0
        self.distance_m = 0.0
        self.speed_kmh = 0.0
        self.samples: list[Sample] = []
        self.exported = False

    def start(self) -> None:
        if self.state in (State.READY, State.PAUSED):
            self.state = State.RUNNING

    def pause(self) -> None:
        if self.state is State.RUNNING:
            self.state = State.PAUSED
            self.speed_kmh = 0.0

    def toggle(self) -> None:
        self.pause() if self.state is State.RUNNING else self.start()

    def adjust_difficulty(self, delta_pct: int) -> int:
        self.difficulty_pct = max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, self.difficulty_pct + delta_pct))
        return self.difficulty_pct

    def tick(self, dt: float) -> None:
        if self.state is State.RUNNING:
            self.elapsed_s += dt

    def ride(self, dt: float, power_w: float) -> None:
        """Avance de `dt` secondes en pédalant à `power_w` (sans effet hors « en cours »)."""
        if self.state is not State.RUNNING or dt <= 0:
            return
        goal = road_speed_kmh(power_w, Slope(self.grade_pct, self.rider_kg))
        self.speed_kmh += (goal - self.speed_kmh) * (1 - math.exp(-dt / SPEED_RESPONSE_S))
        self.distance_m = min(self.distance_m + self.speed_kmh / 3.6 * dt, self.route.total_m)
        if self.distance_m >= self.route.total_m:
            self.state = State.FINISHED
            self.speed_kmh = 0.0

    def record(self, power_w: float, cadence_rpm: float | None = None,
               heart_rate_bpm: float | None = None, speed_kmh: float | None = None,
               at: float | None = None) -> None:
        self.samples.append(Sample(self.elapsed_s, power_w, None, cadence_rpm, heart_rate_bpm,
                                   self.trainer_grade_pct, self.speed_kmh if speed_kmh is None else speed_kmh,
                                   time.time() if at is None else at))

    # --- lecture ---------------------------------------------------------

    @property
    def grade_pct(self) -> float:
        """Pente de la route ici (lissée sur 100 m), au dixième."""
        return round(self.route.grade_at(self.distance_m), 1) + 0.0

    @property
    def trainer_grade_pct(self) -> float:
        """Pente envoyée au home trainer : celle de la route × difficulté, dans ses bornes."""
        g = round(self.grade_pct * self.difficulty_pct / 100, 1)
        return max(MIN_GRADE_PCT, min(MAX_GRADE_PCT, g)) + 0.0

    @property
    def command(self) -> Slope:
        return Slope(self.trainer_grade_pct, self.rider_kg)

    @property
    def remaining_m(self) -> float:
        return max(self.route.total_m - self.distance_m, 0.0)

    @property
    def climb_done_m(self) -> float:
        return self.route.climb_between(0.0, self.distance_m)

    @property
    def climb_remaining_m(self) -> float:
        return self.route.climb_between(self.distance_m, self.route.total_m)

    @property
    def elevation_m(self) -> float:
        return self.route.elevation_at(self.distance_m)

    def average_power(self) -> float | None:
        return sum(x.power_w for x in self.samples) / len(self.samples) if self.samples else None

    def average_heart_rate(self) -> float | None:
        values = [x.heart_rate_bpm for x in self.samples if x.heart_rate_bpm]
        return sum(values) / len(values) if values else None

    def average_speed_kmh(self) -> float | None:
        return self.distance_m / self.elapsed_s * 3.6 if self.elapsed_s > 0 else None


# --- sortie enregistrée ------------------------------------------------------

MIN_RIDE_SAMPLES = 60  # moins d'une minute de pédalage : rien à enregistrer


def ride_points(samples: list[Sample]) -> list[ActivityPoint]:
    """Mesures de la séance ou du mode libre, prêtes pour le .fit d'activité."""
    return [ActivityPoint(x.at, x.power_w, x.cadence_rpm, x.heart_rate_bpm, x.speed_kmh, x.grade_pct, x.lap,
                          x.t) for x in samples if x.at is not None]


def ride_title(session: WorkoutSession | FreeRideSession | RouteSession) -> tuple[str, str]:
    """Titre et description de la sortie pour Strava / Nolio."""
    if isinstance(session, RouteSession):
        title = session.route.name or "Parcours"
        done = session.state is State.FINISHED
        text = f"Parcours « {title} » sur home trainer" + ("" if done else " (arrêté avant l'arrivée)")
        if session.difficulty_pct != 100:
            text += f", difficulté {session.difficulty_pct} %"
        return title, text + "."
    if isinstance(session, WorkoutSession):
        title = session.workout.name or "Séance"
        done = session.state is State.FINISHED
        text = f"« {title} » sur home trainer" + ("" if done else " (arrêtée avant la fin)")
        if session.intensity_pct != 100:
            text += f", intensité {session.intensity_pct} %"
        if session.ftp:
            text += f", FTP {session.ftp:.0f} W"
        return title, text + "."
    slope = any(x.grade_pct is not None for x in session.samples)
    erg = any(x.grade_pct is None for x in session.samples)
    kind = "ERG et pente simulée" if slope and erg else "pente simulée" if slope else "ERG"
    return "Mode libre", f"Mode libre sur home trainer ({kind})."
