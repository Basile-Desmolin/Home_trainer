"""Rapidité : redessins limités à ce qui change, fenêtre réduite, fermeture."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.power import Reading  # noqa: E402

TICK = 0.2


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Clock:
    def nsecsElapsed(self):  # noqa: N802 (API Qt)
        return int(TICK * 1e9)

    def restart(self):
        return 0


class Steady:
    name = "Constant"

    def set_target(self, target):
        pass

    def read(self, dt):
        return Reading(210.0, 90.0)


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(parse_workout("30m@200"), source=Steady(), book=DeviceBook(tmp_path / "d.json"))
    w.timer.stop()
    w.clock = Clock()
    yield w
    w.close()


def test_traced_curve_kept_up_to_date_draws_the_same_as_a_full_recompute(app):
    """Courbe lissée et réduite au pixel, prolongée mesure après mesure (avec des trous du capteur
    et des arrivées par paquets) : mêmes points qu'un calcul complet sur toute la courbe."""
    import math
    import random

    from home_trainer.ui.chart import TimeAxis, TracedCurve, smooth, trace
    from home_trainer.ui.session import Sample

    rnd = random.Random(7)
    run = []
    curve = TracedCurve(10)
    x = TimeAxis(40.0, 0.0, 0.37)  # ~2,7 s par pixel
    y = float  # valeurs telles quelles

    def full():
        return [[(round(q.x(), 6), round(q.y(), 6)) for q in line]
                for line in trace(smooth([(s.t, s.heart_rate_bpm or None) for s in run], 10), x, y)]

    def kept():
        return [[(round(q.x(), 6), round(q.y(), 6)) for q in line] for line in curve.lines(y)]

    t = 0
    while t < 1500:
        for _ in range(rnd.choice((1, 1, 1, 2, 5))):  # une mesure par seconde, parfois un paquet
            t += 1
            gap = 300 <= t < 312 or t == 700 or rnd.random() < 0.01
            run.append(Sample(t, 200, None, heart_rate_bpm=None if gap else 130 + 20 * math.sin(t / 50)
                              + rnd.uniform(-3, 3)))
        curve.follow(run, lambda s: s.heart_rate_bpm or None, x)
        if t % 97 < 5:
            assert kept() == full(), t
    assert kept() == full()
    values = [s.heart_rate_bpm for s in run if s.heart_rate_bpm]
    assert (curve.values, curve.low, curve.high) == (len(values), min(values), max(values))
    assert curve.last == values[-1]
    # Autre axe (fenêtre redimensionnée) : tout est replacé.
    x = TimeAxis(40.0, 0.0, 0.5)
    curve.follow(run, lambda s: s.heart_rate_bpm or None, x)
    assert kept() == full()
    # Tronçon remplacé : on repart de zéro.
    run = run[:100]
    curve.follow(run, lambda s: s.heart_rate_bpm or None, x)
    assert kept() == full() and curve.n == 100


def test_free_ride_window_starts_where_the_full_scan_did():
    from home_trainer.ui.free_ride import window_samples
    from home_trainer.ui.session import Sample

    samples = [Sample(t, 200, None) for t in range(1, 3601)]
    for start in (-600, 0, 0.5, 1, 1799.5, 3000, 3600, 3601):
        assert window_samples(samples, start) == [x for x in samples if x.t >= start]
    assert window_samples([], 10) == []


def test_styles_and_icons_are_set_only_when_they_change(window):
    """Avant : style et icônes reposés à chaque tic (5 par seconde). Après : seulement s'ils changent."""
    calls = []
    for name in ("play_button", "erg_button"):
        widget = getattr(window, name)
        widget.setIcon = lambda icon, name=name: calls.append(name)
    for name in ("intensity_label", "current_label", "next_label", "zone_tag"):
        widget = getattr(window, name)
        original = widget.setStyleSheet
        widget.setStyleSheet = lambda sheet, name=name, original=original: (calls.append(name), original(sheet))
    window._toggle()
    window._on_tick()
    calls.clear()
    for _ in range(25):  # 5 s de séance : rien ne change d'aspect
        window._on_tick()
    assert calls == []
    window._toggle()  # pause : le bouton passe de « pause » à « lecture »
    window._on_tick()
    assert calls == ["play_button"]
    calls.clear()
    window.session.adjust_intensity(5)  # intensité à 105 % : le chiffre passe en couleur
    window._on_tick()
    window._on_tick()
    assert calls == ["intensity_label"]


def test_minimized_window_keeps_recording_but_skips_the_display(app, window):
    refreshed = []
    original = window._refresh
    window._refresh = lambda: (refreshed.append(window.session.elapsed_s), original())
    window.show()
    window._toggle()
    refreshed.clear()
    for _ in range(5):
        window._on_tick()
    assert len(refreshed) == 5
    window.showMinimized()
    app.processEvents()
    assert window.isMinimized()
    refreshed.clear()
    for _ in range(26):  # un peu plus de 5 s
        window._on_tick()
    assert refreshed == [] and len(window.session.samples) == 6  # mesures toujours enregistrées
    window.showNormal()
    app.processEvents()  # rouverte : remise à jour aussitôt, sans attendre le tic
    assert refreshed


class SlowToStop:
    """Capteur dont le fil met `delay_s` à s'arrêter (déconnexion radio lente)."""

    @staticmethod
    def make(delay_s, name="lent"):
        import threading
        import time

        from home_trainer.sensors import BackgroundSensor

        class Sensor(BackgroundSensor):
            def _run(self, stop: threading.Event) -> None:
                stop.wait()
                time.sleep(delay_s)

        sensor = Sensor()
        sensor.name = name
        sensor.start()
        return sensor


def test_sensors_stop_together_within_one_shared_timeout():
    import time

    from home_trainer.sensors import SensorState, stop_all

    sensors = [SlowToStop.make(0.6) for _ in range(3)]
    time.sleep(0.05)
    t0 = time.monotonic()
    stop_all(sensors + [None], timeout=3.0)
    took = time.monotonic() - t0
    assert took < 1.2  # ensemble : ~0,6 s (un par un : ~1,8 s)
    assert all(s.state is SensorState.STOPPED and not s.running for s in sensors)
    stuck = [SlowToStop.make(5.0) for _ in range(3)]  # capteurs qui ne répondent plus
    time.sleep(0.05)
    t0 = time.monotonic()
    stop_all(stuck, timeout=0.5)
    assert time.monotonic() - t0 < 1.0  # 0,5 s en tout, pas 0,5 s chacun


def test_closing_hides_the_window_and_frees_the_sensors_before_waiting_for_the_upload(app, window):
    seen = []
    window.heart_rate = SlowToStop.make(0.3, "cardio")
    window.power_meter = SlowToStop.make(0.3, "puissance")
    window.show()
    window.send_rides = lambda wait_s=0, **kw: seen.append(
        (window.isHidden(), window.heart_rate.running, window.power_meter.running, wait_s))
    window.close()
    assert seen == [(True, False, False, 20)]
    """Avant : un redessin à chaque tic (5 par seconde). Après : à chaque nouvelle mesure (1 par
    seconde), ou quand le curseur change de pixel, la brique, l'intensité ou l'état."""
    window.chart.resize(640, 300)
    asked = []
    window.chart.update = lambda *a: asked.append(window.session.elapsed_s)
    window._toggle()
    for _ in range(51):  # un peu plus de 10 s (50 × 0,2 s = 9,999… s)
        window._on_tick()
    assert len(window.session.samples) == 10
    assert 10 <= len(asked) <= 13  # 10 mesures, et le curseur passe 1 ou 2 fois à un autre pixel
    asked.clear()
    window._toggle()  # pause : plus rien ne bouge (un redessin pour l'état)
    for _ in range(25):
        window._on_tick()
    assert len(asked) == 1
    window.session.adjust_intensity(5)
    window._on_tick()
    assert len(asked) == 2  # intensité changée : la consigne ajustée est redessinée
