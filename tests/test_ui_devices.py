"""Dialogues « Home trainer… » et « Cardio… » avec appareils mémorisés (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.sensors import SensorState  # noqa: E402
from home_trainer.sensors.trainer_ble import BleTrainer  # noqa: E402
from home_trainer.ui.app import HeartRateDialog, MainWindow, TrainerDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def book(tmp_path):
    book = DeviceBook(tmp_path / "appareils.json")
    book.remember("trainer", "ant", "12345", "Wahoo ANT+ n°12345")
    book.remember("trainer", "ble", "AA:BB", "Wahoo Bluetooth KICKR")
    return book


def test_dialog_lists_saved_devices_and_selects_last(app, book):
    dialog = TrainerDialog(book)
    labels = [dialog.kind.itemText(i) for i in range(dialog.kind.count())]
    assert labels[:2] == ["Wahoo Bluetooth KICKR  (Bluetooth)", "Wahoo ANT+ n°12345  (ANT+)"]
    assert dialog.saved_device().ident == "AA:BB"
    assert dialog.name_edit.text() == "Wahoo Bluetooth KICKR"
    assert dialog.choice() == ("ble", "AA:BB")
    trainer = dialog.sensor()
    assert isinstance(trainer, BleTrainer) and trainer.address == "AA:BB"


def test_rename_in_dialog_is_saved(app, book):
    dialog = TrainerDialog(book)
    dialog.name_edit.setText("Kickr du salon")
    dialog.name_edit.editingFinished.emit()
    assert dialog.kind.currentText() == "Kickr du salon  (Bluetooth)"
    assert DeviceBook.load(book.path).find("trainer", "ble", "AA:BB").name == "Kickr du salon"


def test_forget_and_accept_records_last_choice(app, book):
    dialog = TrainerDialog(book)
    dialog.forget_button.click()
    assert [d.ident for d in book.devices("trainer")] == ["12345"]
    assert dialog.kind.currentData() == "ble"
    dialog.kind.setCurrentIndex(dialog.kind.findData("sim"))
    dialog.accept()
    assert DeviceBook.load(book.path).last("trainer") == ("sim", None)
    assert TrainerDialog(book).kind.currentData() == "sim"


def test_heart_rate_dialog_none_is_remembered(app, book):
    dialog = HeartRateDialog(book)
    assert dialog.kind.currentData() == "sim"  # jamais choisi : simulé
    dialog.kind.setCurrentIndex(dialog.kind.findData(None))
    dialog.accept()
    assert dialog.sensor() is None
    assert HeartRateDialog(book).kind.currentText() == "Aucun"


def test_connected_device_is_remembered_and_shown_with_its_name(app, tmp_path):
    book = DeviceBook(tmp_path / "appareils.json")
    trainer = BleTrainer()  # « premier trouvé » : adresse découverte à la connexion
    trainer.start = trainer.stop = lambda *a, **k: None
    window = MainWindow(parse_workout("1m@100"), source=trainer, book=book)
    trainer.device_id, trainer.name = "11:22:33", "Wahoo Bluetooth KICKR 1234"
    trainer._set_state(SensorState.CONNECTED)
    window._refresh()
    saved = book.find("trainer", "ble", "11:22:33")
    assert saved is not None and DeviceBook.load(book.path).last("trainer") == ("ble", "11:22:33")
    book.rename(saved, "Kickr")
    window._refresh()
    assert window.source_label.text().strip().startswith("Kickr")
    window.close()


def test_calibration_wizard_from_trainer_dialog(app, book):
    from home_trainer.sensors.trainer import CalibrationPhase
    from home_trainer.ui.calibration import CalibrationDialog
    from home_trainer.ui.power import SimulatedTrainer

    assert not TrainerDialog(book).calibrate_button.isEnabled()  # aucun home trainer en service
    paused = []
    sim = SimulatedTrainer()
    dialog = TrainerDialog(book, current=sim, before_calibration=lambda: paused.append(True))
    assert dialog.calibrate_button.isEnabled()

    wizard = CalibrationDialog(sim, "Home trainer simulé")
    assert "Démarrer" in wizard.instruction.text()
    wizard.start_button.click()
    assert sim.calibration_status().phase is CalibrationPhase.SPEED_UP
    assert wizard.instruction.text().startswith("Accélérez au-delà de 30 km/h")
    assert "cible 30 km/h" in wizard.speed.text() and not wizard.start_button.isEnabled()
    assert wizard.close_button.text() == "Annuler"
    wizard.reject()
    assert sim.calibration_status().message == "calibration annulée"


def test_main_window_pauses_for_calibration(app, tmp_path):
    window = MainWindow(parse_workout("1m@100"), book=DeviceBook(tmp_path / "a.json"))
    window._toggle()
    assert window.session.state.name == "RUNNING"
    window._pause_for_calibration()
    assert window.session.state.name == "PAUSED"
    window.close()
