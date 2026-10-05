"""Home trainer Bluetooth (FTMS : Wahoo, Elite, Tacx, Saris…) via `bleak`.

Le protocole standard FTMS est utilisé quand le home trainer le propose ;
sinon, on se rabat sur le protocole propriétaire Wahoo des anciens
firmwares (puissance par le service Cycling Power).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from .base import SensorState
from .ble import BleDevice, _bleak, advertises, scan_async
from .trainer import (BLE_CYCLING_POWER_MEASUREMENT, BLE_FTMS_CONTROL_POINT, BLE_FTMS_SERVICE, BLE_FTMS_STATUS,
                      BLE_INDOOR_BIKE_DATA, BLE_TRAINER_SERVICES, BLE_WAHOO_CONTROL_POINT, BLE_WAHOO_SERVICE,
                      FTMS_REQUEST_CONTROL, FTMS_SPIN_DOWN_CONTROL, CyclingPowerDecoder, Trainer, TargetThrottle,
                      WAHOO_UNLOCK, ftms_command_for, ftms_request_control, ftms_spin_down, ftms_start,
                      parse_ftms_response, parse_indoor_bike_data, wahoo_commands_for, wahoo_init_spindown)

log = logging.getLogger(__name__)


def scan_trainers(timeout: float = 5.0) -> list[BleDevice]:
    """Home trainers Bluetooth à portée, le plus proche en premier (bloquant)."""
    return asyncio.run(scan_async(BLE_TRAINER_SERVICES, timeout))


class BleTrainer(Trainer):
    """Se connecte à `address`, ou au premier home trainer Bluetooth trouvé."""

    device_kind = "ble"

    def __init__(self, address: str | None = None, name: str | None = None) -> None:
        super().__init__(TargetThrottle(min_interval_s=1.0))
        self.address = address
        self.device_id = address
        self.name = name or f"Home trainer Bluetooth {address or ''}".strip()
        self.protocol: str | None = None  # "FTMS" ou "Wahoo" une fois connecté

    def _run(self, stop: threading.Event) -> None:
        _bleak()
        try:
            asyncio.run(self._session(stop))
        except OSError as e:  # pas de pile Bluetooth ou adaptateur éteint : on réessaie
            raise RuntimeError(f"Bluetooth inaccessible, adaptateur absent ou éteint ? ({e})") from e

    async def _session(self, stop: threading.Event) -> None:
        bleak = _bleak()
        if self.address:
            device = await bleak.BleakScanner.find_device_by_address(self.address, timeout=10)
        else:
            device = await bleak.BleakScanner.find_device_by_filter(
                lambda _d, adv: advertises(adv, BLE_TRAINER_SERVICES), timeout=10)
        if device is None:
            self._set_state(SensorState.SEARCHING, "aucun home trainer à portée, pédalez pour le réveiller")
            return
        self.name = device.name or f"Home trainer Bluetooth {device.address}"
        self.device_id = device.address
        disconnected = asyncio.Event()
        async with bleak.BleakClient(device, disconnected_callback=lambda _c: disconnected.set()) as client:
            self.throttle.reset()
            if client.services.get_service(BLE_FTMS_SERVICE) is not None:
                self.protocol = "FTMS"
                wahoo = client.services.get_service(BLE_WAHOO_SERVICE) is not None
                await self._ftms(client, stop, disconnected, wahoo)
            elif client.services.get_service(BLE_WAHOO_SERVICE) is not None:
                self.protocol = "Wahoo"
                await self._wahoo(client, stop, disconnected)
            else:
                raise RuntimeError(f"{device.name or device.address} ne se pilote ni en FTMS ni en protocole Wahoo")

    async def _ftms(self, client, stop: threading.Event, disconnected: asyncio.Event, wahoo: bool = False) -> None:
        """`wahoo` : le protocole Wahoo est aussi proposé (KICKR), en secours pour la calibration."""
        loop = asyncio.get_running_loop()
        control_granted: asyncio.Future = loop.create_future()
        ftms_spindown = True  # passe à False si le home trainer ne calibre pas en FTMS

        def on_control_point(_char, data: bytearray) -> None:
            nonlocal ftms_spindown
            response = parse_ftms_response(data)
            if response is None:
                return
            if response.request == FTMS_REQUEST_CONTROL and not control_granted.done():
                control_granted.set_result(response)
            elif response.request == FTMS_SPIN_DOWN_CONTROL:
                if response.result == 0x02 and wahoo and self.calibration.active:
                    ftms_spindown = False
                    self.calibration.request()  # on retente en protocole Wahoo
                else:
                    self.calibration.on_ftms_response(response)
            elif not response.ok:
                log.warning("%s : commande %#04x refusée (%s)", self.name, response.request, response.message)
                self._set_state(self.state, f"consigne refusée : {response.message}")

        await client.start_notify(BLE_FTMS_CONTROL_POINT, on_control_point)
        await client.start_notify(BLE_INDOOR_BIKE_DATA,
                                  lambda _c, data: self._publish(parse_indoor_bike_data(data)))
        try:  # état de la calibration ; facultatif dans la norme
            await client.start_notify(BLE_FTMS_STATUS, lambda _c, data: self.calibration.on_ftms_status(data))
        except Exception:  # noqa: BLE001
            log.debug("%s : pas de notification Fitness Machine Status", self.name)
        await client.write_gatt_char(BLE_FTMS_CONTROL_POINT, ftms_request_control(), response=True)
        try:
            granted = await asyncio.wait_for(control_granted, timeout=5)
        except asyncio.TimeoutError:
            raise RuntimeError("le home trainer ne répond pas à la demande de contrôle FTMS") from None
        if not granted.ok:
            raise RuntimeError(f"pilotage refusé : {granted.message}")
        await client.write_gatt_char(BLE_FTMS_CONTROL_POINT, ftms_start(), response=True)
        self._set_state(SensorState.CONNECTED, "FTMS")

        async def send(target: float | None) -> None:
            await client.write_gatt_char(BLE_FTMS_CONTROL_POINT, ftms_command_for(target), response=True)

        unlocked = False

        async def calibrate() -> None:
            nonlocal unlocked
            await send(None)
            if not ftms_spindown:
                if not unlocked:
                    await client.write_gatt_char(BLE_WAHOO_CONTROL_POINT, WAHOO_UNLOCK, response=True)
                    unlocked = True
                await client.write_gatt_char(BLE_WAHOO_CONTROL_POINT, wahoo_init_spindown(), response=True)
                self.calibration.begin("Wahoo", time.monotonic())
                return
            self.calibration.begin("FTMS", time.monotonic())
            await client.write_gatt_char(BLE_FTMS_CONTROL_POINT, ftms_spin_down(), response=True)

        await self._control_loop(send, stop, disconnected, calibrate)

    async def _wahoo(self, client, stop: threading.Event, disconnected: asyncio.Event) -> None:
        decoder = CyclingPowerDecoder()
        await client.start_notify(BLE_CYCLING_POWER_MEASUREMENT, lambda _c, data: self._publish(decoder.feed(data)))
        try:
            await client.start_notify(BLE_WAHOO_CONTROL_POINT, lambda _c, _data: None)  # accusés de réception
        except Exception:  # noqa: BLE001 (certains firmwares n'indiquent rien)
            pass
        await client.write_gatt_char(BLE_WAHOO_CONTROL_POINT, WAHOO_UNLOCK, response=True)
        self._set_state(SensorState.CONNECTED, "protocole Wahoo")

        async def send(target: float | None) -> None:
            for command in wahoo_commands_for(target):
                await client.write_gatt_char(BLE_WAHOO_CONTROL_POINT, command, response=True)

        async def calibrate() -> None:
            await send(None)
            await client.write_gatt_char(BLE_WAHOO_CONTROL_POINT, wahoo_init_spindown(), response=True)
            self.calibration.begin("Wahoo", time.monotonic())

        await self._control_loop(send, stop, disconnected, calibrate)

    async def _control_loop(self, send, stop: threading.Event, disconnected: asyncio.Event, calibrate) -> None:
        """Transmet les consignes (ou lance une calibration) jusqu'à l'arrêt ou la déconnexion."""
        while not stop.is_set() and not disconnected.is_set():
            if self._calibration_due():
                try:
                    await calibrate()
                except Exception as e:  # noqa: BLE001 (commande refusée par le home trainer)
                    self.calibration.fail(f"calibration impossible : {e}")
            due, target = self._pending_target()
            if due:
                await send(target)
                self._target_sent(target)
            await asyncio.sleep(0.2)
        self.calibration.cancel()
        if disconnected.is_set():
            raise RuntimeError("home trainer déconnecté")
        try:  # on rend une résistance libre plutôt que de laisser la dernière consigne ERG
            await send(None)
        except Exception:  # noqa: BLE001 (déconnexion en cours)
            pass
