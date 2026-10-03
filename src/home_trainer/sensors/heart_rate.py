"""Fréquence cardiaque : mesure, décodage des trames Bluetooth et ANT+.

Les deux décodeurs sont de simples fonctions sur des octets, indépendantes
de la radio, ce qui permet de les tester sans capteur.

- Bluetooth : caractéristique « Heart Rate Measurement » (0x2A37) du service
  Heart Rate (0x180D), norme Bluetooth SIG.
- ANT+ : profil « Heart Rate Monitor » (type d'appareil 120), pages de 8 octets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BLE_HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
BLE_HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
BLE_BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

ANT_HRM_DEVICE_TYPE = 120
ANT_HRM_PERIOD = 8070  # 4,06 Hz
ANT_RF_FREQUENCY = 57  # 2457 MHz
ANTPLUS_NETWORK_KEY = [0xB9, 0xA5, 0x21, 0xFB, 0xBD, 0x72, 0xC3, 0x45]


@dataclass(frozen=True)
class HeartRateReading:
    bpm: int
    rr_ms: tuple[float, ...] = ()  # intervalles entre battements reçus avec cette mesure
    battery_pct: int | None = None
    contact: bool | None = None  # contact de la sangle avec la peau, si le capteur le dit


def parse_ble_measurement(data: bytes | bytearray) -> HeartRateReading:
    """Décode une notification Heart Rate Measurement (0x2A37)."""
    if len(data) < 2:
        raise ValueError("trame cardio Bluetooth trop courte")
    flags = data[0]
    i = 1
    if flags & 0x01:  # fréquence sur 16 bits
        if len(data) < 3:
            raise ValueError("trame cardio Bluetooth trop courte")
        bpm = int.from_bytes(data[1:3], "little")
        i = 3
    else:
        bpm = data[1]
        i = 2
    contact = bool(flags & 0x02) if flags & 0x04 else None
    if flags & 0x08:  # énergie dépensée (2 octets), ignorée
        i += 2
    rr: list[float] = []
    if flags & 0x10:
        while i + 1 < len(data):
            rr.append(int.from_bytes(data[i:i + 2], "little") * 1000 / 1024)
            i += 2
    return HeartRateReading(bpm, tuple(rr), contact=contact)


@dataclass
class AntHeartRateDecoder:
    """Décode le flux de pages ANT+ HRM d'un capteur.

    Chaque page porte la fréquence instantanée (octet 7), l'heure du dernier
    battement (octets 4-5, 1/1024 s) et un compteur de battements (octet 6),
    d'où l'on déduit les intervalles R-R. La page 7 donne la batterie.
    """

    _last_beat_time: int | None = None
    _last_beat_count: int | None = None
    battery_pct: int | None = None
    _seen: set[int] = field(default_factory=set)

    def feed(self, payload: bytes | bytearray | list[int]) -> HeartRateReading:
        data = bytes(payload)
        if len(data) != 8:
            raise ValueError("une page ANT+ fait 8 octets")
        page = data[0] & 0x7F
        if page == 7 and data[1] <= 100:
            self.battery_pct = data[1]
        beat_time = int.from_bytes(data[4:6], "little")
        beat_count = data[6]
        rr: tuple[float, ...] = ()
        if self._last_beat_count is not None and beat_count != self._last_beat_count:
            beats = (beat_count - self._last_beat_count) % 256
            if beats == 1:  # sinon des battements ont été manqués : intervalle inconnu
                rr = (((beat_time - self._last_beat_time) % 65536) * 1000 / 1024,)
        self._last_beat_time, self._last_beat_count = beat_time, beat_count
        return HeartRateReading(data[7], rr, self.battery_pct)
