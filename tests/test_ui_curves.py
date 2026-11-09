"""Courbes de la séance et du mode libre : watts gradués, étiquettes au bout, courbes au choix (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.profiles import ProfileBook  # noqa: E402
from home_trainer.ui import chart  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.session import FreeRideSession  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_scales_leave_room_above_the_highest_value():
    assert chart.curve_scale("cadence", [88, 95]) == (0, 120)
    assert chart.curve_scale("cadence", [131]) == (0, 140)
    assert chart.curve_scale("speed", [31.4, 38]) == (0, 50)
    assert chart.curve_scale("speed", [52]) == (0, 60)
    assert chart.curve_scale("heart", [140, 172]) == (60, 200)
    assert chart.curve_scale("heart", [50, 199]) == (45, 204)


def test_end_labels_do_not_overlap(app):
    image = QImage(400, 200, QImage.Format_ARGB32)
    p = QPainter(image)
    drawn = []
    p.drawText = lambda rect, _flags, text: drawn.append((rect.center().y(), text))
    area = QRectF(40, 10, 300, 180)
    chart.draw_end_labels(p, area, [(100.0, "88 tr/min", chart.CADENCE), (104.0, "142 bpm", chart.HEART),
                                    (189.0, "31,0 km/h", chart.SPEED)])
    p.end()
    ys = [y for y, _ in drawn]
    assert [t for _, t in drawn] == ["88 tr/min", "142 bpm", "31,0 km/h"]
    assert all(b - a >= 15 for a, b in zip(ys, ys[1:]))  # une ligne chacune
    assert ys[-1] <= area.bottom()


def test_curves_are_chosen_and_kept_in_the_profile(app, tmp_path):
    book = ProfileBook(tmp_path / "profils.json", rides_root=tmp_path / "sorties")
    profile = book.create("A")
    window = MainWindow(parse_workout("10m@200"), book=DeviceBook(tmp_path / "d.json"), profiles=book,
                        profile=profile)
    assert window.chart.curves == {"cadence", "heart"} == window.free_panel.chart.curves
    menu = window._curves_menu()
    assert [(a.text(), a.isChecked()) for a in menu.actions() if a.isCheckable()] == [
        ("Cadence", True), ("Cardio", True), ("Vitesse", False)]
    next(a for a in menu.actions() if a.text() == "Vitesse").trigger()
    assert window.chart.curves == {"cadence", "heart", "speed"} == window.free_panel.chart.curves
    loaded = ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id)
    assert loaded.chart_curves == ["cadence", "heart", "speed"]
    window.set_curves({"heart", "inconnue"})
    assert window.chart.curves == {"heart"}
    window.set_curves(chart.DEFAULT_CURVES)  # retour au choix d'origine : rien de gardé
    assert ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id).chart_curves is None
    window.close()


def test_charts_draw_every_curve(app):
    """Rendu hors écran avec les trois courbes : pas d'erreur, y compris sans mesure."""
    free = FreeRideSession(250)
    widget = chart.WorkoutChart()
    widget.resize(800, 300)
    widget.curves = {"cadence", "heart", "speed"}
    window = MainWindow(parse_workout("2m@200"))
    widget.set_session(window.session)
    widget.grab()  # séance sans mesure
    window.session.start()
    for i in range(90):
        window.session.tick(1)
        window.session.record(200 + i % 7, 88, 140 + i % 9, 31.0 + i % 3, at=1.7e9 + i)
    assert not widget.grab().isNull()
    panel = window.free_panel.chart
    panel.resize(800, 300)
    panel.curves = {"speed"}
    panel.set_session(free)
    free.start()
    for i in range(30):
        free.tick(1)
        free.record(180, 90, None, 30.0, at=1.7e9 + i)
    assert not panel.grab().isNull()
    window.close()
