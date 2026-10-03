"""Fin de sortie dans la fenêtre : .fit enregistré, fenêtre des comptes (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.sync import AccountBook, Outbox  # noqa: E402
from home_trainer.ui.accounts import AccountsDialog  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.session import State  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(parse_workout("2m@150"), 250, accounts=AccountBook(tmp_path / "comptes.json"),
                   outbox=Outbox(tmp_path / "sorties"))
    yield w
    w.close()


def pedal(session, seconds, start=1_759_480_000):
    session.start()
    for i in range(seconds):
        session.record(150, 90, 130, 30.0, at=start + i)
        session.tick(1)


def test_finished_workout_is_saved_once(window):
    pedal(window.session, 120)
    assert window.session.state is State.FINISHED
    window._on_tick()
    window._on_tick()
    files = list(window.outbox.dir.glob("*.fit"))
    assert len(files) == 1
    entry = window.outbox.entries()[files[0].name]
    assert entry["title"] == window.session.workout.name


def test_finish_button_saves_free_ride_and_resets(window):
    window.enter_free_ride()
    pedal(window.free, 90)
    old = window.free
    window.free_panel.finish_button.click()
    assert window.free is not old and window.free.samples == []
    [entry] = window.outbox.entries().values()
    assert entry["title"] == "Mode libre"


def test_short_ride_is_not_saved(window):
    pedal(window.session, 30)
    window.finish_button.click()
    assert not window.outbox.dir.exists() or not list(window.outbox.dir.glob("*.fit"))
    assert window.session.samples == []


def test_speed_is_estimated_without_trainer_speed(window):
    from home_trainer.ui.power import Reading
    assert 30 < window._speed(Reading(200, 90)) < 40
    assert window._speed(Reading(200, 90, 25.0)) == 25.0


def test_accounts_dialog_keeps_credentials(window):
    dialog = AccountsDialog(window.accounts, window.outbox, window)
    box = dialog.boxes["strava"]
    box.client_id.setText(" 12345 ")
    box.client_secret.setText("abc")
    dialog.reject()
    again = AccountBook.load(window.accounts.path)
    assert again["strava"].client_id == "12345" and not again["strava"].connected
    assert dialog.boxes["nolio"].connect_button.text() == "Se connecter"


def test_finish_button_saves_route_ride(window):
    from home_trainer.route import Route
    route = Route.from_coordinates("Col test", [(45.0, 6.0, 500.0), (45.01, 6.0, 600.0), (45.02, 6.0, 650.0)])
    window.ride_route(route)
    pedal(window.route, 90)
    window.route_panel.finish_button.click()
    [entry] = window.outbox.entries().values()
    assert entry["title"] == "Col test" and "arrêté avant l'arrivée" in entry["description"]
    assert window.route.samples == []
