"""Ceinture cardio Bluetooth (Bluetooth Low Energy, service Heart Rate) via `bleak`."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

from .base import BackgroundSensor, SensorState, SensorUnavailable
from .heart_rate import (BLE_BATTERY_LEVEL, BLE_HEART_RATE_MEASUREMENT, BLE_HEART_RATE_SERVICE,
                         HeartRateReading, parse_ble_measurement)


def _bleak():
    try:
        import bleak
    except ImportError as e:
        raise SensorUnavailable("Bluetooth indisponible : installer `pip install -e \".[ble]\"`") from e
    return bleak


@dataclass(frozen=True)
class BleDevice:
    address: str
    name: str
    rssi: int | None = None

    def __str__(self) -> str:
        return f"{self.name}  ({self.address})"


def advertises(adv, service_uuids: str | tuple[str, ...]) -> bool:
    """L'annonce Bluetooth `adv` mentionne-t-elle l'un de ces services ?"""
    wanted = (service_uuids,) if isinstance(service_uuids, str) else service_uuids
    return any(u.lower() in wanted for u in adv.service_uuids)


async def scan_async(service_uuids: str | tuple[str, ...], timeout: float = 5.0) -> list[BleDevice]:
    bleak = _bleak()
    found = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = [BleDevice(d.address, d.name or adv.local_name or "sans nom", adv.rssi)
               for d, adv in found.values() if advertises(adv, service_uuids)]
    return sorted(devices, key=lambda d: -(d.rssi or -999))


def scan_heart_rate_monitors(timeout: float = 5.0) -> list[BleDevice]:
    """Ceintures cardio Bluetooth à portée, la plus proche en premier (bloquant)."""
    return asyncio.run(scan_async(BLE_HEART_RATE_SERVICE, timeout))


class BleHeartRateSensor(BackgroundSensor[HeartRateReading]):
    """Se connecte à `address`, ou au premier capteur cardio Bluetooth trouvé."""

    def __init__(self, address: str | None = None, name: str | None = None) -> None:
        super().__init__()
        self.address = address
        self.name = f"Cardio Bluetooth {name or address or ''}".strip()
        self._battery: int | None = None

    def _run(self, stop: threading.Event) -> None:
        _bleak()
        try:
            asyncio.run(self._session(stop))
        except OSError as e:  # pas de pile Bluetooth (BlueZ, WinRT…) ou adaptateur éteint : on réessaie
            raise RuntimeError(f"Bluetooth inaccessible, adaptateur absent ou éteint ? ({e})") from e

    async def _session(self, stop: threading.Event) -> None:
        bleak = _bleak()
        if self.address:
            device = await bleak.BleakScanner.find_device_by_address(self.address, timeout=10)
        else:
            device = await bleak.BleakScanner.find_device_by_filter(
                lambda _d, adv: advertises(adv, BLE_HEART_RATE_SERVICE), timeout=10)
        if device is None:
            self._set_state(SensorState.SEARCHING, "aucun capteur à portée")
            return
        self.name = f"Cardio Bluetooth {device.name or device.address}"
        disconnected = asyncio.Event()

        def on_measurement(_char, data: bytearray) -> None:
            reading = parse_ble_measurement(data)
            self._publish(HeartRateReading(reading.bpm, reading.rr_ms, self._battery, reading.contact))

        async with bleak.BleakClient(device, disconnected_callback=lambda _c: disconnected.set()) as client:
            try:
                self._battery = (await client.read_gatt_char(BLE_BATTERY_LEVEL))[0]
            except Exception:  # noqa: BLE001 (batterie facultative)
                self._battery = None
            await client.start_notify(BLE_HEART_RATE_MEASUREMENT, on_measurement)
            self._set_state(SensorState.CONNECTED)
            while not stop.is_set() and not disconnected.is_set():
                await asyncio.sleep(0.2)
            if client.is_connected:
                await client.stop_notify(BLE_HEART_RATE_MEASUREMENT)
