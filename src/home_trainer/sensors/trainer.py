"""Home trainer piloté : mesures, consignes, et trames Bluetooth / ANT+.

Tout ce qui touche aux octets est ici, sous forme de fonctions pures
testables sans matériel. Les pilotes radio (`trainer_ble.py`,
`trainer_ant.py`) ne font qu'envoyer et recevoir ces trames.

- Bluetooth FTMS (Fitness Machine Service, 0x1826), norme Bluetooth SIG,
  gérée par les KICKR, KICKR CORE, KICKR SNAP… depuis 2020 :
  « Indoor Bike Data » (0x2AD2) pour les mesures, « Fitness Machine Control
  Point » (0x2AD9) pour les consignes.
- Bluetooth Wahoo « historique » (firmwares sans FTMS) : puissance par le
  service Cycling Power (0x1818), consignes par la caractéristique
  propriétaire Wahoo a026e005.
- ANT+ FE-C (Fitness Equipment, type d'appareil 17) : pages 0x10 et 0x19
  pour les mesures, pages 0x31 (puissance cible) et 0x33 (pente) pour les
  consignes.

Une consigne est une puissance en watts (mode ERG), une pente (`Slope`,
mode simulation : la résistance suit la pente, le poids et la vitesse comme
sur la route) ou None : « résistance libre », c'est-à-dire du plat.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .base import BackgroundSensor


def _uuid16(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


# --- Bluetooth : identifiants ------------------------------------------------

BLE_FTMS_SERVICE = _uuid16(0x1826)
BLE_FTMS_FEATURE = _uuid16(0x2ACC)
BLE_INDOOR_BIKE_DATA = _uuid16(0x2AD2)
BLE_FTMS_CONTROL_POINT = _uuid16(0x2AD9)
BLE_FTMS_STATUS = _uuid16(0x2ADA)
BLE_CYCLING_POWER_SERVICE = _uuid16(0x1818)
BLE_CYCLING_POWER_MEASUREMENT = _uuid16(0x2A63)
BLE_WAHOO_SERVICE = "a026ee0b-0a7d-4ab3-97fa-f1500f9feb8b"
BLE_WAHOO_CONTROL_POINT = "a026e005-0a7d-4ab3-97fa-f1500f9feb8b"

# Services annoncés par un home trainer pendant la recherche.
BLE_TRAINER_SERVICES = (BLE_FTMS_SERVICE, BLE_CYCLING_POWER_SERVICE, BLE_WAHOO_SERVICE)

# --- ANT+ FE-C ---------------------------------------------------------------

ANT_FEC_DEVICE_TYPE = 17
ANT_FEC_PERIOD = 8192  # 4 Hz

# Route « normale » utilisée en résistance libre.
DEFAULT_CRR = 0.004  # coefficient de roulement
DEFAULT_CW = 0.51  # coefficient de traînée × surface frontale (kg/m)
DEFAULT_WEIGHT_KG = 75.0  # cycliste + vélo, pour le mode simulation Wahoo
DEFAULT_BIKE_KG = 9.0
MAX_TARGET_W = 2000
# FTMS ne transmet pas le poids : le home trainer simule une masse fixe, qu'on
# suppose de cet ordre. Pour que le poids compte quand même, la pente envoyée
# est mise à l'échelle (la force due à la pente est proportionnelle à la masse).
FTMS_REFERENCE_KG = DEFAULT_WEIGHT_KG


@dataclass(frozen=True)
class Slope:
    """Consigne du mode simulation : pente (%) et poids du cycliste (kg)."""

    grade_pct: float
    rider_kg: float = DEFAULT_WEIGHT_KG - DEFAULT_BIKE_KG
    bike_kg: float = DEFAULT_BIKE_KG

    @property
    def total_kg(self) -> float:
        return self.rider_kg + self.bike_kg

    def rounded(self) -> Slope:
        """Ce qui compte pour le home trainer : pente au 0,01 %, poids aux 100 g."""
        return Slope(round(self.grade_pct, 2), round(self.rider_kg, 1), round(self.bike_kg, 1))



@dataclass(frozen=True)
class TrainerReading:
    """Mesure du home trainer : mêmes champs que `ui.power.Reading`, plus la vitesse."""

    power_w: float
    cadence_rpm: float | None = None
    speed_kmh: float | None = None


def _clamp_target(watts: float) -> int:
    return max(0, min(MAX_TARGET_W, round(watts)))


def _s16(value: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=True)


def _u16(value: int) -> bytes:
    return int(value).to_bytes(2, "little")


# --- Bluetooth FTMS : mesures ------------------------------------------------

def parse_indoor_bike_data(data: bytes | bytearray) -> TrainerReading:
    """Décode une notification « Indoor Bike Data » (0x2AD2).

    Les champs présents dépendent des drapeaux (16 bits) ; attention, le bit 0
    est inversé : à 0, la vitesse instantanée est présente.
    """
    if len(data) < 2:
        raise ValueError("trame FTMS trop courte")
    flags = int.from_bytes(data[:2], "little")
    i = 2
    speed = cadence = power = None

    def take(size: int, signed: bool = False) -> int:
        nonlocal i
        if i + size > len(data):
            raise ValueError("trame FTMS tronquée")
        value = int.from_bytes(data[i:i + size], "little", signed=signed)
        i += size
        return value

    if not flags & 0x0001:
        speed = take(2) / 100  # 0,01 km/h
    if flags & 0x0002:
        take(2)  # vitesse moyenne
    if flags & 0x0004:
        cadence = take(2) / 2  # 0,5 tr/min
    if flags & 0x0008:
        take(2)  # cadence moyenne
    if flags & 0x0010:
        take(3)  # distance totale
    if flags & 0x0020:
        take(2, signed=True)  # niveau de résistance
    if flags & 0x0040:
        power = take(2, signed=True)
    return TrainerReading(float(power or 0), cadence, speed)


# --- Bluetooth FTMS : consignes ----------------------------------------------

FTMS_REQUEST_CONTROL = 0x00
FTMS_RESET = 0x01
FTMS_SET_TARGET_POWER = 0x05
FTMS_START_OR_RESUME = 0x07
FTMS_STOP_OR_PAUSE = 0x08
FTMS_SET_SIMULATION = 0x11
FTMS_RESPONSE = 0x80

FTMS_RESULTS = {0x01: "accepté", 0x02: "commande non gérée", 0x03: "paramètre invalide",
                0x04: "échec", 0x05: "contrôle non accordé (une autre appli pilote le home trainer ?)"}


def ftms_request_control() -> bytes:
    return bytes([FTMS_REQUEST_CONTROL])


def ftms_start() -> bytes:
    return bytes([FTMS_START_OR_RESUME])


def ftms_set_target_power(watts: float) -> bytes:
    return bytes([FTMS_SET_TARGET_POWER]) + _s16(_clamp_target(watts))


def ftms_set_simulation(grade_pct: float = 0.0, wind_mps: float = 0.0,
                        crr: float = DEFAULT_CRR, cw: float = DEFAULT_CW) -> bytes:
    """Mode simulation : vent (0,001 m/s), pente (0,01 %), Crr (0,0001), Cw (0,01 kg/m)."""
    return (bytes([FTMS_SET_SIMULATION]) + _s16(round(wind_mps * 1000)) + _s16(round(grade_pct * 100))
            + bytes([round(crr * 10_000), round(cw * 100)]))


def ftms_command_for(target_w: float | Slope | None) -> bytes:
    """Commande FTMS pour une consigne : puissance cible, pente, ou plat si None."""
    if target_w is None:
        return ftms_set_simulation()
    if isinstance(target_w, Slope):
        return ftms_set_simulation(ftms_grade(target_w))
    return ftms_set_target_power(target_w)


def ftms_grade(slope: Slope) -> float:
    """Pente à envoyer en FTMS, ajustée au poids réel (voir `FTMS_REFERENCE_KG`)."""
    return max(-327.0, min(327.0, slope.grade_pct * slope.total_kg / FTMS_REFERENCE_KG))


@dataclass(frozen=True)
class FtmsResponse:
    request: int
    result: int

    @property
    def ok(self) -> bool:
        return self.result == 0x01

    @property
    def message(self) -> str:
        return FTMS_RESULTS.get(self.result, f"résultat inconnu {self.result:#04x}")


def parse_ftms_response(data: bytes | bytearray) -> FtmsResponse | None:
    """Réponse du Control Point (indication 0x80, commande, résultat), sinon None."""
    if len(data) >= 3 and data[0] == FTMS_RESPONSE:
        return FtmsResponse(data[1], data[2])
    return None


# --- Bluetooth Wahoo historique ------------------------------------------------

WAHOO_UNLOCK = bytes([0x20, 0xEE, 0xFC])
WAHOO_SET_ERG = 0x42
WAHOO_SET_SIM = 0x43
WAHOO_SET_GRADE = 0x46


def wahoo_set_erg(watts: float) -> bytes:
    return bytes([WAHOO_SET_ERG]) + _u16(_clamp_target(watts))


def wahoo_set_sim(weight_kg: float = DEFAULT_WEIGHT_KG, crr: float = DEFAULT_CRR,
                  cw: float = DEFAULT_CW) -> bytes:
    """Passe en mode simulation : masse (0,01 kg), Crr (0,0001), Cw (0,001 kg/m)."""
    return (bytes([WAHOO_SET_SIM]) + _u16(round(weight_kg * 100)) + _u16(round(crr * 10_000))
            + _u16(round(cw * 1000)))


def wahoo_set_grade(grade_pct: float = 0.0) -> bytes:
    """Pente du mode simulation, de −100 % à +100 % ramenée sur 0…65535."""
    grade = max(-1.0, min(1.0, grade_pct / 100))
    return bytes([WAHOO_SET_GRADE]) + _u16(min(65535, round((grade + 1) * 32768)))


def wahoo_commands_for(target_w: float | Slope | None) -> list[bytes]:
    if target_w is None:
        return [wahoo_set_sim(), wahoo_set_grade(0.0)]
    if isinstance(target_w, Slope):
        return [wahoo_set_sim(target_w.total_kg), wahoo_set_grade(target_w.grade_pct)]
    return [wahoo_set_erg(target_w)]


@dataclass
class CyclingPowerDecoder:
    """Décode « Cycling Power Measurement » (0x2A63) ; la cadence vient des tours de pédalier."""

    _revs: int | None = None
    _event: int | None = None
    _cadence: float | None = None
    _idle: int = 0

    def feed(self, data: bytes | bytearray) -> TrainerReading:
        if len(data) < 4:
            raise ValueError("trame Cycling Power trop courte")
        flags = int.from_bytes(data[:2], "little")
        power = int.from_bytes(data[2:4], "little", signed=True)
        i = 4
        if flags & 0x0001:
            i += 1  # équilibre gauche / droite
        if flags & 0x0004:
            i += 2  # couple cumulé
        if flags & 0x0010:
            i += 6  # tours de roue
        if flags & 0x0020 and i + 4 <= len(data):
            revs = int.from_bytes(data[i:i + 2], "little")
            event = int.from_bytes(data[i + 2:i + 4], "little")  # 1/1024 s
            if self._revs is not None:
                d_revs = (revs - self._revs) % 65536
                d_time = (event - self._event) % 65536
                if d_revs and d_time:
                    self._cadence = d_revs * 60 * 1024 / d_time
                    self._idle = 0
                else:
                    self._idle += 1
                    if self._idle >= 4:  # plus de tour de pédalier depuis ~2 s
                        self._cadence = 0.0
            self._revs, self._event = revs, event
        return TrainerReading(float(power), self._cadence)


# --- ANT+ FE-C -----------------------------------------------------------------

@dataclass
class AntFecDecoder:
    """Décode les pages FE-C d'un home trainer.

    La page 0x19 (données du vélo d'appartement) porte puissance et cadence,
    la page 0x10 (données générales) la vitesse. On renvoie une mesure à
    chaque page 0x19, None pour les autres.
    """

    speed_kmh: float | None = None

    def feed(self, payload: bytes | bytearray | list[int]) -> TrainerReading | None:
        data = bytes(payload)
        if len(data) != 8:
            raise ValueError("une page ANT+ fait 8 octets")
        page = data[0]
        if page == 0x10:
            raw = int.from_bytes(data[4:6], "little")  # 0,001 m/s
            self.speed_kmh = None if raw == 0xFFFF else round(raw * 3.6 / 1000, 2)
            return None
        if page == 0x19:
            cadence = None if data[2] == 0xFF else float(data[2])
            power = data[5] | (data[6] & 0x0F) << 8
            return TrainerReading(0.0 if power == 0xFFF else float(power), cadence, self.speed_kmh)
        return None


def fec_target_power(watts: float) -> list[int]:
    """Page 0x31 : puissance cible, par pas de 0,25 W."""
    raw = round(_clamp_target(watts) * 4)
    return [0x31, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, raw & 0xFF, raw >> 8]


def fec_track_resistance(grade_pct: float = 0.0, crr: float = DEFAULT_CRR) -> list[int]:
    """Page 0x33 : pente (0,01 %, décalée de −200 %) et Crr (5×10⁻⁵)."""
    raw = round((max(-200.0, min(200.0, grade_pct)) + 200) * 100)
    return [0x33, 0xFF, 0xFF, 0xFF, 0xFF, raw & 0xFF, raw >> 8, min(254, round(crr / 5e-5))]


def fec_user_configuration(rider_kg: float, bike_kg: float = DEFAULT_BIKE_KG,
                           wheel_m: float = 0.70) -> list[int]:
    """Page 0x37 : poids du cycliste (0,01 kg), du vélo (0,05 kg sur 12 bits), diamètre de roue (cm)."""
    rider = max(0, min(65534, round(rider_kg * 100)))
    bike = max(0, min(0xFFE, round(bike_kg / 0.05)))
    return [0x37, rider & 0xFF, rider >> 8, 0xFF, 0x0F | (bike & 0x0F) << 4, bike >> 4,
            max(0, min(254, round(wheel_m * 100))), 0x00]


def fec_page_for(target_w: float | Slope | None) -> list[int]:
    if target_w is None:
        return fec_track_resistance()
    if isinstance(target_w, Slope):
        return fec_track_resistance(target_w.grade_pct)
    return fec_target_power(target_w)


def fec_pages_for(target_w: float | Slope | None) -> list[list[int]]:
    """Pages à envoyer : en pente, le poids (page 0x37) part avant la pente."""
    if isinstance(target_w, Slope):
        return [fec_user_configuration(target_w.rider_kg, target_w.bike_kg), fec_page_for(target_w)]
    return [fec_page_for(target_w)]


# --- consignes : quand les renvoyer --------------------------------------------

class TargetThrottle:
    """Décide quand transmettre la consigne au home trainer.

    L'interface la recalcule 5 fois par seconde (les rampes la font varier en
    continu) : on la transmet tout de suite si elle change franchement (nouvelle
    brique, réglage ±1 %), au plus une fois par `min_interval_s` pour les petites
    variations, et de nouveau toutes les `refresh_s` secondes si `refresh_s`
    est donné (utile en ANT+, où un message peut se perdre).
    """

    def __init__(self, min_interval_s: float = 1.0, jump_w: float = 5.0,
                 refresh_s: float | None = None) -> None:
        self.min_interval_s = min_interval_s
        self.jump_w = jump_w
        self.refresh_s = refresh_s
        self._sent: int | None | object = _NOTHING
        self._sent_at = 0.0

    def reset(self) -> None:
        """À appeler après une reconnexion : la prochaine consigne repart."""
        self._sent = _NOTHING

    def due(self, target_w: float | Slope | None, now: float) -> bool:
        target = _key(target_w)
        sent = self._sent
        if sent is _NOTHING or type(sent) is not type(target):  # passage ERG / pente / libre
            return True
        elapsed = now - self._sent_at
        if isinstance(target, Slope):
            if target != sent:  # nouvelle pente ou nouveau poids : tout de suite
                return True
        elif target is not None and target != sent:
            if abs(target - sent) >= self.jump_w or elapsed >= self.min_interval_s:
                return True
        return self.refresh_s is not None and elapsed >= self.refresh_s

    def mark_sent(self, target_w: float | Slope | None, now: float) -> None:
        self._sent = _key(target_w)
        self._sent_at = now


def _key(target_w: float | Slope | None) -> int | Slope | None:
    if target_w is None:
        return None
    return target_w.rounded() if isinstance(target_w, Slope) else _clamp_target(target_w)


_NOTHING = object()


# --- socle commun des pilotes ----------------------------------------------------

class Trainer(BackgroundSensor[TrainerReading]):
    """Home trainer réel : même interface que `ui.power.SimulatedTrainer`.

    `set_target` peut être appelé à tout moment depuis l'interface ; le fil
    radio transmet la consigne quand `TargetThrottle` le juge utile. `read`
    renvoie la dernière mesure, ou None si le home trainer ne répond plus.
    """

    max_age_s = 3.0

    def __init__(self, throttle: TargetThrottle | None = None) -> None:
        super().__init__()
        self.throttle = throttle or TargetThrottle()
        self._target: float | Slope | None = None

    @property
    def target_w(self) -> float | Slope | None:
        return self._target

    def set_target(self, watts: float | Slope | None) -> None:
        self._target = watts

    def read(self, dt: float = 0.0) -> TrainerReading | None:
        return self.latest()

    def _pending_target(self) -> tuple[bool, float | Slope | None]:
        """(à envoyer ?, consigne) : à appeler régulièrement par le fil radio."""
        target = self._target
        return self.throttle.due(target, time.monotonic()), target

    def _target_sent(self, target: float | Slope | None) -> None:
        self.throttle.mark_sent(target, time.monotonic())
