"""Calibration (spindown) : trames FTMS, protocole Wahoo, ANT+ FE-C, pilotes sans radio, simulateur."""

import asyncio
import threading

import pytest

from home_trainer.sensors import SensorState, TrainerReading
from home_trainer.sensors.trainer import (BLE_FTMS_CONTROL_POINT, BLE_FTMS_STATUS, BLE_INDOOR_BIKE_DATA,
                                          BLE_WAHOO_CONTROL_POINT, WAHOO_UNLOCK, Calibration, CalibrationPhase,
                                          CyclingPowerDecoder, FtmsResponse, fec_calibration_request,
                                          fec_page_for, fec_target_power, ftms_set_simulation,
                                          ftms_set_target_power, ftms_spin_down, wahoo_init_spindown)
from home_trainer.sensors.trainer_ant import AntTrainer
from home_trainer.sensors.trainer_ble import BleTrainer
from home_trainer.ui.power import SimulatedTrainer

P = CalibrationPhase


def started(protocol, now=0.0):
    cal = Calibration()
    cal.request()
    cal.begin(protocol, now)
    return cal


# --- trames ----------------------------------------------------------------------

def test_calibration_commands():
    assert ftms_spin_down() == bytes([0x13, 0x01])
    assert wahoo_init_spindown() == bytes([0x49])
    assert fec_calibration_request() == [0x01, 0x80, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]


# --- Bluetooth FTMS ----------------------------------------------------------------

def test_ftms_spin_down_happy_path():
    cal = started("FTMS")
    assert cal.status.phase is P.STARTING and cal.active
    # réponse : accepté, plage 33,00 → 35,00 km/h (0,01 km/h)
    cal.on_ftms_response(FtmsResponse(0x13, 0x01, bytes([0xE4, 0x0C, 0xAC, 0x0D])))
    s = cal.status
    assert s.phase is P.SPEED_UP and (s.target_kmh, s.target_high_kmh) == (33.0, 35.0)
    assert "entre 33 et 35 km/h" in s.message
    cal.on_speed(34.2, 5)
    assert cal.status.phase is P.SPEED_UP and cal.status.speed_kmh == 34.2  # c'est le home trainer qui décide
    cal.on_ftms_status(bytes([0x14, 0x04]))
    assert cal.status.phase is P.COAST
    cal.on_ftms_status(bytes([0x14, 0x02]))
    assert cal.status.phase is P.DONE and not cal.active and cal.status.finished


def test_ftms_spin_down_error_and_refusals():
    cal = started("FTMS")
    cal.on_ftms_response(FtmsResponse(0x13, 0x01, b""))
    cal.on_ftms_status(bytes([0x14, 0x03]))
    assert cal.status.phase is P.FAILED and "ratée" in cal.status.message

    cal = started("FTMS")
    cal.on_ftms_response(FtmsResponse(0x13, 0x02))
    assert cal.status.phase is P.FAILED and cal.status.unsupported

    cal = started("FTMS")
    cal.on_ftms_response(FtmsResponse(0x05, 0x01))  # réponse à une autre commande : ignorée
    cal.on_ftms_status(bytes([0x12, 0x00]))  # autre état de la machine : ignoré
    assert cal.status.phase is P.STARTING


def test_no_answer_times_out():
    cal = started("FTMS", now=100)
    cal.tick(105)
    assert cal.status.phase is P.STARTING
    cal.tick(111)
    assert cal.status.phase is P.FAILED and "ne répond pas" in cal.status.message


def test_cancel():
    cal = started("FE-C")
    cal.cancel()
    assert cal.status.phase is P.FAILED and cal.status.message == "calibration annulée"
    cal.on_ftms_status(bytes([0x14, 0x02]))  # trop tard : on ne revient pas en arrière
    assert cal.status.phase is P.FAILED


# --- protocole Wahoo (suivi à la vitesse) ----------------------------------------------

def test_wahoo_spindown_follows_wheel_speed():
    cal = started("Wahoo", now=0)
    assert cal.status.phase is P.SPEED_UP and cal.status.target_kmh == 35
    cal.on_speed(30, 3)
    assert cal.status.phase is P.SPEED_UP
    cal.on_speed(35.4, 6)
    assert cal.status.phase is P.COAST
    cal.on_speed(10, 12)
    assert cal.status.phase is P.COAST
    cal.on_speed(1.0, 16.25)
    assert cal.status.phase is P.DONE and cal.status.spindown_ms == 10250


def test_cycling_power_wheel_speed():
    def frame(revs, event):  # drapeaux 0x10 : tours de roue (32 bits) + instant (1/2048 s)
        return bytes([0x10, 0x00, 0x96, 0x00]) + revs.to_bytes(4, "little") + event.to_bytes(2, "little")

    d = CyclingPowerDecoder()
    assert d.feed(frame(1000, 0)).speed_kmh is None
    r = d.feed(frame(1010, 4096))  # 10 tours de 2,096 m en 2 s
    assert r.power_w == 150 and r.speed_kmh == pytest.approx(37.73, abs=0.01)
    for _ in range(4):
        r = d.feed(frame(1010, 4096))
    assert r.speed_kmh == 0


# --- ANT+ FE-C -------------------------------------------------------------------------

def test_fec_spindown_pages():
    cal = started("FE-C")
    cal.on_fec_page([0x01, 0x80, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])  # écho de la demande : ignoré
    assert cal.status.phase is P.STARTING
    # en cours : vitesse trop basse (0x40), température OK (0x20), 30 °C, cible 9722 mm/s = 35 km/h
    cal.on_fec_page([0x02, 0x80, 0x60, 110, 0xFA, 0x25, 0x10, 0x27])
    s = cal.status
    assert s.phase is P.SPEED_UP and s.target_kmh == 35.0 and s.temperature_c == 30.0
    assert "jusqu'à 35 km/h" in s.message
    cal.on_fec_page([0x02, 0x80, 0xA0, 110, 0xFA, 0x25, 0x10, 0x27])  # vitesse atteinte
    assert cal.status.phase is P.COAST
    cal.on_fec_page([0x02, 0x80, 0x60, 110, 0xFA, 0x25, 0x10, 0x27])  # la roue ralentit : on reste en roue libre
    assert cal.status.phase is P.COAST
    # résultat : roue libre réussie, 31 °C, temps 0x2648 = 9800 ms
    cal.on_fec_page([0x01, 0x80, 0x00, 112, 0xFF, 0xFF, 0x48, 0x26])
    s = cal.status
    assert s.phase is P.DONE and s.spindown_ms == 9800 and s.temperature_c == 31.0


def test_fec_cold_trainer_and_failure():
    cal = started("FE-C")
    cal.on_fec_page([0x02, 0x80, 0x50, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])  # trop lent, trop froid, cible inconnue
    assert cal.status.phase is P.SPEED_UP and "froid" in cal.status.message
    assert cal.status.temperature_c is None and cal.status.target_kmh is None
    cal.on_fec_page([0x01, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    assert cal.status.phase is P.FAILED


def test_pages_ignored_when_idle():
    cal = Calibration()
    cal.on_fec_page([0x02, 0x80, 0xA0, 110, 0xFA, 0x25, 0x10, 0x27])
    cal.on_ftms_status(bytes([0x14, 0x02]))
    assert cal.status.phase is P.IDLE


# --- pilotes, sans radio -------------------------------------------------------------------

class CalibratingClient:
    """`BleakClient` FTMS factice : accepte tout, répond à la calibration par une plage de vitesse."""

    def __init__(self, spin_down_result=0x01):
        self.spin_down_result = spin_down_result
        self.writes = []
        self.handlers = {}

    async def start_notify(self, uuid, callback):
        self.handlers[uuid] = callback

    async def write_gatt_char(self, uuid, data, response=False):
        self.writes.append((uuid, bytes(data)))
        if uuid == BLE_FTMS_CONTROL_POINT:
            extra = bytes([0xE4, 0x0C, 0xAC, 0x0D]) if data[0] == 0x13 and self.spin_down_result == 0x01 else b""
            result = self.spin_down_result if data[0] == 0x13 else 0x01
            self.handlers[uuid](None, bytearray([0x80, data[0], result]) + extra)

    def sent(self, uuid=BLE_FTMS_CONTROL_POINT):
        return [data for u, data in self.writes if u == uuid]


async def wait_for(condition, timeout=2.0):
    for _ in range(int(timeout / 0.02)):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("délai dépassé")


def test_ble_trainer_ftms_calibration():
    trainer, client, stop = BleTrainer("AA:BB"), CalibratingClient(), threading.Event()

    async def scenario():
        task = asyncio.create_task(trainer._ftms(client, stop, asyncio.Event()))
        await wait_for(lambda: trainer.state is SensorState.CONNECTED)
        trainer.set_target(250)
        await wait_for(lambda: ftms_set_target_power(250) in client.sent())
        trainer.start_calibration()
        await wait_for(lambda: trainer.calibration_status().phase is P.SPEED_UP)
        assert client.sent()[-2:] == [ftms_set_simulation(), ftms_spin_down()]  # roue libre, puis calibration
        trainer.set_target(260)
        await asyncio.sleep(0.5)
        assert ftms_set_target_power(260) not in client.sent()  # pas de consigne pendant la calibration
        client.handlers[BLE_INDOOR_BIKE_DATA](None, bytearray([0x44, 0, 0x58, 0x0D, 0xB4, 0, 0xFA, 0]))
        assert trainer.calibration_status().speed_kmh == 34.16
        client.handlers[BLE_FTMS_STATUS](None, bytearray([0x14, 0x04]))
        assert trainer.calibration_status().phase is P.COAST
        client.handlers[BLE_FTMS_STATUS](None, bytearray([0x14, 0x02]))
        await wait_for(lambda: ftms_set_target_power(260) in client.sent())  # la consigne repart
        stop.set()
        await task

    asyncio.run(scenario())
    assert trainer.calibration_status().phase is P.DONE


def test_ble_trainer_falls_back_to_wahoo_spindown():
    trainer, client, stop = BleTrainer("AA:BB"), CalibratingClient(spin_down_result=0x02), threading.Event()

    async def scenario():
        task = asyncio.create_task(trainer._ftms(client, stop, asyncio.Event(), wahoo=True))
        await wait_for(lambda: trainer.state is SensorState.CONNECTED)
        trainer.start_calibration()
        await wait_for(lambda: trainer.calibration_status().phase is P.SPEED_UP)
        stop.set()
        await task

    asyncio.run(scenario())
    assert ftms_spin_down() in client.sent()
    assert client.sent(BLE_WAHOO_CONTROL_POINT) == [WAHOO_UNLOCK, wahoo_init_spindown()]
    assert trainer.calibration.protocol == "Wahoo"


def test_ble_trainer_without_wahoo_reports_unsupported():
    trainer, client, stop = BleTrainer("AA:BB"), CalibratingClient(spin_down_result=0x02), threading.Event()

    async def scenario():
        task = asyncio.create_task(trainer._ftms(client, stop, asyncio.Event()))
        await wait_for(lambda: trainer.state is SensorState.CONNECTED)
        trainer.start_calibration()
        await wait_for(lambda: trainer.calibration_status().finished)
        stop.set()
        await task

    asyncio.run(scenario())
    assert "ne propose pas" in trainer.calibration_status().message
    assert client.sent(BLE_WAHOO_CONTROL_POINT) == []


def test_calibration_needs_a_connected_trainer():
    trainer = BleTrainer()
    trainer.start_calibration()
    assert trainer.calibration_status().phase is P.FAILED and "pas encore connecté" in trainer.calibration_status().message


def test_ant_trainer_calibration():
    class FakeChannel:
        def __init__(self):
            self.pages = []

        def send_acknowledged_data(self, data):
            self.pages.append(list(data))

    trainer, channel = AntTrainer(), FakeChannel()
    trainer.calibration_resend_s = 0.3
    trainer._publish(TrainerReading(180.0, 88.0))
    trainer.set_target(200)
    stop, done = threading.Event(), threading.Event()
    sender = threading.Thread(target=trainer._send_targets, args=(channel, stop, done))
    sender.start()
    try:
        threading.Event().wait(0.4)
        assert channel.pages == [fec_target_power(200)]
        trainer.start_calibration()
        threading.Event().wait(0.9)
        assert channel.pages[1:3] == [fec_page_for(None), fec_calibration_request()]
        assert channel.pages[3:] and set(map(tuple, channel.pages[3:])) == {tuple(fec_calibration_request())}  # renvoyée
        trainer.calibration.on_fec_page([0x02, 0x80, 0x60, 110, 0xFA, 0x25, 0x10, 0x27])
        count = len(channel.pages)
        threading.Event().wait(0.5)
        assert len(channel.pages) == count  # le home trainer a répondu : plus rien ne part
        trainer.calibration.on_fec_page([0x01, 0x80, 0x00, 112, 0xFF, 0xFF, 0x48, 0x26])
        threading.Event().wait(0.4)
        assert channel.pages[-1] == fec_target_power(200)  # la consigne repart
    finally:
        done.set()
        sender.join(2)


# --- simulateur ------------------------------------------------------------------------------

def test_simulated_trainer_calibration_runs_through():
    sim = SimulatedTrainer(seed=1)
    sim.start_calibration(now=0)
    assert sim.calibration_status(now=0).phase is P.SPEED_UP
    t = 0.0
    phases = []
    while not sim.calibration_status(now=t).finished and t < 60:
        t += 0.2
        phases.append(sim.calibration_status(now=t).phase)
    s = sim.calibration_status(now=t)
    assert s.phase is P.DONE and P.COAST in phases
    assert 9000 <= s.spindown_ms <= 11000  # 30 km/h perdus à 3 km/h par seconde
