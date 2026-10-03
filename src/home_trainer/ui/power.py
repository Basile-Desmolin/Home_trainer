"""Sources de puissance : ce que l'interface attend d'un home trainer.

Le pilotage réel (`home_trainer.sensors.open_trainer`, Wahoo en Bluetooth
FTMS ou ANT+ FE-C) et `SimulatedTrainer` offrent la même interface. Le
simulateur imite un home trainer en mode ERG : la puissance mesurée rejoint
la consigne en quelques secondes, avec un peu de bruit de pédalage. En pente
(`Slope`), il imite un cycliste qui appuie plus fort quand ça monte : sa
vitesse découle de la puissance, de la pente et du poids.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Protocol

from ..sensors.trainer import DEFAULT_CRR, DEFAULT_CW, Calibration, CalibrationPhase, CalibrationStatus, Slope

G = 9.81


@dataclass(frozen=True)
class Reading:
    power_w: float
    cadence_rpm: float | None = None
    speed_kmh: float | None = None


class PowerSource(Protocol):
    name: str

    def set_target(self, watts: float | Slope | None) -> None:
        """Consigne ERG en watts, pente (`Slope`, mode simulation) ; None = résistance libre."""

    def read(self, dt: float) -> Reading | None:
        """Dernière mesure, `dt` secondes après la précédente (None : home trainer muet)."""


def open_power_source(kind: str, *, address: str | None = None, device_number: int = 0) -> PowerSource:
    """Home trainer simulé ("sim"), Bluetooth ("ble") ou ANT+ ("ant"), pas encore démarré."""
    if kind == "sim":
        return SimulatedTrainer()
    from ..sensors import open_trainer
    return open_trainer(kind, address=address, device_number=device_number)


class SimulatedTrainer:
    name = "Home trainer simulé"

    def __init__(self, response_s: float = 2.5, noise_w: float = 4.0,
                 free_ride_w: float = 120.0, seed: int | None = None) -> None:
        self.response_s = response_s  # constante de temps de la régulation ERG
        self.noise_w = noise_w
        self.free_ride_w = free_ride_w  # puissance « naturelle » sans consigne
        self._target: float | Slope | None = None
        self._power = 0.0
        self._rng = random.Random(seed)
        self.calibration = Calibration()
        self._cal_speed = 0.0
        self._cal_at = 0.0

    # --- calibration imitée : on accélère à 5 km/h par seconde, la roue libre s'arrête en ~10 s.

    def start_calibration(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.calibration.request()
        self.calibration.begin("simulé", now)
        self._cal_speed, self._cal_at = 0.0, now

    def cancel_calibration(self) -> None:
        self.calibration.cancel()

    def calibration_status(self, now: float | None = None) -> CalibrationStatus:
        now = time.monotonic() if now is None else now
        cal = self.calibration
        if cal.active:
            dt, self._cal_at = now - self._cal_at, now
            if cal.status.phase is CalibrationPhase.SPEED_UP:
                self._cal_speed += 5.0 * dt
            else:
                self._cal_speed = max(0.0, self._cal_speed - 3.0 * dt)
            cal.on_speed(round(self._cal_speed, 1), now)
            cal.tick(now)
        return cal.status

    def set_target(self, watts: float | Slope | None) -> None:
        self._target = watts

    def read(self, dt: float) -> Reading:
        target = self._target
        grade = target.grade_pct if isinstance(target, Slope) else 0.0
        if target is None or isinstance(target, Slope):
            goal = max(60.0, self.free_ride_w + 20 * grade)  # on appuie plus fort en montée
        else:
            goal = target
        if dt > 0:
            self._power += (goal - self._power) * (1 - math.exp(-dt / self.response_s))
        power = max(self._power + self._rng.gauss(0, self.noise_w), 0.0) if goal > 0 else 0.0
        cadence = 0.0 if goal <= 0 else 88 - 1.5 * max(grade, 0) + self._rng.gauss(0, 1.5)
        speed = None
        if isinstance(target, Slope):
            speed = round(road_speed_kmh(self._power, target), 1)
        return Reading(round(power), round(cadence), speed)


def road_speed_kmh(power_w: float, slope: Slope, crr: float = DEFAULT_CRR, cw: float = DEFAULT_CW) -> float:
    """Vitesse sur la route à `power_w`, pour cette pente et ce poids (sans vent)."""
    theta = math.atan(slope.grade_pct / 100)
    weight = slope.total_kg * G * (math.sin(theta) + crr * math.cos(theta))

    def need(v: float) -> float:  # puissance pour rouler à v m/s
        return (weight + 0.5 * cw * v * v) * v

    lo, hi = 0.0, 40.0
    if power_w <= 0 or need(hi) < power_w:
        return 0.0 if power_w <= 0 else hi * 3.6
    for _ in range(50):  # `need` croît avec v (en descente aussi, dès que la vitesse est positive)
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if need(mid) < power_w else (lo, mid)
    return lo * 3.6
