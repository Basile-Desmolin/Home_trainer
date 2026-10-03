"""Parcours GPX dans la fenêtre principale (Qt hors écran, home trainer simulé)."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.sensors import Slope  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.session import State  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "col-fictif.gpx"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Recorder:
    name = "Enregistreur"

    def __init__(self):
        self.targets = []

    def set_target(self, target):
        self.targets.append(target)

    def read(self, dt):
        return None


def test_opening_a_gpx_rides_it_and_sends_the_route_grade(app, tmp_path):
    source = Recorder()
    window = MainWindow(parse_workout("1m@100"), source=source, book=DeviceBook(tmp_path / "a.json"))
    window.weight_box.setValue(65)
    assert window.open_path(str(EXAMPLE))
    assert window.route_ride and window.active is window.route
    assert window.route.route.name == "Col fictif (exemple)"
    assert source.targets[-1] is None  # prêt, pas encore parti : résistance libre
    window._toggle()
    assert window.route.state is State.RUNNING
    window.route.distance_m = 8000  # en pleine montée
    window._push_target()
    sent = source.targets[-1]
    assert isinstance(sent, Slope) and sent.rider_kg == 65 and sent.grade_pct > 5
    window._step(-1)  # ↓ : difficulté −10 %
    assert window.route.difficulty_pct == 90
    assert source.targets[-1].grade_pct == pytest.approx(sent.grade_pct * 0.9, abs=0.1)
    window._refresh()
    assert "km" in window.route_panel.m_remaining.value.text()
    window.grab()  # dessine profils et carte
    window.leave_free_ride()
    assert window.pages.currentIndex() == 0 and window.route.state is State.PAUSED
    window.close()
