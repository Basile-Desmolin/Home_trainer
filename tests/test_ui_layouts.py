"""Indicateurs au choix : clic droit sur une carte, choix gardé dans le profil (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.profiles import ProfileBook  # noqa: E402
from home_trainer.ui.app import WORKOUT_SLOTS, MainWindow  # noqa: E402
from home_trainer.ui.free_ride import SLOTS as FREE_SLOTS  # noqa: E402
from home_trainer.ui.indicators import Mode  # noqa: E402
from home_trainer.ui.metric import MetricGrid  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_choosing_an_indicator_replaces_or_swaps_places(app):
    grid = MetricGrid(FREE_SLOTS, Mode.FREE)
    seen = []
    grid.changed.connect(seen.append)
    assert grid.keys == ["power", "heart", "cadence", "elapsed"]
    grid.choose(3, "normalized_power")  # TEMPS remplacé par NP
    assert grid.keys == ["power", "heart", "cadence", "normalized_power"]
    assert list(grid.cards) == grid.keys and "elapsed" not in grid.cards
    grid.choose(0, "heart")  # déjà affiché : les deux places s'échangent
    assert grid.keys == ["heart", "power", "cadence", "normalized_power"]
    assert seen[-1] == grid.keys
    grid.reset()
    assert grid.keys == grid.defaults and seen[-1] is None


def test_the_menu_only_offers_indicators_of_the_screen(app):
    free = MetricGrid(FREE_SLOTS, Mode.FREE)
    assert "brick" not in free.choices() and "route_remaining" not in free.choices()
    assert "workout_remaining" in MetricGrid(WORKOUT_SLOTS, Mode.WORKOUT).choices()
    menu = free._menu(2)
    titles = [a.text() for a in menu.actions() if a.text()]
    checked = [a.text() for a in menu.actions() if a.isChecked()]
    assert checked == ["Cadence"] and titles[-1] == "Disposition d'origine"
    assert not menu.actions()[-1].isEnabled()  # déjà la disposition d'origine


def test_an_invalid_saved_choice_falls_back_to_the_original_layout(app):
    grid = MetricGrid(FREE_SLOTS, Mode.FREE)
    for keys in (["power"], ["power", "power", "cadence", "elapsed"], ["power", "heart", "cadence", "brick"],
                 ["power", "heart", "cadence", "inconnu"], "power"):
        grid.set_keys(keys)
        assert grid.keys == grid.defaults


def test_the_window_keeps_the_choice_in_the_profile(app, tmp_path):
    book = ProfileBook(tmp_path / "profils.json", rides_root=tmp_path / "sorties")
    profile = book.create("A")
    window = MainWindow(parse_workout("10m@200"), book=DeviceBook(tmp_path / "d.json"), profiles=book,
                        profile=profile)
    window.workout_grid.choose(0, "brick")  # la grande carte PUISSANCE devient BRIQUE
    assert "power" not in window.cards and "brick" in window.cards
    window._refresh()  # sans carte puissance : pas de teinte de zone, pas d'erreur
    assert ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id).layouts == {
        "séance": ["brick", "cadence", "heart", "workout_remaining"]}
    # Un autre profil, sans choix : disposition d'origine ; retour au premier : son choix revient.
    other = book.create("B")
    window.set_profile(other)
    assert window.workout_grid.keys == window.workout_grid.defaults
    window.set_profile(profile)
    assert window.workout_grid.keys[0] == "brick"
    window.workout_grid.reset()
    assert ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id).layouts == {}
    window.close()
