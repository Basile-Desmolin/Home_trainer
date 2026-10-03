"""Capteurs : connexion Bluetooth / ANT+ en tâche de fond, et leurs simulateurs.

    sensor = open_heart_rate_sensor("ble")      # ou "ant", "sim"
    sensor.start()
    reading = sensor.latest()                   # HeartRateReading ou None
    sensor.stop()

Le home trainer Wahoo se pilote de la même façon, avec une consigne en plus :

    trainer = open_trainer("ble")               # ou "ant"
    trainer.start()
    trainer.set_target(200)                     # ERG à 200 W ; None = résistance libre
    reading = trainer.read()                    # TrainerReading ou None
    trainer.stop()

`BackgroundSensor` (base.py) est le socle commun de tous ces capteurs.
"""

from __future__ import annotations

from .base import BackgroundSensor, SensorState, SensorUnavailable
from .heart_rate import AntHeartRateDecoder, HeartRateReading, parse_ble_measurement
from .simulated import SimulatedHeartRate
from .trainer import Slope, Trainer, TrainerReading

HEART_RATE_KINDS = {"sim": "simulé", "ble": "Bluetooth", "ant": "ANT+"}
TRAINER_KINDS = {"ble": "Bluetooth", "ant": "ANT+"}


def open_heart_rate_sensor(kind: str, *, address: str | None = None, device_number: int = 0,
                           **simulated) -> BackgroundSensor[HeartRateReading]:
    """Crée (sans le démarrer) un capteur cardio : "sim", "ble" ou "ant"."""
    if kind == "sim":
        return SimulatedHeartRate(**simulated)
    if kind == "ble":
        from .ble import BleHeartRateSensor
        return BleHeartRateSensor(address)
    if kind == "ant":
        from .ant import AntHeartRateSensor
        return AntHeartRateSensor(device_number)
    raise ValueError(f"capteur cardio inconnu : {kind!r} (sim, ble ou ant)")


def open_trainer(kind: str, *, address: str | None = None, device_number: int = 0) -> Trainer:
    """Crée (sans le démarrer) le pilote du home trainer : "ble" ou "ant"."""
    if kind == "ble":
        from .trainer_ble import BleTrainer
        return BleTrainer(address)
    if kind == "ant":
        from .trainer_ant import AntTrainer
        return AntTrainer(device_number)
    raise ValueError(f"home trainer inconnu : {kind!r} (ble ou ant)")


__all__ = ["AntHeartRateDecoder", "BackgroundSensor", "HEART_RATE_KINDS", "HeartRateReading",
           "SensorState", "SensorUnavailable", "SimulatedHeartRate", "Slope", "TRAINER_KINDS", "Trainer",
           "TrainerReading", "open_heart_rate_sensor", "open_trainer", "parse_ble_measurement"]
