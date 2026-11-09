"""Sources de puissance : ce que l'interface attend d'un home trainer.

Le pilotage réel (`home_trainer.sensors.open_trainer`, Bluetooth FTMS
ou ANT+ FE-C) et `SimulatedTrainer` offrent la même interface. Le
simulateur imite un home trainer en mode ERG : la puissance mesurée rejoint
la consigne en quelques secondes, avec un peu de bruit de pédalage. En pente
(`Slope`), il imite un cycliste qui appuie plus fort quand ça monte : sa
vitesse découle de la puissance, de la pente et du poids.
"""

from __future__ import annotations

import math
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from ..sensors.trainer import DEFAULT_CRR, DEFAULT_CW, Calibration, CalibrationPhase, CalibrationStatus, Slope

G = 9.81


@dataclass(frozen=True)
class Reading:
    power_w: float
    cadence_rpm: float | None = None
    speed_kmh: float | None = None
    balance_left_pct: float | None = None  # part de la jambe gauche (capteur double seulement)


SMOOTHING_CHOICES_S = (0, 3, 5, 10, 30)  # puissance affichée : instantanée (0) ou moyenne sur n secondes


class PowerAverage:
    """Moyenne glissante de la puissance affichée, sur les mesures lues à chaque tic (5 par seconde),
    pas sur l'enregistrement à une mesure par seconde. Elle ne sert qu'à l'affichage : la sortie
    enregistrée (et le .fit) garde la puissance brute. Un tic sans mesure (simulateur en pause,
    capteur muet) vide la moyenne : elle repart des mesures suivantes."""

    def __init__(self, keep_s: float = max(SMOOTHING_CHOICES_S)) -> None:
        self.keep_s = keep_s
        self._now = 0.0
        self._items: deque[tuple[float, float]] = deque()  # (instant, watts)

    def feed(self, dt: float, power_w: float | None) -> None:
        self._now += dt
        if power_w is None:
            self._items.clear()
            return
        self._items.append((self._now, power_w))
        while self._items[0][0] <= self._now - self.keep_s:
            self._items.popleft()

    def average(self, window_s: float) -> float | None:
        """Moyenne des mesures des `window_s` dernières secondes (toutes celles reçues, au début)."""
        kept = [w for t, w in self._items if t > self._now - window_s]
        return sum(kept) / len(kept) if kept else None


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
            gear = target.gear if isinstance(target, Slope) else 1.0
            # On appuie plus fort en montée, et dans un plus grand braquet (même cadence).
            goal = max(60.0, (self.free_ride_w + 20 * grade) * gear)
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
    """Vitesse sur la route à `power_w`, pour cette pente et ce poids (sans vent).

    En descente, on roule même sans pédaler : la pente tire le vélo jusqu'à la
    vitesse où la traînée la compense.
    """
    theta = math.atan(slope.grade_pct / 100)
    weight = slope.total_kg * G * (math.sin(theta) + crr * math.cos(theta))
    power_w = max(power_w, 0.0)

    def need(v: float) -> float:  # puissance pour rouler à v m/s
        return (weight + 0.5 * cw * v * v) * v

    # `need` décroît puis croît en descente (minimum en v0) ; la vitesse cherchée est au-delà.
    lo = math.sqrt(-weight / (1.5 * cw)) if weight < 0 else 0.0
    hi = 40.0
    if need(hi) < power_w:
        return hi * 3.6
    if power_w <= 0 and weight >= 0:
        return 0.0
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if need(mid) < power_w else (lo, mid)
    return lo * 3.6


PUSH_FROM_MS = 1.5  # au démarrage, la poussée est celle de cette vitesse (sinon P / v serait infinie à l'arrêt)
SPEED_STEP_S = 0.05  # pas du calcul : stable même après un gel de l'interface de plusieurs secondes
MAX_SPEED_MS = 40.0


def next_speed_kmh(speed_kmh: float, dt: float, power_w: float, slope: Slope,
                   crr: float = DEFAULT_CRR, cw: float = DEFAULT_CW) -> float:
    """Vitesse sur la route `dt` secondes plus tard, en pédalant à `power_w`.

    Mouvement du vélo (cycliste et vélo, masse `slope.total_kg`) : la poussée P / v, moins le roulement,
    la pente et la traînée de l'air. En roue libre (0 W), le vélo ralentit lentement sur le plat, s'arrête
    vite en montée et accélère en descente jusqu'à la vitesse où la traînée compense la pente. À puissance
    constante, la vitesse rejoint `road_speed_kmh` (même équilibre).
    """
    if dt <= 0:
        return speed_kmh
    mass = slope.total_kg
    theta = math.atan(slope.grade_pct / 100)
    weight = mass * G * (math.sin(theta) + crr * math.cos(theta))
    power_w = max(power_w, 0.0)
    v = max(speed_kmh, 0.0) / 3.6
    steps = max(1, math.ceil(dt / SPEED_STEP_S))
    h = dt / steps
    for _ in range(steps):
        force = power_w / max(v, PUSH_FROM_MS) - weight - 0.5 * cw * v * v
        v = min(max(v + force / mass * h, 0.0), MAX_SPEED_MS)
    return v * 3.6
