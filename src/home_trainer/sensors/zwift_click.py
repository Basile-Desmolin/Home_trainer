"""Commande de vitesses Zwift Click (v1 et v2) en Bluetooth, via `bleak`.

Le Click n'a pas de protocole publié : ce qui suit vient de la rétro-ingénierie
des projets libres zwiftplay (ajchellew) et BikeControl / SwiftControl.

- Service propriétaire Zwift : 00000001-19ca-… (Click v1), ou 0xFC82
  (Click v2, Zwift Ride), avec trois caractéristiques : « async »
  (notifications des boutons), « sync RX » (on y écrit) et « sync TX »
  (réponses, en indications).
- Poignée de main : on écrit « RideOn » sur sync RX. Sans clé publique, le
  Click répond en clair (pas de chiffrement). Le v2 attend en plus FF 04 00.
- Chaque notification commence par son type : 0x37 pour le Click v1, 0x23
  pour le v2 / Ride, 0x19 pour la batterie, 0x15 pour un message vide.
  Le reste est du protobuf : pour le v1, champ 1 = bouton +, champ 2 =
  bouton −, 0 = appuyé ; pour le v2, champ 1 = masque des boutons, un bit à
  0 = bouton appuyé (côté droit : plus dur, côté gauche : plus souple).

Le Click v2 doit avoir été connecté à l'appli Zwift dans la journée : sinon,
d'après BikeControl, il se déconnecte au bout d'une minute environ (on se
reconnecte alors tout seul, quelques appuis peuvent être perdus).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

from .base import BackgroundSensor, SensorState
from .ble import BleDevice, _bleak

ZWIFT_SERVICE = "00000001-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_RIDE_SERVICE = "0000fc82-0000-1000-8000-00805f9b34fb"
ZWIFT_ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_SYNC_RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_SYNC_TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_SERVICES = (ZWIFT_RIDE_SERVICE, ZWIFT_SERVICE)

ZWIFT_MANUFACTURER_ID = 0x094A
# Premier octet des données constructeur : type de commande.
CLICK_V1 = 0x09
CLICK_V2 = (0x0A, 0x0B)  # côté droit, côté gauche
RIDE = (0x07, 0x08)
PLAY = (0x02, 0x03)
CONTROLLER_TYPES = (CLICK_V1, *CLICK_V2, *RIDE, *PLAY)

RIDE_ON = b"RideOn"
CLICK_V2_START = bytes([0xFF, 0x04, 0x00])

CLICK_NOTIFICATION = 0x37
RIDE_NOTIFICATION = 0x23
BATTERY_NOTIFICATION = 0x19

# Masque des boutons du Click v2 / Zwift Ride : (bits, sens du changement de braquet).
RIDE_SHIFT_UP = 0x1000 | 0x2000 | 0x4000  # palettes et bouton du côté droit
RIDE_SHIFT_DOWN = 0x0100 | 0x0200 | 0x0400  # palettes et bouton du côté gauche


def protobuf_varints(data: bytes | bytearray) -> dict[int, int]:
    """Champs entiers (varint) d'un message protobuf ; les autres champs sont sautés."""
    fields: dict[int, int] = {}
    i = 0

    def varint() -> int:
        nonlocal i
        value = shift = 0
        while True:
            if i >= len(data):
                raise ValueError("message protobuf tronqué")
            byte = data[i]
            i += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7

    while i < len(data):
        key = varint()
        number, wire = key >> 3, key & 0x07
        if wire == 0:
            fields[number] = varint()
        elif wire == 1:
            i += 8
        elif wire == 2:
            size = varint()  # (pas `i += varint()` : `i` serait lu avant d'avancer)
            i += size
        elif wire == 5:
            i += 4
        else:
            raise ValueError(f"type de champ protobuf inconnu : {wire}")
    return fields


@dataclass(frozen=True)
class ClickStatus:
    """État du Click, publié à chaque message reçu."""

    battery_pct: int | None = None


class ClickDecoder:
    """Transforme les notifications du Click en changements de braquet (+1 / −1).

    Le Click envoie l'état de ses boutons à chaque changement (appui comme
    relâchement) : seul le passage à « appuyé » change de braquet.
    """

    def __init__(self) -> None:
        self._pressed: frozenset[int] = frozenset()
        self.battery_pct: int | None = None

    def feed(self, data: bytes | bytearray) -> list[int]:
        if not data:
            return []
        kind, message = data[0], bytes(data[1:])
        if kind == BATTERY_NOTIFICATION:
            if len(message) >= 2 and message[1] <= 100:
                self.battery_pct = message[1]
            return []
        if kind == CLICK_NOTIFICATION:
            fields = protobuf_varints(message)
            # Champ absent = valeur par défaut, 0 : appuyé (comme le fait le protobuf du Click).
            pressed = {d for d, number in ((+1, 1), (-1, 2)) if fields.get(number, 0) == 0}
        elif kind == RIDE_NOTIFICATION:
            buttons = protobuf_varints(message).get(1, 0)
            pressed = {d for d, mask in ((+1, RIDE_SHIFT_UP), (-1, RIDE_SHIFT_DOWN)) if buttons & mask != mask}
        else:
            return []
        shifts = sorted(pressed - self._pressed, reverse=True)
        self._pressed = frozenset(pressed)
        return shifts


def controller_type(adv) -> int | None:
    """Type de commande Zwift annoncé (Click, Ride, Play), d'après les données constructeur."""
    data = (getattr(adv, "manufacturer_data", None) or {}).get(ZWIFT_MANUFACTURER_ID)
    if data and data[0] in CONTROLLER_TYPES:
        return data[0]
    return None


def is_zwift_click(adv) -> bool:
    """Annonce Bluetooth d'une commande Zwift (et pas d'un home trainer Zwift Hub)."""
    return controller_type(adv) is not None


def controller_name(kind: int | None) -> str:
    if kind == CLICK_V1:
        return "Zwift Click"
    if kind in CLICK_V2:
        return "Zwift Click v2"
    if kind in RIDE:
        return "Zwift Ride"
    if kind in PLAY:
        return "Zwift Play"
    return "Zwift Click"


async def scan_async(timeout: float = 5.0) -> list[BleDevice]:
    bleak = _bleak()
    found = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = [BleDevice(d.address, d.name or adv.local_name or controller_name(controller_type(adv)), adv.rssi)
               for d, adv in found.values() if is_zwift_click(adv)]
    return sorted(devices, key=lambda d: -(d.rssi or -999))


def scan_zwift_clicks(timeout: float = 5.0) -> list[BleDevice]:
    """Commandes Zwift Click à portée, la plus proche en premier (bloquant)."""
    return asyncio.run(scan_async(timeout))


class BleZwiftClick(BackgroundSensor[ClickStatus]):
    """Se connecte à `address`, ou au premier Zwift Click trouvé ; `take_shifts` rend les appuis."""

    device_kind = "ble"
    max_age_s = float("inf")  # le Click ne parle que quand on appuie

    def __init__(self, address: str | None = None, name: str | None = None) -> None:
        super().__init__()
        self.address = address
        self.device_id = address
        self.name = name or f"Zwift Click {address or ''}".strip()
        self._shifts: list[int] = []

    def take_shifts(self) -> list[int]:
        """Changements de braquet reçus depuis le dernier appel (+1 plus dur, −1 plus souple)."""
        with self._lock:
            shifts, self._shifts = self._shifts, []
        return shifts

    def _on_shifts(self, shifts: list[int], battery_pct: int | None) -> None:
        with self._lock:
            self._shifts.extend(shifts)
        self._publish(ClickStatus(battery_pct))

    def _run(self, stop: threading.Event) -> None:
        _bleak()
        try:
            asyncio.run(self._session(stop))
        except OSError as e:  # pas de pile Bluetooth ou adaptateur éteint : on réessaie
            raise RuntimeError(f"Bluetooth inaccessible, adaptateur absent ou éteint ? ({e})") from e

    async def _session(self, stop: threading.Event) -> None:
        bleak = _bleak()
        wanted = self.address.lower() if self.address else None
        found = await bleak.BleakScanner.find_device_by_filter(
            lambda d, adv: d.address.lower() == wanted if wanted else is_zwift_click(adv), timeout=10)
        if found is None:
            self._set_state(SensorState.SEARCHING, "aucun Click à portée : appuyez sur un bouton pour le réveiller")
            return
        self.name = found.name or "Zwift Click"
        self.device_id = found.address
        decoder = ClickDecoder()
        disconnected = asyncio.Event()

        def on_async(_char, data: bytearray) -> None:
            try:
                shifts = decoder.feed(data)
            except ValueError:
                return  # message mal formé : ignoré
            self._on_shifts(shifts, decoder.battery_pct)

        async with bleak.BleakClient(found, disconnected_callback=lambda _c: disconnected.set()) as client:
            ride = client.services.get_service(ZWIFT_RIDE_SERVICE) is not None
            if not ride and client.services.get_service(ZWIFT_SERVICE) is None:
                raise RuntimeError(f"{self.name} : service Zwift introuvable "
                                   "(mettre à jour le Click avec l'appli Zwift Companion ?)")
            await client.start_notify(ZWIFT_ASYNC, on_async)
            await client.start_notify(ZWIFT_SYNC_TX, lambda _c, _data: None)  # réponse à « RideOn »
            await client.write_gatt_char(ZWIFT_SYNC_RX, RIDE_ON, response=False)
            if ride:
                await client.write_gatt_char(ZWIFT_SYNC_RX, CLICK_V2_START, response=False)
            self._set_state(SensorState.CONNECTED)
            while not stop.is_set() and not disconnected.is_set():
                await asyncio.sleep(0.2)
        if disconnected.is_set() and not stop.is_set():
            raise RuntimeError("Click déconnecté (Click v2 : le connecter une fois à Zwift dans la journée)")
