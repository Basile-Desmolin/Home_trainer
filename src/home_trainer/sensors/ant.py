"""Ceinture cardio ANT+ (profil HRM) via `openant` et une clé USB ANT+."""

from __future__ import annotations

import threading

from .base import BackgroundSensor, SensorState, SensorUnavailable
from .heart_rate import (ANT_HRM_DEVICE_TYPE, ANT_HRM_PERIOD, ANT_RF_FREQUENCY, ANTPLUS_NETWORK_KEY,
                         AntHeartRateDecoder, HeartRateReading)


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


class AntHeartRateSensor(BackgroundSensor[HeartRateReading]):
    """Écoute la ceinture `device_number`, ou la première trouvée si 0."""

    def __init__(self, device_number: int = 0) -> None:
        super().__init__()
        self.device_number = device_number
        self.name = f"Cardio ANT+ n°{device_number}" if device_number else "Cardio ANT+"
        self._node = None

    def _run(self, stop: threading.Event) -> None:
        from openant.easy.channel import Channel

        node = self._node = open_ant_node()
        decoder = AntHeartRateDecoder()
        try:
            channel = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
            channel.on_broadcast_data = lambda data: self._publish(decoder.feed(data))
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

    def _interrupt(self) -> None:
        node = self._node
        if node is not None:
            node.stop()
