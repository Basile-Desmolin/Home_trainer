"""Sources de puissance : ce que l'interface attend d'un home trainer.

Le pilotage réel (`home_trainer.sensors.open_trainer`, Wahoo en Bluetooth
FTMS ou ANT+ FE-C) et `SimulatedTrainer` offrent la même interface. Le
simulateur imite un home trainer en mode ERG : la puissance mesurée rejoint
la consigne en quelques secondes, avec un peu de bruit de pédalage.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Reading:
    power_w: float
    cadence_rpm: float | None = None


class PowerSource(Protocol):
    name: str

    def set_target(self, watts: float | None) -> None:
        """Consigne ERG en watts ; None = résistance libre."""

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
        self._target: float | None = None
        self._power = 0.0
        self._rng = random.Random(seed)

    def set_target(self, watts: float | None) -> None:
        self._target = watts

    def read(self, dt: float) -> Reading:
        goal = self.free_ride_w if self._target is None else self._target
        if dt > 0:
            self._power += (goal - self._power) * (1 - math.exp(-dt / self.response_s))
        power = max(self._power + self._rng.gauss(0, self.noise_w), 0.0) if goal > 0 else 0.0
        cadence = 0.0 if goal <= 0 else 88 + self._rng.gauss(0, 1.5)
        return Reading(round(power), round(cadence))
