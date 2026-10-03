import asyncio
import threading

import pytest

from home_trainer.sensors import SensorState, TrainerReading, open_trainer
from home_trainer.sensors.trainer import (AntFecDecoder, CyclingPowerDecoder, Slope, TargetThrottle, Trainer,
                                          WAHOO_UNLOCK, fec_page_for, fec_pages_for, fec_target_power, fec_track_resistance,
                                          ftms_command_for, ftms_request_control, ftms_set_simulation,
                                          ftms_set_target_power, ftms_start, parse_ftms_response,
                                          parse_indoor_bike_data, wahoo_commands_for, wahoo_set_erg,
                                          wahoo_set_grade, wahoo_set_sim)
from home_trainer.sensors.trainer_ant import AntTrainer
from home_trainer.sensors.trainer_ble import BleTrainer
from home_trainer.ui.power import SimulatedTrainer, open_power_source


# --- Bluetooth FTMS : Indoor Bike Data (0x2AD2) ---------------------------------

def test_indoor_bike_data_speed_cadence_power():
    # drapeaux 0x0044 : vitesse (bit 0 à 0), cadence, puissance
    data = bytes([0x44, 0x00, 0xC4, 0x09, 0xB4, 0x00, 0xFA, 0x00])
    assert parse_indoor_bike_data(data) == TrainerReading(250.0, 90.0, 25.0)


def test_indoor_bike_data_skips_optional_fields():
    # KICKR typique : vitesse, cadence, distance (3 o), puissance, cardio absent
    data = bytes([0x54, 0x00, 0x10, 0x0E, 0xAA, 0x00, 0x39, 0x30, 0x00, 0x2C, 0x01])
    assert parse_indoor_bike_data(data) == TrainerReading(300.0, 85.0, 36.0)


def test_indoor_bike_data_without_speed_nor_power():
    # bit 0 à 1 : pas de vitesse ; résistance (2 o) puis rien d'autre
    r = parse_indoor_bike_data(bytes([0x21, 0x00, 0x05, 0x00]))
    assert r == TrainerReading(0.0, None, None)


def test_indoor_bike_data_truncated():
    with pytest.raises(ValueError):
        parse_indoor_bike_data(bytes([0x40, 0x00, 0x01]))


# --- Bluetooth FTMS : Control Point (0x2AD9) ------------------------------------

def test_ftms_commands():
    assert ftms_request_control() == b"\x00"
    assert ftms_start() == b"\x07"
    assert ftms_set_target_power(250) == bytes([0x05, 0xFA, 0x00])
    assert ftms_set_target_power(312.6) == bytes([0x05, 0x39, 0x01])
    assert ftms_set_target_power(-20) == bytes([0x05, 0, 0])


def test_ftms_free_ride_is_flat_simulation():
    # vent 0, pente 0, Crr 0,004 → 40, Cw 0,51 → 51
    assert ftms_set_simulation() == bytes([0x11, 0, 0, 0, 0, 40, 51])
    assert ftms_set_simulation(grade_pct=-1.5)[3:5] == (-150).to_bytes(2, "little", signed=True)
    assert ftms_command_for(None) == ftms_set_simulation()
    assert ftms_command_for(200) == ftms_set_target_power(200)


def test_ftms_response():
    r = parse_ftms_response(bytes([0x80, 0x00, 0x01]))
    assert r.ok and r.request == 0x00
    refused = parse_ftms_response(bytes([0x80, 0x00, 0x05]))
    assert not refused.ok and "autre appli" in refused.message
    assert parse_ftms_response(bytes([0x05, 0xFA])) is None


# --- Bluetooth Wahoo historique ---------------------------------------------------

def test_wahoo_commands():
    assert WAHOO_UNLOCK == bytes([0x20, 0xEE, 0xFC])
    assert wahoo_set_erg(250) == bytes([0x42, 0xFA, 0x00])
    # 75 kg → 7500, Crr 40, Cw 510
    assert wahoo_set_sim() == bytes([0x43, 0x4C, 0x1D, 0x28, 0x00, 0xFE, 0x01])
    assert wahoo_set_grade(0) == bytes([0x46, 0x00, 0x80])
    assert wahoo_set_grade(100) == bytes([0x46, 0xFF, 0xFF])
    assert wahoo_set_grade(-100) == bytes([0x46, 0x00, 0x00])
    assert wahoo_commands_for(180) == [wahoo_set_erg(180)]
    assert wahoo_commands_for(None) == [wahoo_set_sim(), wahoo_set_grade(0)]


def crank(power, revs, event):
    return bytes([0x20, 0x00]) + power.to_bytes(2, "little") + revs.to_bytes(2, "little") + event.to_bytes(2, "little")


def test_cycling_power_cadence_from_crank_revolutions():
    d = CyclingPowerDecoder()
    assert d.feed(crank(200, 100, 1000)) == TrainerReading(200.0, None)
    r = d.feed(crank(210, 101, 1000 + 683))  # un tour en 683/1024 s ≈ 90 tr/min
    assert r.power_w == 210 and r.cadence_rpm == pytest.approx(90, abs=0.1)
    d = CyclingPowerDecoder()
    d.feed(crank(200, 65534, 65000))
    r = d.feed(crank(200, 1, (65000 + 2048) % 65536))  # compteurs qui repassent par zéro : 3 tours en 2 s
    assert r.cadence_rpm == pytest.approx(90)


def test_cycling_power_cadence_drops_to_zero_when_pedaling_stops():
    d = CyclingPowerDecoder()
    d.feed(crank(150, 10, 0))
    assert d.feed(crank(150, 11, 700)).cadence_rpm > 80
    for _ in range(3):
        assert d.feed(crank(0, 11, 700)).cadence_rpm > 80  # simple répétition de la même mesure
    assert d.feed(crank(0, 11, 700)).cadence_rpm == 0


def test_cycling_power_skips_balance_and_wheel_data():
    data = bytes([0x31, 0x00, 0x2C, 0x01, 50]) + bytes(6) + bytes([5, 0, 0, 4])
    d = CyclingPowerDecoder()
    assert d.feed(data).power_w == 300 and d._revs == 5 and d._event == 1024


# --- ANT+ FE-C --------------------------------------------------------------------

def test_fec_trainer_page():
    d = AntFecDecoder()
    assert d.feed([0x10, 0x19, 0, 0, 0x88, 0x13, 0xFF, 0x24]) is None  # 5 m/s
    # page 0x19 : cadence 92, puissance 0x12C = 300 W (12 bits), état du trainer dans le quartet haut
    r = d.feed([0x19, 7, 92, 0x10, 0x27, 0x2C, 0x31, 0x30])
    assert r == TrainerReading(300.0, 92.0, 18.0)


def test_fec_invalid_values():
    d = AntFecDecoder()
    assert d.feed([0x10, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0]) is None and d.speed_kmh is None
    assert d.feed([0x19, 0, 0xFF, 0, 0, 0xFF, 0x0F, 0]) == TrainerReading(0.0, None, None)
    assert d.feed([0x50, 0, 0, 0, 0, 0, 0, 0]) is None
    with pytest.raises(ValueError):
        d.feed([0x19, 0])


def test_fec_commands():
    assert fec_target_power(250) == [0x31, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xE8, 0x03]  # 1000 × 0,25 W
    # pente 0 % → 20000 (décalage de 200 %), Crr 0,004 → 80
    assert fec_track_resistance() == [0x33, 0xFF, 0xFF, 0xFF, 0xFF, 0x20, 0x4E, 80]
    assert fec_track_resistance(5)[5:7] == [0x14, 0x50]  # 20500
    assert fec_page_for(None) == fec_track_resistance()
    assert all(len(fec_page_for(t)) == 8 for t in (None, 0, 150, 5000))


# --- quand renvoyer la consigne ---------------------------------------------------

def test_throttle_sends_changes_but_not_every_ramp_tick():
    t = TargetThrottle(min_interval_s=1.0, jump_w=5)
    assert t.due(None, 0)
    t.mark_sent(None, 0)
    assert not t.due(None, 0.5)
    assert t.due(200, 0.6)  # passage en ERG : tout de suite
    t.mark_sent(200, 0.6)
    assert not t.due(200.4, 0.8)  # même watt arrondi
    assert not t.due(202, 0.8)  # petite variation de rampe : on attend
    assert t.due(202, 1.7)
    assert t.due(230, 0.7)  # nouvelle brique : tout de suite
    assert t.due(None, 0.7)  # pause : tout de suite


def test_throttle_refresh_and_reset():
    t = TargetThrottle(refresh_s=5)
    t.mark_sent(150, 0)
    assert not t.due(150, 4)
    assert t.due(150, 5)
    assert TargetThrottle().due(150, 100)  # rien d'envoyé : toujours dû
    t.reset()
    assert t.due(150, 0.1)


# --- pilotes, sans radio -----------------------------------------------------------

class FakeClient:
    """Imite un `BleakClient` FTMS : répond au Control Point, note les écritures."""

    def __init__(self, result=0x01):
        self.result = result
        self.writes = []
        self.handlers = {}

    async def start_notify(self, uuid, callback):
        self.handlers[uuid] = callback

    async def write_gatt_char(self, uuid, data, response=False):
        self.writes.append(bytes(data))
        callback = self.handlers.get(uuid)
        if callback is not None:
            callback(None, bytearray([0x80, data[0], self.result]))


def run_ftms(trainer, client, stop, duration=0.7):
    from home_trainer.sensors.trainer import BLE_INDOOR_BIKE_DATA

    async def scenario():
        disconnected = asyncio.Event()
        task = asyncio.create_task(trainer._ftms(client, stop, disconnected))
        await asyncio.sleep(0.05)
        client.handlers[BLE_INDOOR_BIKE_DATA](None, bytearray([0x44, 0, 0xC4, 9, 0xB4, 0, 0xFA, 0]))
        trainer.set_target(250)
        await asyncio.sleep(duration)
        stop.set()
        await task

    asyncio.run(scenario())


def test_ble_trainer_ftms_session():
    trainer, client, stop = BleTrainer("AA:BB"), FakeClient(), threading.Event()
    run_ftms(trainer, client, stop)
    assert client.writes[:3] == [ftms_request_control(), ftms_start(), ftms_set_simulation()]
    assert ftms_set_target_power(250) in client.writes
    assert client.writes[-1] == ftms_set_simulation()  # résistance libre en partant
    assert trainer.read() == TrainerReading(250.0, 90.0, 25.0) and trainer.state is SensorState.CONNECTED


def test_ble_trainer_control_refused():
    trainer, stop = BleTrainer(), threading.Event()
    with pytest.raises(RuntimeError, match="autre appli"):
        asyncio.run(trainer._ftms(FakeClient(result=0x05), stop, asyncio.Event()))


def test_ant_trainer_sends_targets_once_connected():
    class FakeChannel:
        def __init__(self):
            self.pages = []

        def send_acknowledged_data(self, data):
            self.pages.append(list(data))

    trainer, channel = AntTrainer(), FakeChannel()
    stop, done = threading.Event(), threading.Event()
    sender = threading.Thread(target=trainer._send_targets, args=(channel, stop, done))
    sender.start()
    try:
        trainer.set_target(200)
        threading.Event().wait(0.4)
        assert channel.pages == []  # rien tant que le home trainer n'a pas parlé
        trainer._publish(TrainerReading(180.0, 88.0))
        threading.Event().wait(0.6)
        assert channel.pages == [fec_target_power(200)]
    finally:
        done.set()
        sender.join(2)


def test_factories():
    assert isinstance(open_trainer("ble", address="AA:BB"), BleTrainer)
    assert open_trainer("ant", device_number=7).device_number == 7
    assert isinstance(open_power_source("sim"), SimulatedTrainer)
    assert isinstance(open_power_source("ant"), Trainer)
    with pytest.raises(ValueError):
        open_trainer("usb")


def test_trainer_read_is_none_until_data():
    t = BleTrainer()
    assert t.read(0.2) is None
    t.set_target(150)
    assert t.target_w == 150
    t._publish(TrainerReading(148.0, 90.0))
    assert t.read(0.2).power_w == 148


# --- pente simulée -------------------------------------------------------------------

def test_slope_commands_carry_grade_and_weight():
    slope = Slope(5.0, rider_kg=66.0)  # 66 + 9 = 75 kg : la masse de référence FTMS
    assert ftms_command_for(slope) == ftms_set_simulation(5.0)
    assert ftms_command_for(Slope(5.0, rider_kg=96.0)) == ftms_set_simulation(7.0)  # (96 + 9) / 75 × 5 %
    assert wahoo_commands_for(slope) == [wahoo_set_sim(75.0), wahoo_set_grade(5.0)]
    assert fec_page_for(slope) == fec_track_resistance(5.0)
    config, track = fec_pages_for(Slope(-2.5, rider_kg=70.0, bike_kg=8.0))
    assert config == [0x37, 0x58, 0x1B, 0xFF, 0x0F, 0x0A, 70, 0x00]  # 7000 × 0,01 kg ; 160 × 0,05 kg
    assert track == fec_track_resistance(-2.5)
    assert fec_pages_for(200) == [fec_target_power(200)]


def test_throttle_sends_slope_changes_at_once():
    t = TargetThrottle(min_interval_s=1.0)
    t.mark_sent(200, 0)
    assert t.due(Slope(3.0), 0.1)  # ERG → pente : tout de suite
    t.mark_sent(Slope(3.0), 0.1)
    assert not t.due(Slope(3.0), 0.2)
    assert t.due(Slope(3.5), 0.2)  # nouvelle pente
    assert t.due(Slope(3.0, rider_kg=80), 0.2)  # nouveau poids
    assert t.due(None, 0.2)
