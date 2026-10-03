"""Fenêtre « Bibliothèque » (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore  # noqa: E402

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.formats import save_workout  # noqa: E402
from home_trainer.library import Library  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.library import COL_DURATION, LibraryDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "Séances"
    (folder / "Endurance").mkdir(parents=True)
    save_workout(parse_workout("90m@65%", name="Endurance longue"), folder / "Endurance" / "longue.zwo")
    save_workout(parse_workout("10m@50% 4x(4m@110% 3m@50%) 10m@50%", name="VO2 4x4"), folder / "vo2.zwo")
    save_workout(parse_workout("10m@50% 2x(20m@95% 5m@50%) 5m@50%", name="Seuil 2x20"), folder / "seuil.mrc")
    return Library(folder, tmp_path / "bibliotheque.json")


def test_lists_filters_and_sorts(app, library):
    dialog = LibraryDialog(library, 250)
    assert len(dialog.visible_entries()) == 3
    dialog.search.setText("vo2")
    assert [e.name for e in dialog.visible_entries()] == ["VO2 4x4"]
    assert dialog.selected_entry().name == "VO2 4x4" and dialog.ride_button.isEnabled()
    assert dialog.chart.session.workout.name == "VO2 4x4"
    dialog.search.setText("")
    dialog.duration_box.setCurrentIndex(3)  # 1 h 15 à 2 h
    assert [e.name for e in dialog.visible_entries()] == ["Endurance longue"]
    dialog.duration_box.setCurrentIndex(0)
    dialog.tree.sortByColumn(COL_DURATION, QtCore.Qt.AscendingOrder)
    assert [e.name for e in dialog.visible_entries()] == ["VO2 4x4", "Seuil 2x20", "Endurance longue"]
    dialog.search.setText("rien de tel")
    assert dialog.visible_entries() == [] and not dialog.ride_button.isEnabled()
    assert dialog.empty.isVisibleTo(dialog)


def test_ride_returns_chosen_file(app, library):
    dialog = LibraryDialog(library, 250)
    assert dialog.select_path(library.folder / "seuil.mrc")
    dialog._ride()
    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert dialog.chosen_path() == library.folder / "seuil.mrc"


def test_change_folder_is_saved_and_rescanned(app, library, tmp_path):
    dialog = LibraryDialog(library, 250)
    other = tmp_path / "Autres"
    other.mkdir()
    save_workout(parse_workout("30m@60%", name="Récup"), other / "recup.erg", ftp=250)
    dialog.set_folder(other)
    assert [e.name for e in dialog.visible_entries()] == ["Récup"]
    assert Library.load(library.path).folder == other


def test_main_window_saves_new_workouts_in_library(app, library):
    window = MainWindow(parse_workout("10m@100"), 250)
    window.directory = "/ailleurs"
    window.set_library(library)
    assert window.directory == str(library.folder)
    assert window.open_path(str(library.folder / "vo2.zwo"))
    assert window.session.workout.name == "VO2 4x4"
    window.close()
