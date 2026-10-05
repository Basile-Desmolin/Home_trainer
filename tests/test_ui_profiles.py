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
