"""Déroulé d'une séance en cours, indépendant de l'interface graphique.

`WorkoutSession` avance l'horloge (`tick`), sait sur quelle brique on se
trouve, combien de temps il reste, et calcule la consigne ERG à envoyer au
home trainer. L'intensité (`intensity_pct`, 100 % par défaut) multiplie
toutes les consignes : c'est le réglage « +1 % / −1 % » de l'interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..workout import Segment, Workout

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


class WorkoutSession:
    def __init__(self, workout: Workout, ftp: float | None = None) -> None:
        self.workout = workout
        self.ftp = ftp
        self.segments: list[Segment] = workout.timeline(ftp)
        self.elapsed_s = 0.0  # temps écoulé depuis le début (pauses exclues)
        self.index = 0  # brique en cours
        self.step_elapsed_s = 0.0  # temps écoulé dans la brique en cours
        self.intensity_pct = 100
        self.state = State.READY if self.segments else State.FINISHED
        self.samples: list[Sample] = []

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
               heart_rate_bpm: float | None = None) -> None:
        self.samples.append(Sample(self.elapsed_s, power_w, self.target_w, cadence_rpm, heart_rate_bpm))

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

    def _finish(self) -> None:
        self.index = len(self.segments)
        self.step_elapsed_s = 0.0
        self.state = State.FINISHED
