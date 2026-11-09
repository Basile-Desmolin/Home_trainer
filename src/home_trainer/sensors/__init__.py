"""Capteurs : connexion Bluetooth / ANT+ en tâche de fond.

    sensor = open_heart_rate_sensor("ble")      # ou "ant"
    sensor.start()
    reading = sensor.latest()                   # HeartRateReading ou None
    sensor.stop()

Le home trainer se pilote de la même façon, avec une consigne en plus :

    trainer = open_trainer("ble")               # ou "ant"
    trainer.start()
    trainer.set_target(200)                     # ERG à 200 W ; None = résistance libre
    reading = trainer.read()                    # TrainerReading ou None
    trainer.stop()

Un capteur de puissance externe (pédales, manivelle) se lit comme le cardio ;
`PowerMatch` règle alors l'ERG du home trainer sur sa mesure :

    meter = open_power_meter("ble")             # ou "ant"

Le Zwift Click change le braquet virtuel (voir `home_trainer.gears`) :

    click = open_zwift_click()                  # Bluetooth seulement
    click.take_shifts()                         # [+1, −1…] : appuis depuis le dernier appel

`BackgroundSensor` (base.py) est le socle commun de tous ces capteurs.
"""

from __future__ import annotations

from .base import BackgroundSensor, SensorState, SensorUnavailable, stop_all
from .heart_rate import AntHeartRateDecoder, HeartRateReading, parse_ble_measurement
from .power_meter import AntPowerDecoder, PowerMatch, PowerMeterReading
from .trainer import Slope, Trainer, TrainerReading

HEART_RATE_KINDS = {"ble": "Bluetooth", "ant": "ANT+"}
TRAINER_KINDS = {"ble": "Bluetooth", "ant": "ANT+"}
POWER_METER_KINDS = {"ble": "Bluetooth", "ant": "ANT+"}


def open_heart_rate_sensor(kind: str, *, address: str | None = None,
                           device_number: int = 0) -> BackgroundSensor[HeartRateReading]:
    """Crée (sans le démarrer) un capteur cardio : "ble" ou "ant"."""
    if kind == "ble":
        from .ble import BleHeartRateSensor
        return BleHeartRateSensor(address)
    if kind == "ant":
        from .ant import AntHeartRateSensor
        return AntHeartRateSensor(device_number)
    raise ValueError(f"capteur cardio inconnu : {kind!r} (ble ou ant)")


def open_power_meter(kind: str, *, address: str | None = None,
                     device_number: int = 0) -> BackgroundSensor[PowerMeterReading]:
    """Crée (sans le démarrer) un capteur de puissance : "ble" ou "ant"."""
    if kind == "ble":
        from .power_meter import BlePowerMeter
        return BlePowerMeter(address)
    if kind == "ant":
        from .power_meter import AntPowerMeter
        return AntPowerMeter(device_number)
    raise ValueError(f"capteur de puissance inconnu : {kind!r} (ble ou ant)")


def open_zwift_click(*, address: str | None = None):
    """Crée (sans le démarrer) la commande de vitesses Zwift Click (Bluetooth)."""
    from .zwift_click import BleZwiftClick
    return BleZwiftClick(address)


def open_trainer(kind: str, *, address: str | None = None, device_number: int = 0) -> Trainer:
    """Crée (sans le démarrer) le pilote du home trainer : "ble" ou "ant"."""
    if kind == "ble":
        from .trainer_ble import BleTrainer
        return BleTrainer(address)
    if kind == "ant":
        from .trainer_ant import AntTrainer
        return AntTrainer(device_number)
    raise ValueError(f"home trainer inconnu : {kind!r} (ble ou ant)")


__all__ = ["AntHeartRateDecoder", "AntPowerDecoder", "BackgroundSensor", "HEART_RATE_KINDS", "HeartRateReading",
           "POWER_METER_KINDS", "PowerMatch", "PowerMeterReading", "SensorState", "SensorUnavailable", "Slope",
           "TRAINER_KINDS", "Trainer", "TrainerReading", "open_heart_rate_sensor", "open_power_meter",
           "open_trainer", "open_zwift_click", "parse_ble_measurement", "stop_all"]
