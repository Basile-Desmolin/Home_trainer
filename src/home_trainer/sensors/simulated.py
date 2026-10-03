"""Cardio simulé, pour essayer l'interface sans ceinture.

La fréquence suit l'effort fourni (la puissance du home trainer) avec
l'inertie d'un vrai cœur : elle monte en une trentaine de secondes, et
redescend doucement vers le repos quand on arrête de pédaler.
"""

from __future__ import annotations

import math
import random
import threading
from collections.abc import Callable

from .base import BackgroundSensor
from .heart_rate import HeartRateReading


class SimulatedHeartRate(BackgroundSensor[HeartRateReading]):
    name = "Cardio simulé"

    def __init__(self, effort_w: Callable[[], float | None] | None = None, ftp: float = 250,
                 rest_bpm: float = 62, threshold_bpm: float = 168, max_bpm: float = 192,
                 response_s: float = 30.0, seed: int | None = None, period_s: float = 1.0) -> None:
        super().__init__()
        self.effort_w = effort_w or (lambda: None)
        self.ftp = ftp
        self.rest_bpm, self.threshold_bpm, self.max_bpm = rest_bpm, threshold_bpm, max_bpm
        self.response_s = response_s
        self.period_s = period_s
        self._bpm = rest_bpm
        self._rng = random.Random(seed)

    def goal_bpm(self, power_w: float | None) -> float:
        """Fréquence vers laquelle tend le cœur à cette puissance (seuil à la FTP)."""
        if not power_w:
            return self.rest_bpm
        bpm = self.rest_bpm + (self.threshold_bpm - self.rest_bpm) * power_w / self.ftp
        return min(bpm, self.max_bpm)

    def step(self, dt: float) -> HeartRateReading:
        goal = self.goal_bpm(self.effort_w())
        self._bpm += (goal - self._bpm) * (1 - math.exp(-dt / self.response_s))
        bpm = round(self._bpm + self._rng.gauss(0, 0.6))
        reading = HeartRateReading(bpm, (round(60_000 / bpm, 1),), battery_pct=100, contact=True)
        self._publish(reading)
        return reading

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(self.period_s):
            self.step(self.period_s)
