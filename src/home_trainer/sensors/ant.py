"""Ceinture cardio ANT+ (profil HRM) via `openant` et une clé USB ANT+."""

from __future__ import annotations

import logging
import threading

from .base import BackgroundSensor, SensorState, SensorUnavailable
from .heart_rate import (ANT_HRM_DEVICE_TYPE, ANT_HRM_PERIOD, ANT_RF_FREQUENCY, ANTPLUS_NETWORK_KEY,
                         AntHeartRateDecoder, HeartRateReading)

log = logging.getLogger(__name__)


def open_ant_node():
    """Ouvre la clé USB ANT+ (Garmin, Suunto, CycPlus…) et renvoie un `Node` openant."""
    try:
        from openant.easy.node import Node
    except ImportError as e:
        raise SensorUnavailable("ANT+ indisponible : installer `pip install -e \".[ant]\"`") from e
    try:
        node = Node()
    except Exception as e:  # noqa: BLE001 (openant lève des types variés selon le pilote USB)
        detail = f" ({e})" if str(e) else ""
        raise SensorUnavailable(f"clé USB ANT+ introuvable{detail}") from e
    node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
    return node


ANT_CHANNEL_ID_MESSAGE = 0x51  # openant : Message.ID.RESPONSE_CHANNEL_ID


def parse_channel_id(data) -> int:
    """Numéro d'appareil dans la réponse « Channel ID » (0x51) : numéro sur 2 octets, type, transmission."""
    return data[0] | data[1] << 8


class DeviceNumberProbe:
    """En recherche générique (numéro 0), demande à la clé le numéro de l'appareil trouvé.

    `seen(channel)` s'appelle à chaque message reçu : la première fois, la question part
    d'un fil à part (openant y répond depuis son propre fil), puis `on_number(n)`.
    """

    def __init__(self, device_number: int, on_number) -> None:
        self._asked = device_number != 0
        self._on_number = on_number

    def seen(self, channel) -> None:
        if self._asked:
            return
        self._asked = True
        threading.Thread(target=self._ask, args=(channel,), name="numéro ANT+", daemon=True).start()

    def _ask(self, channel) -> None:
        try:
            _channel, _event, data = channel.request_message(ANT_CHANNEL_ID_MESSAGE)
            number = parse_channel_id(data)
        except Exception as e:  # noqa: BLE001 (facultatif : l'appareil marche sans être mémorisé)
            log.debug("numéro ANT+ de l'appareil inconnu (%s)", e)
            return
        if number:
            self._on_number(number)


class AntHeartRateSensor(BackgroundSensor[HeartRateReading]):
    """Écoute la ceinture `device_number`, ou la première trouvée si 0."""

    device_kind = "ant"

    def __init__(self, device_number: int = 0) -> None:
        super().__init__()
        self.device_number = device_number
        self.device_id = str(device_number) if device_number else None
        self.name = f"Cardio ANT+ n°{device_number}" if device_number else "Cardio ANT+"
        self._node = None

    def _run(self, stop: threading.Event) -> None:
        from openant.easy.channel import Channel

        node = self._node = open_ant_node()
        decoder = AntHeartRateDecoder()
        probe = DeviceNumberProbe(self.device_number, self._found)
        try:
            channel = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)

            def on_data(data) -> None:
                probe.seen(channel)
                self._publish(decoder.feed(data))

            channel.on_broadcast_data = on_data
            channel.on_burst_data = channel.on_broadcast_data
            channel.set_id(self.device_number, ANT_HRM_DEVICE_TYPE, 0)
            channel.set_period(ANT_HRM_PERIOD)
            channel.set_search_timeout(255)  # recherche sans limite de temps
            channel.set_rf_freq(ANT_RF_FREQUENCY)
            channel.open()
            self._set_state(SensorState.SEARCHING, "en attente de la ceinture")
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
        self.name = f"Cardio ANT+ n°{number}"

    def _interrupt(self) -> None:
        node = self._node
        if node is not None:
            node.stop()
