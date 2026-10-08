"""Capteur de puissance externe (pédales, manivelle, moyeu) et réglage de l'ERG sur sa mesure.

    meter = open_power_meter("ble")             # ou "ant"
    meter.start()
    reading = meter.latest()                    # PowerMeterReading ou None

Les watts du capteur remplacent ceux du home trainer à l'écran et dans la
sortie enregistrée. Pour que l'ERG tienne la cible *selon le capteur*,
`PowerMatch` mesure l'écart entre les deux (le home trainer lit souvent
quelques watts de plus ou de moins) et corrige d'autant la consigne envoyée
au home trainer.

- Bluetooth : service Cycling Power (0x1818), caractéristique 0x2A63, norme
  Bluetooth SIG (Assioma, Garmin Rally, Stages, 4iiii, Quarq, SRM…).
- ANT+ : profil Bicycle Power (type d'appareil 11), page 0x10 (puissance
  seule) ou, à défaut, page 0x12 (couple au pédalier).
"""

from __future__ import annotations

import asyncio
import math
import threading
from dataclasses import dataclass

from .ant import DeviceNumberProbe, open_ant_node
from .base import BackgroundSensor, SensorState
from .heart_rate import ANT_RF_FREQUENCY
from .trainer import (BLE_CYCLING_POWER_MEASUREMENT, BLE_CYCLING_POWER_SERVICE, BLE_FTMS_SERVICE,
                      CyclingPowerDecoder, Slope)

ANT_POWER_DEVICE_TYPE = 11
ANT_POWER_PERIOD = 8182  # 4,0049 Hz
ANT_POWER_ONLY_PAGE = 0x10
ANT_CRANK_TORQUE_PAGE = 0x12
ANT_STALL_PAGES = 12  # ~3 s sans nouvel événement : on ne pédale plus


@dataclass(frozen=True)
class PowerMeterReading:
    power_w: float
    cadence_rpm: float | None = None


# --- ANT+ Bicycle Power ----------------------------------------------------------

@dataclass
class AntPowerDecoder:
    """Décode les pages ANT+ Bicycle Power d'un capteur.

    Page 0x10 : compteur d'événements (octet 1), cadence (octet 3), puissance
    cumulée (octets 4-5) et instantanée (octets 6-7). On prend la moyenne entre
    deux événements (puissance cumulée / événements), plus juste que l'instantanée
    quand un message se perd. Page 0x12 (capteurs qui ne donnent que le couple) :
    puissance = 128π × couple cumulé / période cumulée. Si le compteur n'avance
    plus pendant `ANT_STALL_PAGES` pages, le cycliste a arrêté de pédaler : 0 W.
    Renvoie une mesure par page de puissance, None pour les autres pages.
    """

    _power_only: bool = False
    _last: dict | None = None
    _stalled: int = 0
    _reading: PowerMeterReading | None = None

    def feed(self, payload: bytes | bytearray | list[int]) -> PowerMeterReading | None:
        data = bytes(payload)
        if len(data) != 8:
            raise ValueError("une page ANT+ fait 8 octets")
        page = data[0]
        if page == ANT_POWER_ONLY_PAGE:
            if not self._power_only:  # le capteur donne la puissance directement : on s'y tient
                self._power_only, self._last = True, None
            return self._power_only_page(data)
        if page == ANT_CRANK_TORQUE_PAGE and not self._power_only:
            return self._crank_torque_page(data)
        return None

    def _power_only_page(self, data: bytes) -> PowerMeterReading:
        events, accumulated = data[1], int.from_bytes(data[4:6], "little")
        cadence = None if data[3] == 0xFF else float(data[3])
        instant = float(int.from_bytes(data[6:8], "little"))
        last, self._last = self._last, {"events": events, "acc": accumulated}
        if last is None:
            return self._set(instant, cadence)
        d_events = (events - last["events"]) % 256
        if d_events == 0:
            return self._stall()
        return self._set(((accumulated - last["acc"]) % 65536) / d_events, cadence)

    def _crank_torque_page(self, data: bytes) -> PowerMeterReading | None:
        events = data[1]
        cadence = None if data[3] == 0xFF else float(data[3])
        period, torque = int.from_bytes(data[4:6], "little"), int.from_bytes(data[6:8], "little")
        last, self._last = self._last, {"events": events, "period": period, "torque": torque}
        if last is None:
            return None
        if (events - last["events"]) % 256 == 0:
            return self._stall()
        d_period = (period - last["period"]) % 65536  # 1/2048 s
        d_torque = (torque - last["torque"]) % 65536  # 1/32 N·m
        if d_period == 0:
            return self._stall()
        return self._set(128 * math.pi * d_torque / d_period, cadence)

    def _set(self, power_w: float, cadence: float | None) -> PowerMeterReading:
        self._stalled = 0
        self._reading = PowerMeterReading(round(power_w, 1), cadence)
        return self._reading

    def _stall(self) -> PowerMeterReading:
        self._stalled += 1
        if self._stalled >= ANT_STALL_PAGES or self._reading is None:
            self._reading = PowerMeterReading(0.0, 0.0)
        return self._reading


# --- ERG réglé sur le capteur ------------------------------------------------------

class PowerMatch:
    """Corrige la consigne ERG pour que la cible soit tenue selon le capteur, et non le home trainer.

    On suit la moyenne glissante (sur ~`tau_s` secondes) de l'écart « home trainer −
    capteur », mesuré seulement quand on pédale vraiment. Si le home trainer lit
    8 W de trop, on lui demande cible + 8 W : sa régulation le cale alors à la
    cible lue par le capteur. L'écart vient de deux mesures simultanées et non de
    la consigne elle-même : pas de boucle qui s'emballe. La correction est bornée
    (`max_offset_w`, et `max_offset_frac` de la cible) en cas de mesure aberrante.
    """

    def __init__(self, tau_s: float = 10.0, min_w: float = 40.0, max_offset_w: float = 100.0,
                 max_offset_frac: float = 0.3) -> None:
        self.tau_s = tau_s
        self.min_w = min_w
        self.max_offset_w = max_offset_w
        self.max_offset_frac = max_offset_frac
        self.reset()

    def reset(self) -> None:
        self.offset_w = 0.0
        self._seen_s = 0.0

    @property
    def ready(self) -> bool:
        """Assez de mesures pour que la correction veuille dire quelque chose (~3 s de pédalage)."""
        return self._seen_s >= 3.0

    def update(self, dt: float, trainer_w: float | None, meter_w: float | None) -> None:
        if dt <= 0 or trainer_w is None or meter_w is None or min(trainer_w, meter_w) < self.min_w:
            return
        self._seen_s += dt
        # Au début, simple moyenne de ce qu'on a vu ; ensuite, moyenne glissante sur tau_s.
        weight = max(1 - math.exp(-dt / self.tau_s), dt / self._seen_s)
        self.offset_w += (trainer_w - meter_w - self.offset_w) * weight
        self.offset_w = max(-self.max_offset_w, min(self.max_offset_w, self.offset_w))

    def command(self, target: float | Slope | None) -> float | Slope | None:
        """Consigne à envoyer au home trainer : seules les consignes ERG (en watts) sont corrigées."""
        if target is None or isinstance(target, Slope) or not self.ready:
            return target
        limit = min(self.max_offset_w, self.max_offset_frac * target)
        return max(0.0, target + max(-limit, min(limit, self.offset_w)))


# --- capteurs ----------------------------------------------------------------------

def is_power_meter(adv) -> bool:
    """Annonce Bluetooth d'un capteur de puissance : Cycling Power, mais pas un home trainer FTMS."""
    uuids = {u.lower() for u in adv.service_uuids}
    return BLE_CYCLING_POWER_SERVICE in uuids and BLE_FTMS_SERVICE not in uuids


def scan_power_meters(timeout: float = 5.0) -> list:
    """Capteurs de puissance Bluetooth à portée, le plus proche en premier (bloquant)."""
    from .ble import BleDevice, _bleak

    async def scan() -> list[BleDevice]:
        bleak = _bleak()
        found = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
        devices = [BleDevice(d.address, d.name or adv.local_name or "sans nom", adv.rssi)
                   for d, adv in found.values() if is_power_meter(adv)]
        return sorted(devices, key=lambda d: -(d.rssi or -999))

    return asyncio.run(scan())


class BlePowerMeter(BackgroundSensor[PowerMeterReading]):
    """Se connecte à `address`, ou au premier capteur de puissance Bluetooth trouvé (hors home trainer)."""

    device_kind = "ble"

    def __init__(self, address: str | None = None, name: str | None = None) -> None:
        super().__init__()
        self.address = address
        self.device_id = address
        self.name = f"Puissance Bluetooth {name or address or ''}".strip()

    def _run(self, stop: threading.Event) -> None:
        from .ble import _bleak
        _bleak()
        try:
            asyncio.run(self._session(stop))
        except OSError as e:  # pas de pile Bluetooth ou adaptateur éteint : on réessaie
            raise RuntimeError(f"Bluetooth inaccessible, adaptateur absent ou éteint ? ({e})") from e

    async def _session(self, stop: threading.Event) -> None:
        from .ble import _bleak
        bleak = _bleak()
        if self.address:
            device = await bleak.BleakScanner.find_device_by_address(self.address, timeout=10)
        else:
            device = await bleak.BleakScanner.find_device_by_filter(lambda _d, adv: is_power_meter(adv), timeout=10)
        if device is None:
            self._set_state(SensorState.SEARCHING, "aucun capteur à portée, pédalez pour le réveiller")
            return
        self.name = device.name or f"Puissance Bluetooth {device.address}"
        self.device_id = device.address
        decoder = CyclingPowerDecoder()
        disconnected = asyncio.Event()

        def on_measurement(_char, data: bytearray) -> None:
            reading = decoder.feed(data)
            self._publish(PowerMeterReading(max(reading.power_w, 0.0), reading.cadence_rpm))

        async with bleak.BleakClient(device, disconnected_callback=lambda _c: disconnected.set()) as client:
            await client.start_notify(BLE_CYCLING_POWER_MEASUREMENT, on_measurement)
            self._set_state(SensorState.CONNECTED)
            while not stop.is_set() and not disconnected.is_set():
                await asyncio.sleep(0.2)
            if client.is_connected:
                await client.stop_notify(BLE_CYCLING_POWER_MEASUREMENT)


class AntPowerMeter(BackgroundSensor[PowerMeterReading]):
    """Écoute le capteur de puissance ANT+ `device_number`, ou le premier trouvé si 0."""

    device_kind = "ant"

    def __init__(self, device_number: int = 0) -> None:
        super().__init__()
        self.device_number = device_number
        self.device_id = str(device_number) if device_number else None
        self.name = f"Puissance ANT+ n°{device_number}" if device_number else "Puissance ANT+"
        self._node = None

    def _run(self, stop: threading.Event) -> None:
        from openant.easy.channel import Channel

        node = self._node = open_ant_node()
        decoder = AntPowerDecoder()
        probe = DeviceNumberProbe(self.device_number, self._found)
        try:
            channel = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)

            def on_data(data) -> None:
                probe.seen(channel)
                reading = decoder.feed(data)
                if reading is not None:
                    self._publish(reading)

            channel.on_broadcast_data = on_data
            channel.on_burst_data = on_data
            channel.set_id(self.device_number, ANT_POWER_DEVICE_TYPE, 0)
            channel.set_period(ANT_POWER_PERIOD)
            channel.set_search_timeout(255)  # recherche sans limite de temps
            channel.set_rf_freq(ANT_RF_FREQUENCY)
            channel.open()
            self._set_state(SensorState.SEARCHING, "en attente du capteur, pédalez pour le réveiller")
            if not stop.is_set():
                node.start()  # bloque jusqu'à node.stop()
        finally:
            self._node = None
            try:
                node.stop()
            except Exception:  # noqa: BLE001 (déjà arrêté)
                pass

    def _found(self, number: int) -> None:
        self.device_id = str(number)
        self.name = f"Puissance ANT+ n°{number}"

    def _interrupt(self) -> None:
        node = self._node
        if node is not None:
            node.stop()
