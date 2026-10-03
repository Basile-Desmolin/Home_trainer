"""Capteurs : connexion Bluetooth / ANT+ en tâche de fond, et leurs simulateurs.

    sensor = open_heart_rate_sensor("ble")      # ou "ant", "sim"
    sensor.start()
    reading = sensor.latest()                   # HeartRateReading ou None
    sensor.stop()

`BackgroundSensor` (base.py) est le socle commun : le pilote du home trainer
Wahoo pourra s'appuyer dessus de la même façon.
"""

from __future__ import annotations

from .base import BackgroundSensor, SensorState, SensorUnavailable
from .heart_rate import AntHeartRateDecoder, HeartRateReading, parse_ble_measurement
from .simulated import SimulatedHeartRate

HEART_RATE_KINDS = {"sim": "simulé", "ble": "Bluetooth", "ant": "ANT+"}


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


__all__ = ["AntHeartRateDecoder", "BackgroundSensor", "HEART_RATE_KINDS", "HeartRateReading",
           "SensorState", "SensorUnavailable", "SimulatedHeartRate", "open_heart_rate_sensor",
           "parse_ble_measurement"]
