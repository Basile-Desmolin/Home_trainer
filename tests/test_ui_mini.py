"""Vignette au premier plan quand le logiciel passe en arrière-plan pendant une sortie (Qt hors écran).

Étape 10 : ses valeurs viennent du catalogue d'indicateurs, choisies au clic droit, mode par mode,
et gardées dans le profil."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.profiles import ProfileBook  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.chart import CADENCE, zone_color  # noqa: E402
from home_trainer.ui.indicators import Mode  # noqa: E402
from home_trainer.ui.mini import DEFAULTS, MAX_PLACES, MiniWindow, layout_key  # noqa: E402


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


def menu_actions(menu):
    return {a.text(): a for a in menu.actions() if a.text()}


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
    """Valeurs d'origine en séance : les mêmes qu'avant l'étape 10."""
    window.session.start()
    window.session.tick(15)
    window.background = True
    window._on_tick()
    mini = window.mini
    assert mini.keys == DEFAULTS[Mode.WORKOUT]
    (t0, v0), (t1, _), (t2, v2) = mini.texts()
    assert (t0, v0, t1, t2, v2) == ("CIBLE", "150 W", "PUISSANCE", "RESTE", "0:45")
    assert mini.places[0].value.styleSheet() == f"color: {zone_color(150, 250).name()};"
    assert "brique 1 / 2" in mini.status.text() and "séance 2:45" in mini.status.text()
    window.session.pause()
    window._on_tick()
    assert mini.status.text().startswith("En pause")


def test_free_ride_and_click_brings_back(window):
    window.enter_free_ride()
    window.free.start()
    window.background = True
    window._on_tick()
    mini = window.mini
    assert mini.keys == DEFAULTS[Mode.FREE]
    assert mini.texts()[0] == ("CIBLE", f"{window.free.target_w} W") and mini.texts()[2][0] == "TEMPS"
    assert mini.status.text() == ""
    assert mini.isVisible()
    mini.clicked.emit()
    assert not mini.isVisible()


def test_right_click_menu_chooses_adds_and_removes(window):
    """Clic droit sur une valeur : la remplacer, en ajouter une à droite, la retirer, revenir à l'origine."""
    window.session.start()
    window.session.tick(15)
    window.background = True
    window._on_tick()
    mini = window.mini
    menu = mini._menu(1)
    actions = menu_actions(menu)
    assert "Cible (ou pente)" in actions and "Reste de la brique" in actions
    assert "Reste" not in actions  # le reste en km n'existe qu'en parcours
    assert actions["Retirer cette valeur"].isEnabled() and not actions["Valeurs d'origine"].isEnabled()
    actions["Cadence"].trigger()  # la puissance devient la cadence
    assert mini.keys == ["target", "cadence", "brick_remaining"]
    assert mini.places[1].value.styleSheet() == f"color: {CADENCE};"
    # Ajout à droite de la 1re valeur (sous-menu), puis retrait de la dernière.
    more = menu_actions(menu_actions(mini._menu(0))["Ajouter une valeur à droite"].menu())
    assert "Cadence" not in more and "Cible (ou pente)" not in more  # déjà affichées
    more["Cardio"].trigger()
    assert mini.keys == ["target", "heart", "cadence", "brick_remaining"]
    assert mini.texts()[1] == ("CARDIO", "Off")
    menu_actions(mini._menu(3))["Retirer cette valeur"].trigger()
    assert mini.keys == ["target", "heart", "cadence"]
    # Choisir une valeur déjà affichée échange les deux places.
    menu_actions(mini._menu(0))["Cadence"].trigger()
    assert mini.keys == ["cadence", "heart", "target"]
    menu_actions(mini._menu(0))["Valeurs d'origine"].trigger()
    assert mini.keys == DEFAULTS[Mode.WORKOUT]


def test_between_one_and_five_values(app):
    mini = MiniWindow()
    for key in ("cadence", "heart", "speed"):  # ajoutés en fin de ligne ; « speed », 6e, est refusé
        mini.add(len(mini.keys) - 1, key)
    assert mini.keys == ["target", "power", "brick_remaining", "cadence", "heart"] and len(mini.keys) == MAX_PLACES
    more = menu_actions(mini._menu(0))["Ajouter une valeur à droite"].menu()
    assert not more.isEnabled()
    mini.add(0, "elapsed")  # au-delà de 5 : refusé
    assert len(mini.keys) == MAX_PLACES
    while len(mini.keys) > 1:
        mini.remove(0)
    assert not menu_actions(mini._menu(0))["Retirer cette valeur"].isEnabled()
    mini.remove(0)  # la dernière reste
    assert mini.keys == ["heart"]


def test_choice_is_kept_per_mode_in_the_profile(app, tmp_path):
    book = ProfileBook(tmp_path / "profils.json", rides_root=tmp_path / "sorties")
    profile = book.create("A")
    window = MainWindow(parse_workout("10m@200"), book=DeviceBook(tmp_path / "d.json"), profiles=book,
                        profile=profile)
    window.in_background = lambda: True
    window.show()
    window.session.start()
    window._on_tick()
    window.mini.add(2, "heart")  # séance : cible, puissance, reste, cardio
    workout = ["target", "power", "brick_remaining", "heart"]
    assert book.get(profile.id).layouts[layout_key(Mode.WORKOUT)] == workout
    window.session.pause()
    window.enter_free_ride()  # le mode libre garde ses valeurs d'origine
    window.free.start()
    window._on_tick()
    assert window.mini.keys == DEFAULTS[Mode.FREE]
    window.mini.choose(2, "cadence")
    saved = ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id).layouts
    assert saved == {layout_key(Mode.FREE): ["target", "power", "cadence"], layout_key(Mode.WORKOUT): workout}
    # Un autre profil, sans choix : valeurs d'origine ; retour au premier : son choix revient.
    window.set_profile(book.create("B"))
    assert window.mini.keys_for(Mode.FREE) == DEFAULTS[Mode.FREE]
    window.set_profile(profile)
    assert window.mini.keys_for(Mode.WORKOUT) == workout
    # Retour aux valeurs d'origine : la clé disparaît du profil.
    window.mini.reset()
    assert layout_key(Mode.FREE) not in book.get(profile.id).layouts
    window.close()


def test_profile_choice_read_back_and_bad_choice_ignored(app):
    mini = MiniWindow()
    mini.set_choices({Mode.WORKOUT: ["power", "heart"], Mode.FREE: ["brick_remaining"],  # n'existe pas en libre
                      Mode.ROUTE: ["power"] * 2})
    assert mini.keys_for(Mode.WORKOUT) == ["power", "heart"]
    assert mini.keys_for(Mode.FREE) == DEFAULTS[Mode.FREE] and mini.keys_for(Mode.ROUTE) == DEFAULTS[Mode.ROUTE]


def test_background_asks_windows_which_window_is_in_front(app, monkeypatch):
    """Bug vu sur le vélo : après un choix au clic droit, Qt croyait l'application active et cachait la
    vignette, fenêtre principale restée derrière. Sous Windows, on demande la fenêtre au premier plan."""
    import home_trainer.ui.app as app_module
    from home_trainer.ui.mini import foreground_window
    assert foreground_window() is None  # Qt hors écran : état tenu par Qt, comme avant
    w = MainWindow(parse_workout("1m@150"), 250)
    w.show()
    w.session.start()
    w._on_tick()
    front = {}
    monkeypatch.setattr(app_module, "foreground_window", lambda: front["value"])
    main = int(w.winId())
    mini = int(w.mini.winId())
    for value, background in (((main, True), False),  # fenêtre principale devant : vignette cachée
                              ((mini, True), True),  # vignette (et son menu) devant : arrière-plan
                              ((4242, True), False),  # autre fenêtre du logiciel (dialogue natif)
                              ((4242, False), True),  # autre application
                              ((0, False), True)):  # aucune fenêtre au premier plan
        front["value"] = value
        assert w.in_background() is background, value
    front["value"] = (mini, True)
    w._on_tick()
    assert w.mini.isVisible()  # après un choix dans le menu, la vignette reste
    front["value"] = (main, True)
    w._on_tick()
    assert not w.mini.isVisible()
    w.close()


def test_menu_open_keeps_the_mini_shown(window):
    window.session.start()
    window.background = True
    window._on_tick()
    window.mini.menu_open = True  # le menu peut rendre l'application active : la vignette ne se cache pas
    window.background = False
    window._on_tick()
    assert window.mini.isVisible()
    window.mini.menu_open = False
    window._on_tick()
    assert not window.mini.isVisible()
