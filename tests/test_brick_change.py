"""Changement de brique : compte à rebours et bip aux 3 dernières secondes, bilan de la brique finie."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.devices import DeviceBook  # noqa: E402
from home_trainer.profiles import ProfileBook  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.power import Reading  # noqa: E402
from home_trainer.ui.session import BrickSummary, WorkoutSession, brick_summary  # noqa: E402

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

    def __init__(self, watts):
        self.watts = watts

    def set_target(self, target):
        pass

    def read(self, dt):
        return Reading(self.watts, 90.0)


def test_brick_summary_text():
    w = WorkoutSession(parse_workout("1m@200 1m@100"), 250)
    w.start()
    for p in [190] * 30 + [210] * 30:
        w.record(p, 90)
        w.tick(1)
    s = brick_summary(w.samples, 0)
    assert s == BrickSummary(0, 60, 200.0, 200.0)
    assert s.text() == "Brique 1 finie : 1:00 · 200 W pour 200 W visés (100 %)"
    assert BrickSummary(2, 95, 281.4, None).text() == "Brique 3 finie : 1:35 · 281 W"
    assert brick_summary(w.samples, 5) is None


def test_last_second_of_a_brick_stays_in_its_lap(app, tmp_path):
    """Étape 8 : la mesure de la dernière seconde d'une brique allait à la brique suivante (enregistrée
    après le tic qui change de brique). Séance 1 min à 200 W puis 30 s à 150 W, mesures à 5 Hz :
    tours du .fit de 59 s et 31 s avant, 60 s et 30 s après ; la dernière seconde de la séance n'ouvre
    plus de tour à elle seule."""
    fit_sdk = pytest.importorskip("garmin_fit_sdk")
    from home_trainer.formats.fit_activity import encode_activity
    from home_trainer.ui.session import ride_points

    window = MainWindow(parse_workout("1m@200 30s@150"), source=Steady(210.0),
                        book=DeviceBook(tmp_path / "d.json"))
    window.timer.stop()
    window.clock = Clock()
    window._toggle()
    for _ in range(460):  # 92 s : la séance (90 s) est finie
        window._on_tick()
    samples = window.session.samples
    assert len(samples) == 90 and [x.t for x in samples] == list(range(1, 91))
    assert [x.lap for x in samples] == [0] * 60 + [1] * 30
    assert (samples[59].target_w, samples[60].target_w, samples[-1].target_w) == (200, 150, 150)
    assert (samples[59].position_s, samples[-1].position_s) == (60, 90)
    msgs, errors = fit_sdk.Decoder(fit_sdk.Stream.from_byte_array(bytearray(
        encode_activity(ride_points(samples))))).read()
    assert errors == []
    assert [lap["total_timer_time"] for lap in msgs["lap_mesgs"]] == [60, 30]
    assert brick_summary(samples, 0).seconds == 60
    window.close()


def test_skipped_brick_keeps_the_seconds_before_the_skip(app):
    """Brique passée à la main (N) au milieu d'une seconde : la seconde va à la brique où elle a
    surtout été roulée (celle du milieu de la seconde)."""
    w = WorkoutSession(parse_workout("10m@200 5m@150 5m@100"), 250)
    w.start()
    for _ in range(5):
        w.tick(1)
        w.record(200, t=w.elapsed_s)
    w.tick(0.7)
    w.next_step()  # à 5,7 s : la seconde 6 (5 à 6 s) a surtout été roulée dans la brique 1
    w.tick(0.3)
    w.record(200, t=6)
    w.tick(0.2)
    w.next_step()  # à 6,2 s : la seconde 7 (6 à 7 s) a surtout été roulée dans la brique 3
    w.tick(0.8)
    w.record(100, t=7)
    assert [x.lap for x in w.samples] == [0, 0, 0, 0, 0, 0, 2]
    assert [x.target_w for x in w.samples[-2:]] == [200, 100]


def test_countdown_beeps_and_summary_at_the_end_of_a_brick(app, tmp_path):
    book = ProfileBook(tmp_path / "profils.json", rides_root=tmp_path / "sorties")
    profile = book.create("A")
    window = MainWindow(parse_workout("10s@200 20s@100"), source=Steady(210.0), book=DeviceBook(tmp_path / "d.json"),
                        profiles=book, profile=profile)
    window.timer.stop()
    window.clock = Clock()
    beeps = []
    window._beep = lambda: beeps.append(window.session.step_remaining_s)
    window._toggle()
    for _ in range(30):  # 6 s : pas encore de compte à rebours
        window._on_tick()
    assert beeps == [] and window.next_label.text().startswith("Ensuite : ")
    window._on_tick()  # 6,2 s : il reste 3,8 s
    window._on_tick()  # 6,4 s : il reste 3,6 s
    for _ in range(3):
        window._on_tick()  # 7,0 s : il reste 3 s
    assert window.next_label.text().startswith("Dans 3 s : ")
    for _ in range(16):  # un peu plus de 10 s (50 × 0,2 s = 9,999… s) : un bip à 3, 2 et 1 s
        window._on_tick()
    assert len(beeps) == 3
    assert window.session.index == 1
    # Bilan de la brique finie, à la place de « Maintenant ». 0:10 : la mesure de la dernière seconde,
    # enregistrée au tic qui change de brique, reste dans sa brique (0:09 avant l'étape 8).
    assert window.current_label.text() == "Brique 1 finie : 0:10 · 210 W pour 200 W visés (105 %)"
    window._brick_note = (window._brick_note[0], 0.0)  # 8 s plus tard
    window._refresh()
    assert window.current_label.text().startswith("Maintenant : ")
    # Bip coupé (clic droit sur la carte cible) : gardé dans le profil, plus de bip.
    window.set_brick_beep(False)
    for _ in range(100):
        window._on_tick()
    assert len(beeps) == 3
    assert ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id).brick_beep is False
    window.close()
