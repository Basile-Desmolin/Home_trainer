"""Étape 7 : vitesse calculée par l'application d'après la puissance (ERG compris), avec l'élan du vélo.

Avant : en séance, la vitesse était celle du home trainer ; avec un pignon unique (Zwift Cog) en ERG, elle
ne suivait que la cadence (sortie du 7 novembre 2026 : ~19 km/h à 129 W). Le parcours rejoignait la vitesse
d'équilibre avec une inertie fixe de 4 s : roue libre sur le plat depuis 31,7 km/h, 0,2 km/h après 20 s.
Après : mouvement du vélo (poussée P / v, roulement, pente, traînée, masse cycliste + vélo), partout.
"""

import os

import pytest

from home_trainer.sensors.trainer import Slope
from home_trainer.ui.power import Reading, next_speed_kmh, road_speed_kmh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FLAT = Slope(0.0, 75.0)  # 75 kg de cycliste + 9 kg de vélo


def ride(speed_kmh, seconds, power_w, slope, dt=0.2):
    for _ in range(round(seconds / dt)):
        speed_kmh = next_speed_kmh(speed_kmh, dt, power_w, slope)
    return speed_kmh


def test_steady_speed_is_the_road_speed():
    assert ride(0, 600, 200, FLAT) == pytest.approx(road_speed_kmh(200, FLAT), abs=0.05)  # 31,5 km/h
    assert ride(0, 600, 129, FLAT) == pytest.approx(road_speed_kmh(129, FLAT), abs=0.05)


def test_standing_start_takes_tens_of_seconds():
    """200 W départ arrêté : 14,2 km/h à 4 s, 27,5 km/h à 20 s (avant : 31,5 km/h à 20 s)."""
    assert ride(0, 4, 200, FLAT) == pytest.approx(14.2, abs=0.3)
    assert ride(0, 20, 200, FLAT) == pytest.approx(27.5, abs=0.3)


def test_coasting_on_the_flat_slows_down_slowly():
    """Roue libre depuis 31,7 km/h : 18,7 km/h à 20 s, 7,5 km/h à 60 s (avant : 0,2 km/h à 20 s)."""
    assert ride(31.7, 20, 0, FLAT) == pytest.approx(18.7, abs=0.3)
    assert ride(31.7, 60, 0, FLAT) == pytest.approx(7.5, abs=0.3)
    assert ride(31.7, 180, 0, FLAT) == 0


def test_coasting_uphill_stops_quickly_and_downhill_speeds_up():
    assert ride(15, 4, 0, Slope(5.0, 75.0)) == pytest.approx(7.0, abs=0.3)
    assert ride(15, 10, 0, Slope(5.0, 75.0)) == 0  # pas de recul
    down = Slope(-3.0, 75.0)
    assert ride(25, 10, 0, down) == pytest.approx(28.2, abs=0.3)
    assert ride(25, 180, 0, down) == pytest.approx(road_speed_kmh(0, down), abs=0.1)  # 33,0 km/h


def test_weight_slows_acceleration_not_flat_top_speed_much():
    light, heavy = Slope(0.0, 60.0), Slope(0.0, 100.0)
    assert ride(0, 10, 200, heavy) < ride(0, 10, 200, light) - 2
    assert ride(0, 600, 200, heavy) == pytest.approx(ride(0, 600, 200, light), abs=1.0)  # roulement seul
    assert Slope(0.0, 75.0, 12.0).total_kg == 87.0  # le poids du vélo compte


def test_a_frozen_interface_gives_the_same_speed():
    assert next_speed_kmh(20, 5.0, 250, FLAT) == pytest.approx(ride(20, 5.0, 250, FLAT), abs=0.05)
    assert next_speed_kmh(20, 0, 250, FLAT) == 20


# --- dans la fenêtre ------------------------------------------------------------------------------

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.route import Route  # noqa: E402
from home_trainer.sync import AccountBook, Outbox  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.session import FreeMode, State  # noqa: E402


class Clock:
    """200 ms par tic, lu comme un `QElapsedTimer`."""

    def nsecsElapsed(self):  # noqa: N802 (nom de l'API Qt)
        return 200_000_000

    def restart(self):
        return 200


class CogTrainer:
    """Home trainer en ERG avec un pignon unique : 129 W, et une vitesse qui ne suit que la cadence."""
    name = "Cog"

    def __init__(self):
        self.reading: Reading | None = Reading(129, 85, 19.0)
        self.target = None

    def set_target(self, target):
        self.target = target

    def read(self, dt):
        return self.reading


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(parse_workout("20m@129"), 250, source=CogTrainer(), accounts=AccountBook(tmp_path / "c.json"),
                   outbox=Outbox(tmp_path / "sorties"))
    w.timer.stop()
    w.clock = Clock()
    yield w
    w.close()


def ticks(window, seconds):
    for _ in range(round(seconds / 0.2)):
        window._on_tick()


def test_erg_session_speed_follows_power_not_the_trainer(window):
    """129 W en ERG : 26,9 km/h (avant : 19 km/h, la vitesse du home trainer)."""
    assert window.session.erg
    window._toggle()
    ticks(window, 120)
    speed = window._ride_view().speed_kmh
    assert speed == pytest.approx(26.9, abs=0.1)
    assert speed == pytest.approx(road_speed_kmh(129, Slope(0.0, window.free.rider_kg)), abs=0.1)
    assert window.session.samples[-1].speed_kmh == pytest.approx(speed, abs=0.1)  # enregistrée = affichée


def test_free_ride_erg_shows_the_computed_speed(window):
    window.enter_free_ride()
    window.set_free_mode(FreeMode.ERG)
    window._toggle()
    ticks(window, 30)
    speed = window._ride_view().speed_kmh
    assert speed is not None and 20 < speed < 26.9  # en route vers 26,9 km/h
    assert window.free.samples[-1].speed_kmh == pytest.approx(speed, abs=0.3)


def test_pause_stops_and_lost_signal_coasts(window):
    window._toggle()
    ticks(window, 60)
    before = window.road_kmh
    window.source.reading = None  # signal perdu : roue libre
    ticks(window, 10)
    assert 0 < window.road_kmh < before - 3
    window._toggle()  # pause
    window._on_tick()
    assert window.road_kmh == 0


def test_the_trainer_speed_can_be_chosen(window, tmp_path):
    from PySide6.QtWidgets import QMenu

    from home_trainer.profiles import SPEED_APP, SPEED_TRAINER, ProfileBook
    book = ProfileBook(tmp_path / "profils.json")
    profile = book.create("Adrien")
    window.profiles = book
    window.set_profile(profile)
    assert window.speed_source == SPEED_APP  # par défaut : vitesse calculée
    window._toggle()
    ticks(window, 5)
    menu = QMenu(window)
    window._card_menu("speed", menu)
    actions = menu.actions()[0].menu().actions()
    assert [a.isChecked() for a in actions] == [True, False]
    assert actions[1].text() == "Celle du home trainer"  # elle est envoyée
    actions[1].trigger()
    assert window.speed_source == SPEED_TRAINER
    assert ProfileBook.load(tmp_path / "profils.json").profiles[0].speed_source == SPEED_TRAINER
    ticks(window, 5)
    assert window._ride_view().speed_kmh == 19.0  # vitesse du home trainer (ERG : sans braquet)
    assert window.session.samples[-1].speed_kmh == 19.0  # enregistrée = affichée
    window.source.reading = Reading(129, 85)  # le home trainer n'envoie plus de vitesse : vitesse calculée
    ticks(window, 1)
    assert window._ride_view().speed_kmh == pytest.approx(window.road_kmh, abs=0.01) != 19.0
    menu = QMenu(window)
    window._card_menu("cadence", menu)  # la carte cadence montre la vitesse quand sa carte n'est pas là
    assert "non envoyée" in menu.actions()[0].menu().actions()[1].text()


def test_route_ignores_the_trainer_speed(window):
    from home_trainer.profiles import SPEED_TRAINER
    window.set_speed_source(SPEED_TRAINER)
    window.ride_route(Route.from_coordinates("Plat", [(45.0, 6.0, 500.0), (45.05, 6.0, 500.0)]))
    window._toggle()
    ticks(window, 120)
    assert window._ride_view().speed_kmh == pytest.approx(26.9, abs=0.2)  # la distance en dépend


def test_route_speed_coasts_when_the_signal_is_lost(window):
    route = Route.from_coordinates("Plat", [(45.0, 6.0, 500.0), (45.05, 6.0, 500.0)])
    window.ride_route(route)
    window._toggle()
    ticks(window, 60)
    assert window.route.state is State.RUNNING
    before = window.route.speed_kmh
    window.source.reading = None
    ticks(window, 10)
    assert 0 < window.route.speed_kmh < before - 3  # avant : figée à sa dernière valeur
