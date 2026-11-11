"""Parcours GPX dans la fenêtre principale (Qt hors écran, home trainer simulé)."""

import math
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.sensors import SensorState, Slope  # noqa: E402
from home_trainer.route import Route  # noqa: E402
from home_trainer.ui.app import CHIP_MAX_PX, MainWindow  # noqa: E402
from home_trainer.ui.route_ride import RouteMap, RouteProfile  # noqa: E402
from home_trainer.ui.session import RouteSession, State  # noqa: E402
from home_trainer.ui.theme import ElidedLabel  # noqa: E402

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
    assert window.route_panel.cards["route_remaining"].unit.text() == "km"
    window.grab()  # dessine profils et carte
    window.leave_free_ride()
    assert window.pages.currentIndex() == 0 and window.route.state is State.PAUSED
    window.close()


def test_erg_button_switches_the_workout_to_free_resistance(app, tmp_path):
    source = Recorder()
    window = MainWindow(parse_workout("10m@150"), source=source, book=DeviceBook(tmp_path / "a.json"))
    window.weight_box.setValue(70)
    window._toggle()
    window._push_target()
    assert source.targets[-1] == 150
    window.erg_button.click()
    assert not window.session.erg and source.targets[-1] == Slope(0.0, 70)
    assert window.erg_button.text() == "ERG off" and "à la main" in window.m_target.sub.text()
    assert window.session.state is State.RUNNING
    window.load(window.session.workout, end_ride=False)  # nouvelle séance : le réglage est gardé
    assert not window.session.erg
    window.toggle_erg()
    window._toggle()
    window._push_target()
    assert source.targets[-1] == 150 and window.erg_button.isChecked()
    window.close()


def wiggly_route(n):
    """Boucle de `n` points (un tous les ~10 m) qui ondule : un long parcours réaliste pour la carte."""
    coords = []
    for i in range(n):
        t = i / n * 2 * math.pi
        r = n * 10 / (2 * math.pi) / 111_000
        lat = 45 + r * math.sin(t) + 0.0005 * math.sin(40 * t)
        coords.append((lat, 6 + r * math.cos(t) / math.cos(math.radians(45)), 500 + 300 * math.sin(3 * t)))
    return Route.from_coordinates("boucle", coords)


def old_map_point(route, width, height, lat, lon):
    """Pixel d'un point tel que le calculait l'ancienne carte (référence)."""
    points = route.points
    k = math.cos(math.radians(sum(pt.lat for pt in points) / len(points)))
    xs = [pt.lon * k for pt in points]
    ys = [pt.lat for pt in points]
    w, h = (max(xs) - min(xs)) or 1e-9, (max(ys) - min(ys)) or 1e-9
    scale = min((width - 24) / w, (height - 24) / h)
    ox, oy = (width - w * scale) / 2, (height - h * scale) / 2
    return ox + (lon * k - min(xs)) * scale, height - oy - (lat - min(ys)) * scale


def test_route_map_of_a_long_route_is_drawn_quickly_at_the_same_place(app):
    route = wiggly_route(20_000)
    session = RouteSession(route, 250, 75)
    session.distance_m = route.total_m * 0.37
    chart = RouteMap()
    chart.resize(320, 260)
    chart.set_session(session)
    t = time.perf_counter()
    chart.grab()  # premier dessin : tracé mis en pixels et simplifié
    first = time.perf_counter() - t
    t = time.perf_counter()
    for _ in range(5):
        chart.grab()
    following = (time.perf_counter() - t) / 5
    assert first < 0.5 and following < 0.05  # auparavant : environ 20 s par dessin
    assert 2 < len(chart._trace_xy) < 3000  # simplifié au pixel, au lieu de 20 000 points
    # Départ, arrivée et position aux mêmes pixels qu'avant.
    for lat, lon in ((route.points[0].lat, route.points[0].lon), (route.points[-1].lat, route.points[-1].lon),
                     route.position_at(session.distance_m)):
        q = chart.map_point(lat, lon)
        assert (q.x(), q.y()) == pytest.approx(old_map_point(route, 320, 260, lat, lon), abs=1e-6)
    chart.resize(500, 400)  # nouvelle taille : le tracé est refait
    chart.grab()
    q = chart.map_point(route.points[0].lat, route.points[0].lon)
    assert (q.x(), q.y()) == pytest.approx(old_map_point(route, 500, 400, route.points[0].lat,
                                                         route.points[0].lon), abs=1e-6)


def test_full_profile_is_kept_and_only_the_done_part_moves(app):
    route = wiggly_route(20_000)
    session = RouteSession(route, 250, 75)
    chart = RouteProfile()
    chart.resize(1250, 190)
    chart.set_session(session)
    session.distance_m = route.total_m * 0.3
    before = chart.grab().toImage()
    t = time.perf_counter()
    for _ in range(5):
        chart.grab()
    assert (time.perf_counter() - t) / 5 < 0.01  # sans les images gardées : 15 à 30 ms par dessin
    frame = chart._frame
    session.distance_m = route.total_m * 0.6
    after = chart.grab().toImage()
    assert chart._frame is frame  # même parcours, même taille : rien n'est recalculé
    # Le terrain entre les deux positions est passé de vif à estompé ; avant la première, inchangé.
    ground = round(frame.area.bottom()) - 2
    x_between, x_done = round(frame.x(route.total_m * 0.45)), round(frame.x(route.total_m * 0.1))
    assert before.pixel(x_between, ground) != after.pixel(x_between, ground)
    assert before.pixel(x_done, ground) == after.pixel(x_done, ground)
    chart.resize(900, 190)  # nouvelle taille : les images sont refaites
    chart.grab()
    assert chart._frame is not frame and chart._frame.area.width() == 900 - 48 - 12


class Searching:
    """Capteur qui cherche encore (au lancement, sans pédaler) : son état est long."""

    def __init__(self, name):
        self.name = name
        self.state = SensorState.SEARCHING

    @property
    def status(self):
        return f"{self.state.value} : aucun capteur à portée, pédalez pour le réveiller"

    def latest(self):
        return None


def test_long_sensor_states_do_not_widen_the_window(app, tmp_path):
    """Écran de 1 600 px à 200 % : des états de capteurs longs élargissaient le bandeau et les cartes
    au-delà de l'écran, et le parcours était coupé à droite."""
    window = MainWindow(parse_workout("10m@150"), book=DeviceBook(tmp_path / "a.json"))
    assert window.open_path(str(EXAMPLE))
    window.resize(1600, 950)
    window.show()
    app.processEvents()
    bar = window.findChild(QtWidgets.QToolBar)
    calm_window, calm_bar = window.minimumSizeHint().width(), bar.sizeHint().width()

    window.power_meter = Searching("Assioma")
    window.heart_rate = Searching("Cardio Bluetooth C0:7F:BA:95:3C:08")
    window._refresh()
    app.processEvents()
    # Pastilles : l'état seul, le détail dans la bulle.
    assert window.power_chip.text() == "Puissance : recherche…"
    assert window.power_chip.toolTip().startswith("recherche… : aucun capteur à portée")
    assert window.heart_chip.text() == "Cardio : recherche…"
    # La ligne de la carte Cardio garde son texte entier mais n'impose pas sa largeur.
    sub = window.route_panel.cards["heart"].sub
    assert sub.text() == "Cardio Bluetooth C0:7F:BA:95:3C:08 : recherche… : aucun capteur à portée, pédalez pour le réveiller"
    assert sub.minimumSizeHint().width() == 0
    assert window.minimumSizeHint().width() == calm_window
    # Bandeau : deux pastilles de plus (puissance, cardio en recherche), chacune de largeur bornée.
    assert bar.sizeHint().width() <= calm_bar + 2 * (CHIP_MAX_PX + 60)
    window.power_meter = window.heart_rate = None  # capteurs factices : rien à arrêter à la fermeture
    window.close()


def test_elided_label_cuts_long_text_and_shows_it_whole_in_the_tooltip(app):
    label = ElidedLabel("court")
    label.resize(400, 20)
    label.show()  # un widget masqué ne reçoit pas ses changements de taille
    assert label.toolTip() == ""
    long = "Cardio Bluetooth C0:7F:BA:95:3C:08 : recherche… : aucun capteur à portée, pédalez pour le réveiller"
    label.setText(long)
    assert label.text() == long and label.toolTip() == long
    shown = QtWidgets.QLabel.text(label)  # texte affiché
    assert shown.endswith("…") and label.fontMetrics().horizontalAdvance(shown) <= 400
    label.resize(4000, 20)  # assez de place : texte entier, plus de bulle
    assert QtWidgets.QLabel.text(label) == long and label.toolTip() == ""
    label.close()
