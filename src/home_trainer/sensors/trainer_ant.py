"""Home trainer ANT+ FE-C (Wahoo KICKR et compatibles) via `openant` et une clé USB ANT+."""

from __future__ import annotations

import logging
import threading
import time

from .ant import DeviceNumberProbe, open_ant_node
from .base import SensorState
from .heart_rate import ANT_RF_FREQUENCY
from .trainer import (ANT_FEC_DEVICE_TYPE, ANT_FEC_PERIOD, AntFecDecoder, CalibrationPhase, TargetThrottle,
                      Trainer, fec_calibration_request, fec_page_for, fec_pages_for)

log = logging.getLogger(__name__)


class AntTrainer(Trainer):
    """Pilote le home trainer `device_number`, ou le premier trouvé si 0."""

    device_kind = "ant"

    def __init__(self, device_number: int = 0) -> None:
        # En ANT+, un message de consigne peut se perdre : on la renvoie régulièrement.
        super().__init__(TargetThrottle(min_interval_s=1.0, refresh_s=5.0))
        self.device_number = device_number
        self.device_id = str(device_number) if device_number else None
        self.name = f"Wahoo ANT+ n°{device_number}" if device_number else "Wahoo ANT+"
        self._node = None
        self._channel = None

    def _run(self, stop: threading.Event) -> None:
        from openant.easy.channel import Channel

        node = self._node = open_ant_node()
        decoder = AntFecDecoder()
        done = threading.Event()
        probe = DeviceNumberProbe(self.device_number, self._found)

        def on_data(data) -> None:
            probe.seen(self._channel)
            self.calibration.on_fec_page(data)
            reading = decoder.feed(data)
            if reading is not None:
                self._publish(reading)

        try:
            channel = self._channel = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
            channel.on_broadcast_data = on_data
            channel.on_burst_data = on_data
            channel.on_acknowledge_data = on_data
            channel.set_id(self.device_number, ANT_FEC_DEVICE_TYPE, 0)
            channel.set_period(ANT_FEC_PERIOD)
            channel.set_search_timeout(255)  # recherche sans limite de temps
            channel.set_rf_freq(ANT_RF_FREQUENCY)
            channel.open()
            self.throttle.reset()
            self._set_state(SensorState.SEARCHING, "en attente du home trainer, pédalez pour le réveiller")
            # node.start() bloque : les consignes partent d'un fil à part.
            sender = threading.Thread(target=self._send_targets, args=(channel, stop, done),
                                      name=f"consignes {self.name}", daemon=True)
            sender.start()
            if not stop.is_set():
                node.start()  # bloque jusqu'à node.stop()
        finally:
            done.set()
            self._node = self._channel = None
            try:
                node.stop()
            except Exception:  # noqa: BLE001 (déjà arrêté)
                pass

    def _found(self, number: int) -> None:
        self.device_id = str(number)
        self.name = f"Wahoo ANT+ n°{number}"

    # Sans réponse, la demande de calibration est renvoyée (un message ANT+ peut se perdre).
    calibration_resend_s = 2.0

    def _send_targets(self, channel, stop: threading.Event, done: threading.Event) -> None:
        requested_at = None
        while not stop.is_set() and not done.is_set():
            connected = self.state is SensorState.CONNECTED
            now = time.monotonic()
            calibration = self.calibration
            resend = (calibration.status.phase is CalibrationPhase.STARTING and requested_at is not None
                      and now - requested_at >= self.calibration_resend_s)
            if connected and (self._calibration_due() or resend):
                try:
                    if not resend:
                        channel.send_acknowledged_data(fec_page_for(None))
                        calibration.begin("FE-C", now)
                    requested_at = now
                    channel.send_acknowledged_data(fec_calibration_request())
                except Exception as e:  # noqa: BLE001 (message perdu : on retentera)
                    log.debug("%s : demande de calibration non transmise (%s)", self.name, e)
            due, target = self._pending_target()
            if due and connected:
                try:
                    for page in fec_pages_for(target):
                        channel.send_acknowledged_data(page)
                    self._target_sent(target)
                except Exception as e:  # noqa: BLE001 (message perdu : on retentera)
                    log.debug("%s : consigne non transmise (%s)", self.name, e)
            done.wait(0.25)

    def _interrupt(self) -> None:
        node, channel = self._node, self._channel
        if channel is not None and self.state is SensorState.CONNECTED:
            try:  # résistance libre plutôt que de laisser la dernière consigne ERG
                channel.send_acknowledged_data(fec_page_for(None))
            except Exception:  # noqa: BLE001 (home trainer déjà hors de portée)
                pass
        if node is not None:
            node.stop()
