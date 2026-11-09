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
    """La mesure de la dernière seconde d'une brique allait à la brique suivante (enregistrée
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


def ride(w, seconds, watts=200):
    for _ in range(seconds):
        w.tick(1)
        w.record(watts, 90, at=1.7e9 + w.elapsed_s, t=w.elapsed_s)


def fit_laps(samples):
    fit_sdk = pytest.importorskip("garmin_fit_sdk")
    from home_trainer.formats.fit_activity import encode_activity
    from home_trainer.ui.session import ride_points
    msgs, errors = fit_sdk.Decoder(fit_sdk.Stream.from_byte_array(bytearray(
        encode_activity(ride_points(samples))))).read()
    assert errors == []
    return msgs["lap_mesgs"]


def test_previous_brick_resumes_a_brick_skipped_by_mistake():
    """Séance 1 min à 200 W, 1 min à 100 W, 1 min à 150 W. Brique 2 passée par erreur (N)
    à 1:10, 5 s roulées dans la brique 3, puis ← : la brique 2 reprend là où elle a été quittée
    (0:50 restantes), puis la brique 3 entière. Avant : la brique 2 était perdue (0:10 roulées).
    Tours du .fit : 60, 10, 5, 50, 60 s ; 185 s au total."""
    w = WorkoutSession(parse_workout("1m@200 1m@100 1m@150"), 250)
    w.start()
    ride(w, 70)
    w.next_step()
    ride(w, 5)
    assert (w.index, w.step_remaining_s) == (2, 55)
    w.previous_step()
    assert (w.index, w.step_remaining_s, w.position_s, w.target_w) == (1, 50, 70, 100)
    ride(w, 110)
    assert w.state.name == "FINISHED" and w.elapsed_s == 185
    laps = [x.lap for x in w.samples]
    assert laps == [0] * 60 + [1] * 10 + [2] * 5 + [1] * 50 + [2] * 60
    assert [x.target_w for x in w.samples[70:76]] == [150] * 5 + [100]
    assert [lap["total_timer_time"] for lap in fit_laps(w.samples)] == [60, 10, 5, 50, 60]
    # Graphique : le retour en arrière ouvre un nouveau tronçon (pas de trait qui revient sur l'axe).
    assert [len(run) for run in w.chart_runs] == [70, 5, 110]


def test_previous_brick_after_a_finished_brick_starts_it_again():
    w = WorkoutSession(parse_workout("1m@200 1m@100"), 250)
    w.start()
    ride(w, 65)  # brique 1 finie, 5 s dans la brique 2
    w.previous_step()
    assert (w.index, w.step_remaining_s) == (0, 60)
    w.previous_step()  # première brique : elle recommence
    assert (w.index, w.step_remaining_s) == (0, 60)


def test_restart_brick_opens_a_new_lap():
    """Brique 1 (1 min) recommencée à 0:30 (R) : tours du .fit 30, 60, 60 s ; le premier
    est marqué fermé par l'utilisateur, comme un tour manuel (Tab)."""
    w = WorkoutSession(parse_workout("1m@200 1m@100"), 250)
    w.start()
    ride(w, 30)
    w.restart_step()
    assert (w.index, w.step_remaining_s, w.position_s, w.elapsed_s) == (0, 60, 0, 30)
    ride(w, 120)
    assert w.state.name == "FINISHED" and w.elapsed_s == 150
    laps = fit_laps(w.samples)
    assert [lap["total_timer_time"] for lap in laps] == [30, 60, 60]
    assert laps[0].get("lap_trigger") == "manual" and laps[1].get("lap_trigger") != "manual"
    w.restart_step()  # séance finie : rien ne change
    assert w.state.name == "FINISHED"


def test_previous_and_restart_from_the_window(app, tmp_path):
    window = MainWindow(parse_workout("1m@200 1m@100 1m@150"), source=Steady(210.0),
                        book=DeviceBook(tmp_path / "d.json"))
    window.timer.stop()
    window.clock = Clock()
    window._beep = lambda last=False: None
    window._toggle()
    for _ in range(350):  # 70 s
        window._on_tick()
    window._next_step()
    window.previous_step()
    assert window.session.index == 1 and round(window.session.step_remaining_s) == 50
    window.restart_step()
    assert window.session.index == 1 and window.session.step_remaining_s == 60
    menu = window._step_menu()
    assert [a.text() for a in menu.actions()] == ["Brique précédente  (←)", "Recommencer la brique  (R)"]
    window.close()


def test_countdown_beeps_and_summary_at_the_end_of_a_brick(app, tmp_path):
    book = ProfileBook(tmp_path / "profils.json", rides_root=tmp_path / "sorties")
    profile = book.create("A")
    window = MainWindow(parse_workout("10s@200 20s@100"), source=Steady(210.0), book=DeviceBook(tmp_path / "d.json"),
                        profiles=book, profile=profile)
    window.timer.stop()
    window.clock = Clock()
    beeps = []
    window._beep = lambda last=False: beeps.append(window.session.step_remaining_s)
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
    # enregistrée au tic qui change de brique, reste dans sa brique (0:09 avant la correction).
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


def test_beep_sound_and_countdown_length_are_kept_in_the_profile(app, tmp_path):
    """Compte à rebours de 5 s, bips à 5 s puis aux 3 dernières secondes, la dernière
    signalée ; son et durée gardés dans le profil et repris à la relance."""
    book = ProfileBook(tmp_path / "profils.json", rides_root=tmp_path / "sorties")
    profile = book.create("A")
    window = MainWindow(parse_workout("10s@200 20s@100"), source=Steady(210.0), book=DeviceBook(tmp_path / "d.json"),
                        profiles=book, profile=profile)
    window.timer.stop()
    window.clock = Clock()
    assert (window.beep_sound, window.beep_before_s, window.beep_last_distinct) == ("double", 3, True)
    window.set_beep(sound="montant", before_s=5, last_distinct=False)
    window.set_beep(before_s=7)  # durée hors des choix : ignorée
    beeps = []
    window._beep = lambda last=False: beeps.append((window._countdown_s(), last))
    window._toggle()
    for _ in range(24):  # 4,8 s : pas encore de compte à rebours
        window._on_tick()
    assert window._countdown_s() is None
    window._on_tick()  # 5,0 s : il reste 5 s
    assert window.next_label.text().startswith("Dans 5 s : ")
    for _ in range(26):
        window._on_tick()
    assert beeps == [(5, False), (3, False), (2, False), (1, True)]
    saved = ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id)
    assert (saved.beep_sound, saved.beep_before_s, saved.beep_last_distinct) == ("montant", 5, False)
    window.close()
    other = MainWindow(parse_workout("10s@200"), source=Steady(210.0), book=DeviceBook(tmp_path / "d.json"),
                       profiles=book, profile=saved)
    assert (other.beep_sound, other.beep_before_s) == ("montant", 5)
    other.close()


def test_beep_menu_lists_the_sounds_and_durations(app, tmp_path):
    window = MainWindow(parse_workout("10s@200"), source=Steady(210.0), book=DeviceBook(tmp_path / "d.json"))
    window._beep = lambda last=False: None
    menu = window._beep_menu()
    texts = [a.text() for a in menu.actions()]
    assert texts[0] == "Bip 3 s avant chaque brique" and "Son distinct à la dernière seconde" in texts
    sounds = next(a.menu() for a in menu.actions() if a.text() == "Son du bip")
    assert [a.text() for a in sounds.actions()] == ["Double bip", "Bip court", "Bip montant", "Son de Windows"]
    assert [a.isChecked() for a in sounds.actions()] == [True, False, False, False]
    sounds.actions()[3].trigger()
    assert window.beep_sound == "windows"
    durations = next(a.menu() for a in menu.actions() if a.text() == "Compte à rebours")
    durations.actions()[2].trigger()
    assert window.beep_before_s == 10
    window.close()


class Cadence(Steady):
    def __init__(self, watts, cadence):
        super().__init__(watts)
        self.cadence = cadence

    def read(self, dt):
        return Reading(self.watts, self.cadence)


def test_low_cadence_alert_after_five_seconds_under_the_threshold(app, tmp_path):
    """Seuil de 50 tr/min par défaut ; alerte après 5 s sous le seuil, en orange sur la
    carte cadence ; une cadence de 0 (roue libre) ne compte pas ; seuil gardé dans le profil."""
    from home_trainer.ui.theme import WAIT
    book = ProfileBook(tmp_path / "profils.json", rides_root=tmp_path / "sorties")
    profile = book.create("A")
    assert profile.cadence_alert_rpm == 50
    source = Cadence(150.0, 45.0)
    window = MainWindow(parse_workout("10m@150"), source=source, book=DeviceBook(tmp_path / "d.json"),
                        profiles=book, profile=profile)
    window.timer.stop()
    window.clock = Clock()
    window._beep = lambda last=False: None
    window._toggle()
    for _ in range(20):  # 4 s sous 50 tr/min : pas encore d'alerte
        window._on_tick()
    card = window.cards["cadence"]
    assert window.cadence_low() is None and "cadence basse" not in card.sub.text()
    for _ in range(8):  # 5,6 s
        window._on_tick()
    assert window.cadence_low() == 50
    assert card.sub.text().startswith("cadence basse (< 50)") and WAIT.lower() in card.value.styleSheet().lower()
    source.cadence = 0.0  # roue libre : plus d'alerte
    window._on_tick()
    assert window.cadence_low() is None and "cadence basse" not in card.sub.text()
    source.cadence = 85.0
    for _ in range(50):
        window._on_tick()
    assert window.cadence_low() is None
    window.set_cadence_alert(90)  # 85 < 90
    for _ in range(28):
        window._on_tick()
    assert window.cadence_low() == 90
    window.set_cadence_alert(0)  # pas d'alerte
    for _ in range(30):
        window._on_tick()
    assert window.cadence_low() is None
    assert ProfileBook.load(book.path, tmp_path / "sorties").get(profile.id).cadence_alert_rpm == 0
    menu = QtWidgets.QMenu()
    window._card_menu("cadence", menu)
    alert = next(a.menu() for a in menu.actions() if a.text() == "Alerte de cadence basse")
    assert [a.text() for a in alert.actions()][:2] == ["Pas d'alerte", "Sous 50 tr/min"]
    window.close()


def test_sounds_are_short_valid_wav_files(tmp_path):
    import wave

    from home_trainer.ui.sounds import LAST, RATE, SOUND_NAMES, sound_file
    for name in [*[k for k in SOUND_NAMES if k != "windows"], LAST]:
        path = sound_file(name, tmp_path)
        with wave.open(str(path)) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
            seconds = w.getnframes() / RATE
        assert 0.05 < seconds < 0.5, name
    assert sound_file("double", tmp_path).stat().st_mtime == path.with_name("double.wav").stat().st_mtime
