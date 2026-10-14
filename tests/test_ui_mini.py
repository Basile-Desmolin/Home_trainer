"""Vignette au premier plan quand le logiciel passe en arrière-plan pendant une sortie (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app):
    w = MainWindow(parse_workout("1m@150 2m@300"), 250)
    w.background = False
    w.in_background = lambda: w.background
    w.show()
    yield w
    w.close()


def test_mini_shows_only_in_background_during_a_ride(window):
    window.background = True
    window._on_tick()
    assert not window.mini.isVisible()  # pas encore démarré
    window.session.start()
    window.session.tick(15)
    window._on_tick()
    assert window.mini.isVisible()
    window.background = False
    window._on_tick()
    assert not window.mini.isVisible()


def test_mini_shows_target_power_and_remaining(window):
    window.session.start()
    window.session.tick(15)
    info = window.mini_info()
    assert info.target == "150 W"
    assert info.remaining == "0:45"
    assert "brique 1 / 2" in info.status
    window.session.pause()
    assert window.mini_info().status.startswith("En pause")


def test_free_ride_and_click_brings_back(window):
    window.enter_free_ride()
    window.free.start()
    window.background = True
    window._on_tick()
    info = window.mini_info()
    assert info.target == f"{window.free.target_w} W" and info.remaining_title == "DURÉE"
    assert window.mini.isVisible()
    window.mini.clicked.emit()
    assert not window.mini.isVisible()
