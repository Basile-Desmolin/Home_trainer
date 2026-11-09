"""Capteur de puissance externe : décodage ANT+, correction de l'ERG, et branchement dans la fenêtre."""

import math
import os
from types import SimpleNamespace

import pytest

from home_trainer.sensors import AntPowerDecoder, PowerMatch, PowerMeterReading, Slope, open_power_meter
from home_trainer.sensors.power_meter import AntPowerMeter, BlePowerMeter, is_power_meter
from home_trainer.sensors.trainer import BLE_CYCLING_POWER_SERVICE, BLE_FTMS_SERVICE, CyclingPowerDecoder


def power_only(events, accumulated, instant, cadence=90):
    return [0x10, events, 0xFF, cadence, accumulated & 0xFF, accumulated >> 8 & 0xFF, instant & 0xFF, instant >> 8]


def crank_torque(events, period, torque, cadence=90):
    return [0x12, events, events, cadence, period & 0xFF, period >> 8 & 0xFF, torque & 0xFF, torque >> 8 & 0xFF]


def test_ant_power_only_page_uses_accumulated_power():
    d = AntPowerDecoder()
    assert d.feed(power_only(1, 100, 200)) == PowerMeterReading(200, 90)  # première page : l'instantanée
    assert d.feed(power_only(2, 310, 215)).power_w == 210
    assert d.feed(power_only(4, 710, 300)).power_w == 200  # un message perdu : moyenne sur 2 événements
    d2 = AntPowerDecoder()
    d2.feed(power_only(255, 65500, 0))
    assert d2.feed(power_only(0, 164, 0)).power_w == 200  # compteurs qui repartent à zéro


def test_ant_stops_pedaling_after_three_seconds():
    d = AntPowerDecoder()
    d.feed(power_only(1, 100, 200))
    d.feed(power_only(2, 300, 200))
    for _ in range(11):
        assert d.feed(power_only(2, 300, 200)).power_w == 200  # simple répétition
    assert d.feed(power_only(2, 300, 200)) == PowerMeterReading(0.0, 0.0)


def test_ant_crank_torque_page():
    d = AntPowerDecoder()
    assert d.feed(crank_torque(1, 0, 0)) is None
    reading = d.feed(crank_torque(2, 2048, 1019, cadence=60))  # 1 s, 1019/32 N·m par tour
    assert reading.power_w == pytest.approx(128 * math.pi * 1019 / 2048, abs=0.1)
    assert reading.cadence_rpm == 60
    assert d.feed([0x01, 0, 0, 0, 0, 0, 0, 0]) is None  # page de calibration : ignorée


def test_ant_power_only_wins_over_crank_torque():
    d = AntPowerDecoder()
    d.feed(crank_torque(1, 0, 0))
    d.feed(power_only(1, 0, 180))
    assert d.feed(crank_torque(2, 2048, 5000)) is None
    assert d.feed(power_only(2, 190, 190)).power_w == 190


def test_ant_page_must_be_8_bytes():
    with pytest.raises(ValueError):
        AntPowerDecoder().feed([0x10, 0])


def test_ant_left_right_balance():
    def page(events, pedal_power):
        p = power_only(events, events * 200, 200)
        p[2] = pedal_power
        return p

    d = AntPowerDecoder()
    assert d.feed(page(1, 0x80 | 49)).balance_left_pct == 51  # 49 % jambe droite (Assioma, Garmin)
    assert d.feed(page(2, 0xFF)).balance_left_pct is None  # capteur sans équilibre
    assert d.feed(page(3, 49)).balance_left_pct is None  # jambe non précisée : pas d'équilibre fiable
    assert d.feed(page(4, 0x80 | 120)).balance_left_pct is None  # valeur impossible


def test_ble_left_right_balance():
    def frame(flags, balance=None):  # 150 W ; drapeau 0x01 : équilibre en 1/2 %, 0x02 : de la jambe gauche
        extra = bytes([balance]) if balance is not None else b""
        return flags.to_bytes(2, "little") + (150).to_bytes(2, "little") + extra

    d = CyclingPowerDecoder()
    assert d.feed(frame(0x03, 102), now=0).balance_left_pct == 51.0
    assert d.feed(frame(0x01, 97), now=1).balance_left_pct == 48.5  # jambe non précisée : comptée à gauche
    assert d.feed(frame(0x00), now=2).balance_left_pct is None
    assert d.feed(frame(0x03, 0xFF), now=3).balance_left_pct is None  # au-delà de 100 %
    # L'octet d'équilibre précède la cadence : celle-ci reste bien lue.
    crank = lambda revs, event: frame(0x23, 100) + revs.to_bytes(2, "little") + event.to_bytes(2, "little")  # noqa: E731
    d.feed(crank(10, 0), now=4)
    r = d.feed(crank(11, 1024), now=5)
    assert r.cadence_rpm == 60 and r.balance_left_pct == 50.0 and r.power_w == 150


def test_power_match_corrects_erg_target():
    match = PowerMatch()
    assert match.command(200) == 200  # rien mesuré : consigne inchangée
    for _ in range(100):  # 20 s : le home trainer lit 10 W de trop
        match.update(0.2, 210, 200)
    assert match.offset_w == pytest.approx(10, abs=0.01)
    assert match.command(200) == pytest.approx(210)
    assert match.command(None) is None
    slope = Slope(3.0, 70)
    assert match.command(slope) is slope  # pente ou résistance libre : rien à corriger


def test_power_match_ignores_coasting_and_is_bounded():
    match = PowerMatch()
    for _ in range(100):
        match.update(0.2, 20, 0)  # roue libre : pas d'écart mesuré
    assert not match.ready and match.offset_w == 0
    for _ in range(200):
        match.update(0.2, 400, 200)  # mesure aberrante
    assert match.offset_w == 100
    assert match.command(100) == 130  # au plus 30 % de la cible
    match.reset()
    assert match.offset_w == 0 and match.command(150) == 150


def test_power_match_follows_slow_drift():
    match = PowerMatch(tau_s=10)
    for _ in range(50):
        match.update(0.2, 205, 200)
    for _ in range(250):  # 50 s plus tard le home trainer a chauffé : 15 W d'écart
        match.update(0.2, 215, 200)
    assert match.offset_w == pytest.approx(15, abs=0.2)


def test_ble_scan_skips_trainers():
    meter = SimpleNamespace(service_uuids=[BLE_CYCLING_POWER_SERVICE.upper()])
    trainer = SimpleNamespace(service_uuids=[BLE_CYCLING_POWER_SERVICE, BLE_FTMS_SERVICE])
    assert is_power_meter(meter) and not is_power_meter(trainer)


def test_factory():
    assert isinstance(open_power_meter("ble", address="AA:BB"), BlePowerMeter)
    ant = open_power_meter("ant", device_number=4321)
    assert isinstance(ant, AntPowerMeter) and ant.device_id == "4321"
    with pytest.raises(ValueError):
        open_power_meter("sim")


# --- dans la fenêtre ---------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Quiet:
    """Pilote qui ne démarre pas de radio : on publie ses mesures à la main."""

    def start(self):
        pass

    def stop(self, timeout=3.0):
        pass


def test_window_uses_power_meter_watts_and_corrects_erg(app, tmp_path):
    from home_trainer.bricks import parse_workout
    from home_trainer.devices import DeviceBook
    from home_trainer.sensors import SensorState, TrainerReading
    from home_trainer.sensors.trainer_ble import BleTrainer
    from home_trainer.ui.app import MainWindow

    class Trainer(Quiet, BleTrainer):
        pass

    class Meter(Quiet, BlePowerMeter):
        pass

    book = DeviceBook(tmp_path / "appareils.json")
    trainer = Trainer("11:22")
    window = MainWindow(parse_workout("10m@200"), source=trainer, book=book)
    assert not window.power_chip_action.isVisible()  # sans capteur, pas de pastille
    meter = Meter("AA:BB")
    window.set_power_meter(meter)
    assert window.power_chip_action.isVisible()
    assert window.power_chip.text().startswith("Puissance : ")  # pas encore de mesure
    window._toggle()
    trainer._publish(TrainerReading(212, 88, 31.0))
    meter.device_id, meter.name = "AA:BB", "ASSIOMA 1234"
    meter._publish(PowerMeterReading(200, 91))
    for _ in range(100):
        window._with_power_meter(trainer.read(), 0.2)
    window._on_tick()
    reading = window._last_reading
    assert (reading.power_w, reading.cadence_rpm, reading.speed_kmh) == (200, 91, 31.0)
    assert trainer.target_w == pytest.approx(212, abs=0.5)  # 200 W selon le capteur
    assert window.cards["power"].value.text() == "200"
    assert window.power_chip.text() == "ASSIOMA 1234"
    assert "corrigée de +12 W" in window.power_chip.toolTip()
    assert book.find("power", "ble", "AA:BB") is not None  # mémorisé à la connexion

    window._toggle()  # pause : résistance libre, sans correction
    window._push_target()
    assert trainer.target_w is None
    window._toggle()
    meter._set_state(SensorState.ERROR, "déconnecté")
    meter._latest = None  # capteur muet : on revient aux watts du home trainer
    window._on_tick()
    assert window._last_reading.power_w == 212
    assert window.power_chip.text() == "Puissance : erreur : déconnecté"
    window.set_power_meter(None)
    assert trainer.target_w == pytest.approx(200, abs=0.5)
    assert not window.power_chip_action.isVisible()
    window.close()


def test_balance_comes_from_power_meter_only(app, tmp_path):
    from home_trainer.bricks import parse_workout
    from home_trainer.devices import DeviceBook
    from home_trainer.sensors import TrainerReading
    from home_trainer.ui.app import MainWindow

    class Meter(Quiet, BlePowerMeter):
        pass

    window = MainWindow(parse_workout("10m@200"), book=DeviceBook(tmp_path / "appareils.json"))
    meter = Meter("AA:BB")
    window.set_power_meter(meter)
    meter._publish(PowerMeterReading(200, 91, 51.5))
    trainer = TrainerReading(212, 88, 31.0, balance_left_pct=40.0)  # un home trainer n'en a pas de réel
    assert window._with_power_meter(trainer, 0.2).balance_left_pct == 51.5
    assert window._with_power_meter(None, 0.2).balance_left_pct == 51.5
    window.enter_free_ride()
    window.free.start()
    window.free.elapsed_s = 1.0  # une seconde de chrono, enregistrée avec son équilibre
    window._record_seconds(window.free, 0.0, window._with_power_meter(trainer, 1.0))
    assert window.free.samples[-1].balance_left_pct == 51.5
    from home_trainer.ui.session import ride_points
    assert ride_points(window.free.samples)[-1].balance_left_pct == 51.5  # jusqu'au .fit
    meter._publish(PowerMeterReading(200, 91))  # capteur sans équilibre
    assert window._with_power_meter(trainer, 0.2).balance_left_pct is None
    window.close()


def test_second_balance_is_weighted_by_power():
    """0,5 s à 200 W et 52 % gauche, puis 0,5 s à 100 W et 46 % : (52×200 + 46×100) / 300 = 50,0 %
    (moyenne simple : 49,0 %). Une lecture à 0 W ou sans équilibre ne compte pas."""
    from home_trainer.ui.session import SecondSampler

    sampler = SecondSampler()
    assert sampler.feed(0.0, 0.5, 200, balance_left_pct=52) == []
    second = sampler.feed(0.5, 1.0, 100, balance_left_pct=46)[0]
    assert second.balance_left_pct == pytest.approx(50.0)
    assert second.power_w == pytest.approx(150.0)
    sampler.feed(1.0, 1.5, 0, balance_left_pct=10)  # roue libre : poids nul
    assert sampler.feed(1.5, 2.0, 180, balance_left_pct=51)[0].balance_left_pct == pytest.approx(51.0)
    assert sampler.feed(2.0, 3.0, 180)[0].balance_left_pct is None
    assert sampler.feed(3.0, 4.0, 0, balance_left_pct=50)[0].balance_left_pct is None


def test_power_meter_dialog(app, tmp_path):
    from home_trainer.devices import DeviceBook
    from home_trainer.ui.app import PowerMeterDialog

    book = DeviceBook(tmp_path / "appareils.json")
    dialog = PowerMeterDialog(book)
    assert dialog.kind.currentData() is None and dialog.sensor() is None
    dialog.kind.setCurrentIndex(dialog.kind.findData("ant"))
    dialog.ant_number.setValue(4321)
    dialog.accept()
    meter = dialog.sensor()
    assert isinstance(meter, AntPowerMeter) and meter.device_number == 4321
    assert DeviceBook.load(book.path).last("power") == ("ant", "4321")
    assert book.startup_choice("power", ("ble", "ant", None), default=None) == ("ant", None, 4321)


def test_power_meter_is_chosen_from_trainer_dialog(app, tmp_path):
    from home_trainer.devices import DeviceBook
    from home_trainer.ui.app import TrainerDialog

    chosen = []

    def choose(parent):
        chosen.append(parent)
        return "Assioma"

    dialog = TrainerDialog(DeviceBook(tmp_path / "a.json"), choose_power_meter=choose)
    assert dialog.power_meter_label.text() == "aucun"
    dialog.power_meter_button.click()
    assert chosen == [dialog] and dialog.power_meter_label.text() == "Assioma"
