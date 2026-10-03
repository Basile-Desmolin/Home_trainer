import time

import pytest

from home_trainer.sensors import (AntHeartRateDecoder, BackgroundSensor, SensorState, SensorUnavailable,
                                  HeartRateReading, open_heart_rate_sensor, parse_ble_measurement)
from home_trainer.ui import WorkoutSession
from home_trainer.bricks import parse_workout


# --- Bluetooth : caractéristique Heart Rate Measurement (0x2A37) ---------------

def test_ble_8_bit_heart_rate():
    r = parse_ble_measurement(bytes([0x00, 72]))
    assert r.bpm == 72 and r.rr_ms == () and r.contact is None


def test_ble_16_bit_heart_rate_with_contact():
    r = parse_ble_measurement(bytes([0x01 | 0x04 | 0x02, 0x2C, 0x01]))
    assert r.bpm == 300 and r.contact is True
    assert parse_ble_measurement(bytes([0x04, 80])).contact is False


def test_ble_energy_then_rr_intervals():
    # flags : énergie + R-R ; 150 bpm ; énergie 0x0010 ; R-R 1024 et 512 (en 1/1024 s)
    data = bytes([0x08 | 0x10, 150, 0x10, 0x00, 0x00, 0x04, 0x00, 0x02])
    r = parse_ble_measurement(data)
    assert r.bpm == 150 and r.rr_ms == (1000.0, 500.0)


def test_ble_too_short():
    with pytest.raises(ValueError):
        parse_ble_measurement(b"\x00")


# --- ANT+ : pages HRM ----------------------------------------------------------

def page(number, beat_time, count, bpm, b1=0, b2=0, b3=0):
    return [number, b1, b2, b3, beat_time & 0xFF, beat_time >> 8, count, bpm]


def test_ant_heart_rate_and_rr():
    d = AntHeartRateDecoder()
    assert d.feed(page(0, 1000, 10, 120)).bpm == 120
    r = d.feed(page(0x80 | 4, 1512, 11, 121))  # bit de bascule ignoré, +512/1024 s
    assert r.bpm == 121 and r.rr_ms == (500.0,)
    assert d.feed(page(4, 1512, 11, 121)).rr_ms == ()  # même battement répété


def test_ant_rr_wraps_and_skips_missed_beats():
    d = AntHeartRateDecoder()
    d.feed(page(0, 65000, 255, 100))
    assert d.feed(page(0, 64, 0, 100)).rr_ms == (600 * 1000 / 1024,)  # 65000 → 64 : 600 ticks
    assert d.feed(page(0, 2000, 2, 100)).rr_ms == ()  # deux battements d'un coup


def test_ant_battery_page():
    d = AntHeartRateDecoder()
    assert d.feed(page(7, 0, 0, 90, b1=85)).battery_pct == 85
    assert d.feed(page(0, 0, 0, 91)).battery_pct == 85


def test_ant_page_must_be_8_bytes():
    with pytest.raises(ValueError):
        AntHeartRateDecoder().feed([0, 1, 2])


# --- socle des capteurs --------------------------------------------------------

class Beating(BackgroundSensor):
    """Capteur de test : publie un battement à chaque tour."""

    name = "test"

    def _run(self, stop):
        while not stop.wait(0.01):
            self._publish(HeartRateReading(120))


def test_sensor_runs_in_background():
    hr = Beating()
    hr.start()
    try:
        deadline = time.monotonic() + 2
        while hr.latest() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert hr.latest().bpm == 120 and hr.running and hr.state is SensorState.CONNECTED
    finally:
        hr.stop()
    assert not hr.running and hr.state is SensorState.STOPPED


def test_stale_reading_is_dropped():
    hr = Beating()
    hr._publish(HeartRateReading(100))
    assert hr.latest().bpm == 100
    hr.max_age_s = 0
    time.sleep(0.01)
    assert hr.latest() is None


def test_missing_hardware_is_reported_without_retry():
    class Broken(BackgroundSensor):
        name = "test"
        calls = 0

        def _run(self, stop):
            Broken.calls += 1
            raise SensorUnavailable("clé USB ANT+ introuvable")

    s = Broken()
    s.start()
    s._thread.join(2)
    assert s.state is SensorState.ERROR and "introuvable" in s.status and Broken.calls == 1


def test_factory():
    assert open_heart_rate_sensor("ble", address="AA:BB").address == "AA:BB"
    assert open_heart_rate_sensor("ant", device_number=42).device_number == 42
    with pytest.raises(ValueError):
        open_heart_rate_sensor("usb")
    with pytest.raises(ValueError):  # plus de cardio estimé à partir de la puissance
        open_heart_rate_sensor("sim")


def test_session_records_heart_rate():
    s = WorkoutSession(parse_workout("10m@150"), 250)
    s.start()
    s.record(150, 90, 130)
    s.tick(1)
    s.record(150, 90, 140)
    s.record(150, 90, None)
    assert s.samples[0].heart_rate_bpm == 130
    assert s.average_heart_rate() == 135
