"""Fenêtre « Profil » et changement de profil dans la fenêtre principale (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.profiles import ProfileBook  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.profiles import ProfileDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def book(tmp_path):
    book = ProfileBook(tmp_path / "profils.json")
    book.create("Basile", 280, 72)
    book.last = book.create("Camille", 210, 58).id
    book.save()
    return book


def test_first_launch_creates_a_profile(app, tmp_path):
    book = ProfileBook(tmp_path / "profils.json")
    dialog = ProfileDialog(book, startup=True)
    dialog.name_edit.setText("Basile")
    dialog.name_edit.textEdited.emit("Basile")
    dialog.ftp_box.setValue(265)
    dialog.remember_box.setChecked(True)
    dialog.accept()
    loaded = ProfileBook.load(tmp_path / "profils.json")
    assert [(p.id, p.name, p.ftp) for p in loaded.profiles] == [("basile", "Basile", 265)]
    assert loaded.startup_profile().name == "Basile"


def test_hr_max_is_set_in_the_profile_dialog(app, book, tmp_path):
    dialog = ProfileDialog(book)  # Camille, sans FC max
    assert dialog.hr_max_box.text() == "non renseignée"
    dialog.hr_max_box.setValue(188)
    dialog.list.setCurrentRow(0)  # Basile : toujours sans FC max
    assert dialog.hr_max_box.text() == "non renseignée"
    dialog.list.setCurrentRow(1)
    assert dialog.hr_max_box.value() == 188
    dialog.accept()
    loaded = ProfileBook.load(tmp_path / "profils.json")
    assert [p.hr_max for p in loaded.profiles] == [None, 188]
    # Retour à « non renseignée » : plus de FC max.
    dialog = ProfileDialog(loaded)
    dialog.hr_max_box.setValue(dialog.hr_max_box.minimum())
    dialog.accept()
    assert ProfileBook.load(tmp_path / "profils.json").profiles[1].hr_max is None


def test_bike_weight_is_set_in_the_profile_dialog_and_used_by_the_window(app, book, tmp_path):
    import json

    from home_trainer.ui.session import FreeMode
    dialog = ProfileDialog(book)  # Camille
    assert dialog.bike_box.value() == 9.0  # par défaut
    dialog.bike_box.setValue(12.5)
    dialog.accept()
    loaded = ProfileBook.load(tmp_path / "profils.json")
    assert [p.bike_kg for p in loaded.profiles] == [9.0, 12.5]
    # Profil enregistré avant l'étape 7 (sans poids du vélo) : 9 kg.
    data = json.loads((tmp_path / "profils.json").read_text(encoding="utf-8"))
    del data["profiles"][1]["bike_kg"]
    (tmp_path / "profils.json").write_text(json.dumps(data), encoding="utf-8")
    assert ProfileBook.load(tmp_path / "profils.json").profiles[1].bike_kg == 9.0

    w = MainWindow(parse_workout("10m@200"), profiles=loaded, profile=loaded.profiles[1])
    assert w.bike_kg == 12.5 and w._road_slope().total_kg == 58 + 12.5
    w.enter_free_ride()
    w.set_free_mode(FreeMode.SLOPE)
    assert w.free.command.bike_kg == 12.5  # consigne de pente envoyée au home trainer
    w.session.toggle_erg()
    assert w.session.command.bike_kg == 12.5
    w.set_bike_kg(7.0)
    assert w.free.command.bike_kg == 7.0 and w.session.bike_kg == 7.0
    w.close()


def test_the_heart_card_shows_esie_zones_with_an_hr_max(app, book):
    from home_trainer.sensors import HeartRateReading

    w = MainWindow(parse_workout("10m@200"), profiles=book, profile=book.profiles[1])
    w._heart_view = lambda: (HeartRateReading(165), "")
    w._refresh()
    bar = w.cards["heart"].body.itemAt(0).widget()
    assert w.cards["heart"].sub.text() == "" and bar.isHidden()  # sans FC max : pas de zones
    book.profiles[1].hr_max = 190
    w._refresh()
    assert w.cards["heart"].sub.text() == "i3" and not bar.isHidden() and bar.zone == 2
    w.close()


def test_dialog_selects_last_and_cancel_keeps_book(app, book):
    dialog = ProfileDialog(book)
    assert dialog.selected().name == "Camille"
    dialog.ftp_box.setValue(300)
    dialog.delete_selected()
    dialog.reject()
    assert [(p.name, p.ftp) for p in book.profiles] == [("Basile", 280), ("Camille", 210)]


def test_duplicate_names_are_refused(app, book, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a: None)
    dialog = ProfileDialog(book)
    dialog.add_profile()
    dialog.name_edit.textEdited.emit("basile")
    dialog.accept()
    assert dialog.profile is None and len(book.profiles) == 2


def test_switching_profile_changes_ftp_weight_and_rides_folder(app, book):
    basile, camille = book.profiles
    w = MainWindow(parse_workout("2m@75%"), basile.ftp, profiles=book, profile=basile)
    try:
        assert w.ftp_box.value() == 280 and w.weight_box.value() == 72
        assert w.outbox.dir == book.rides_dir(basile)
        w.session.start()
        for i in range(10):
            w.session.record(200, 90, None, 30.0, at=1_759_480_000 + i)
            w.session.tick(1)
        w.set_profile(camille)
        assert list(book.rides_dir(basile).glob("*.fit"))  # la sortie reste à Basile
        assert w.session.samples == []
        assert w.ftp_box.value() == 210 and w.weight_box.value() == 58
        assert w.outbox.dir == book.rides_dir(camille)
        assert w.profile_action.text() == "Profil : Camille"
        w.ftp_box.setValue(220)
        w._ftp_changed()
        w.weight_box.setValue(59.5)
        loaded = ProfileBook.load(book.path)
        assert (loaded.profiles[1].ftp, loaded.profiles[1].weight_kg) == (220, 59.5)
        assert loaded.profiles[0].ftp == 280
    finally:
        w.close()
