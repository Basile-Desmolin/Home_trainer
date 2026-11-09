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
    # Bilan de la brique finie, à la place de « Maintenant ». 0:09 et non 0:10 : la mesure de la dernière
    # seconde est enregistrée après le tic qui change de brique, avec la brique suivante (défaut d'origine,
    # noté pour l'étape 8 ; il décale aussi d'une seconde les tours du .fit).
    assert window.current_label.text() == "Brique 1 finie : 0:09 · 210 W pour 200 W visés (105 %)"
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
