"""Puissance affichée : instantanée ou moyenne sur 3, 5, 10, 30 s ; l'enregistrement garde la valeur brute."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.profiles import ProfileBook  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.power import PowerAverage, Reading  # noqa: E402

TICK = 0.2  # 5 mesures par seconde


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_moving_average_over_the_last_seconds():
    avg = PowerAverage()
    assert avg.average(3) is None
    # 2 s à 200 W (10 mesures) puis 1 s à 300 W (5 mesures) : sur 3 s, (10 × 200 + 5 × 300) / 15 = 233,3 W.
    for p in [200] * 10 + [300] * 5:
        avg.feed(TICK, p)
    assert avg.average(3) == pytest.approx(233.33, abs=0.01)
    assert avg.average(1) == pytest.approx(300)  # la dernière seconde
    # 3 s de plus à 300 W : les mesures à 200 W sortent de la fenêtre de 3 s, pas de celle de 30 s.
    for _ in range(15):
        avg.feed(TICK, 300)
    assert avg.average(3) == pytest.approx(300)
    assert avg.average(30) == pytest.approx((10 * 200 + 20 * 300) / 30)  # 266,7 W
    avg.feed(TICK, None)  # tic sans mesure : on repart de zéro
    assert avg.average(30) is None


def test_old_readings_are_dropped_after_30_seconds():
    avg = PowerAverage()
    for _ in range(5 * 60):  # une minute
        avg.feed(TICK, 250)
    assert len(avg._items) <= 30 / TICK + 1


class Clock:
    """Horloge de la boucle : chaque tic dure 200 ms."""

    def nsecsElapsed(self):  # noqa: N802 (API Qt)
        return int(TICK * 1e9)

    def restart(self):
        return 0


class Steps:
    """Home trainer qui donne 200 W puis 300 W, une mesure par lecture."""

    name = "Paliers"

    def __init__(self):
        self.powers = iter([200.0] * 10 + [300.0] * 5)

    def set_target(self, target):
        pass

    def read(self, dt):
        return Reading(next(self.powers, 300.0), 90.0)


def test_the_power_card_shows_the_chosen_average_and_the_ride_keeps_raw_power(app, tmp_path):
    book = ProfileBook(tmp_path / "profils.json", rides_root=tmp_path / "sorties")
    profile = book.create("A")
    window = MainWindow(parse_workout("10m@200"), source=Steps(), book=DeviceBook(tmp_path / "d.json"),
                        profiles=book, profile=profile)
    window.timer.stop()
    window.clock = Clock()
    window._toggle()
    for _ in range(15 + 5):  # 2 s à 200 W, 1 s à 300 W, puis 1 s à 300 W
        window._on_tick()
    card = window.cards["power"]
    assert (card.title.text(), card.value.text()) == ("PUISSANCE", "300")  # instantanée par défaut
    window.set_smoothing(3)
    # 3 dernières secondes : 5 × 200 + 10 × 300 sur 15 mesures = 266,7 W.
    assert (card.title.text(), card.value.text()) == ("PUISSANCE · 3 S", "267")
    window.mini.refresh(window._ride_view())
    assert ("PUISSANCE · 3 S", "267 W") in window.mini.texts()
    # Enregistrement : une mesure par seconde, moyenne de ses 5 mesures brutes (pas la moyenne sur 3 s).
    assert [x.power_w for x in window.session.samples] == pytest.approx([200, 200, 300, 300], abs=0.01)
    assert ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id).power_smoothing_s == 3
    menu = window.workout_grid._menu(0)  # clic droit sur la carte puissance
    assert [a.text() for a in menu.actions() if a.isChecked()] == ["Puissance"]
    sub = next(a.menu() for a in menu.actions() if a.text() == "Puissance affichée")
    assert [a.text() for a in sub.actions() if a.isChecked()] == ["Moyenne sur 3 s"]
    assert all(a.text() != "Puissance affichée" for a in window.workout_grid._menu(1).actions())  # carte cadence
    window.set_smoothing(7)  # valeur inconnue (profil abîmé) : instantanée
    assert window.smoothing_s == 0 and card.title.text() == "PUISSANCE"
    window.close()
