"""Sortie de secours : fichier écrit pendant la sortie, relu et proposé au lancement (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from home_trainer.formats.fit_activity import encode_activity  # noqa: E402
from home_trainer.ui.rescue import RescueFile, find_rescued, read_rescue  # noqa: E402
from home_trainer.ui.session import Sample, ride_points  # noqa: E402

START = 1_759_480_000


def sample(i: int) -> Sample:
    """Mesures aux décimales quelconques, pour vérifier qu'elles reviennent à l'identique."""
    return Sample(t=i + 1.0, power_w=150 + (i * 37) % 120 + 0.123456789, target_w=200.0 if i % 2 else None,
                  cadence_rpm=88.6 + (i % 7) / 3, heart_rate_bpm=None if i % 11 == 0 else 120 + i % 40,
                  grade_pct=None, speed_kmh=31.4159265 + i / 1000, at=START + i, lap=i // 300,
                  position_s=i + 1.0, balance_left_pct=49.5 + (i % 3) / 10)


def test_file_grows_then_disappears(tmp_path):
    samples: list[Sample] = []
    rescue = RescueFile(tmp_path, "adrien", "seance", samples)
    rescue.write("Séance", "texte")
    assert rescue.path is None  # rien roulé : pas de fichier
    samples += [sample(i) for i in range(10)]
    rescue.write("Séance", "texte")
    size = rescue.path.stat().st_size
    samples += [sample(i) for i in range(10, 20)]
    rescue.write("Séance", "texte")
    assert rescue.path.stat().st_size > size
    assert len(read_rescue(rescue.path).samples) == 20
    path = rescue.path
    rescue.remove()
    assert not path.exists()


def test_last_header_counts_and_cut_line_is_ignored(tmp_path):
    samples = [sample(i) for i in range(5)]
    rescue = RescueFile(tmp_path, "adrien", "seance", samples)
    rescue.write("Séance", "(arrêtée avant la fin)")
    samples.append(sample(5))
    rescue.write("Séance", "terminée")
    with open(rescue.path, "a", encoding="utf-8") as f:
        f.write('{"t": 7.0, "power_w": 2')  # coupure de courant pendant l'écriture
    ride = read_rescue(rescue.path)
    assert ride.description == "terminée" and len(ride.samples) == 6


def test_find_rescued_keeps_the_profile(tmp_path):
    for profile, mode, offset in (("adrien", "libre", 100), ("adrien", "seance", 0), ("adrien-2", "seance", 0)):
        RescueFile(tmp_path, profile, mode, [sample(i + offset) for i in range(3)]).write(mode, "")
    (tmp_path / "adrien-seance-illisible.jsonl").write_bytes(b"\xff\xfe")
    rides = find_rescued(tmp_path, "adrien")
    assert [r.mode for r in rides] == ["seance", "libre"]  # dans l'ordre de départ
    assert (tmp_path / "adrien-seance-illisible.jsonl").exists()


def test_fit_rebuilt_from_rescue_is_identical(tmp_path):
    """Sortie simulée de 30 min, écrite toutes les 10 s : le .fit reconstruit est le même octet pour octet."""
    samples: list[Sample] = []
    rescue = RescueFile(tmp_path, "adrien", "seance", samples)
    for i in range(1800):
        samples.append(sample(i))
        if i % 10 == 9:
            rescue.write("Séance", "")
    back = read_rescue(rescue.path).samples
    assert back == samples  # mesures identiques, décimales comprises (le .fit, lui, arrondit)
    expected = encode_activity(ride_points(samples))
    rebuilt = encode_activity(ride_points(back))
    assert len(expected) > 10_000 and rebuilt == expected


# --- dans la fenêtre ---------------------------------------------------------

from PySide6 import QtWidgets  # noqa: E402

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.history import FILE_NAME as HISTORY_FILE, RideHistory  # noqa: E402
from home_trainer.sync import AccountBook, Outbox  # noqa: E402
from home_trainer.ui import rescue as rescue_module  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.ride_export import DISCARD  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def new_window(tmp_path) -> MainWindow:
    w = MainWindow(parse_workout("2m@150"), 250, accounts=AccountBook(tmp_path / "comptes.json"),
                   outbox=Outbox(tmp_path / "sorties"), rescue_dir=tmp_path / "secours")
    # Jamais de vraie fenêtre modale : la proposition différée peut partir pendant un autre test.
    w.ask_rescued = lambda parent, ride: rescue_module.LATER
    return w


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    w = new_window(tmp_path)
    w.ask_ride_export = lambda parent, default, summary="", **kw: default
    w.confirm_stop = lambda parent, action, question: True
    w.warnings = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda parent, title, text: w.warnings.append(text))
    yield w
    w.ask_unsaved = lambda *a: False  # rien ne doit rester ouvert à la fermeture
    w.close()


def pedal(session, seconds, start=1_759_480_000):
    session.start()
    for i in range(seconds):
        session.record(150 + i % 50, 90, 130, 30.0, at=start + i)
        session.tick(1)


def rescues(tmp_path):
    return sorted((tmp_path / "secours").glob("*.jsonl"))


def save_now(window):
    window._rescued_at = float("-inf")  # les 10 s sont passées
    window._on_tick()


def block(path):
    path.write_text("")  # un fichier là où devrait être le dossier


def test_window_writes_rescue_then_removes_it(window, tmp_path):
    pedal(window.session, 30)
    save_now(window)
    [path] = rescues(tmp_path)
    assert read_rescue(path).samples == window.session.samples
    window.finish_button.click()
    assert rescues(tmp_path) == [] and len(list((tmp_path / "sorties").glob("*.fit"))) == 1


def test_rescue_follows_ftp_change(window, tmp_path):
    pedal(window.session, 20)
    save_now(window)
    window.ftp_box.setValue(300)  # la séance est recréée, ses mesures gardées
    pedal(window.session, 20, start=1_759_480_020)
    save_now(window)
    [path] = rescues(tmp_path)
    assert len(read_rescue(path).samples) == 40


def test_discarded_ride_removes_rescue(window, tmp_path):
    pedal(window.session, 30)
    save_now(window)
    window.ask_ride_export = lambda parent, default, summary="", **kw: DISCARD
    window.finish_button.click()
    assert rescues(tmp_path) == []


def test_failed_save_on_load_keeps_ride_then_proposes_it(window, tmp_path):
    pedal(window.session, 30)
    samples = list(window.session.samples)
    block(tmp_path / "sorties")
    window.load(parse_workout("1m@100"))  # la séance est remplacée, son .fit impossible
    assert "proposée au prochain lancement" in window.warnings[-1]
    [path] = rescues(tmp_path)
    assert read_rescue(path).samples == samples
    window.load(parse_workout("1m@120"))  # pas de second message ni de second secours
    assert len(window.warnings) == 1 and len(rescues(tmp_path)) == 1

    (tmp_path / "sorties").unlink()
    asked = []
    window.ask_rescued = lambda parent, ride: asked.append(ride) or rescue_module.SAVE
    window.propose_rescued()
    assert asked == []  # gardée pendant cette session : proposée au prochain lancement seulement
    later = new_window(tmp_path)  # prochain lancement
    later.ask_rescued = window.ask_rescued
    later.propose_rescued()
    later.close()
    assert len(asked) == 1 and len(asked[0].samples) == 30
    assert rescues(tmp_path) == []
    [fit] = (tmp_path / "sorties").glob("*.fit")
    assert fit.name in window.outbox.entries()
    history = RideHistory.load(tmp_path / "sorties" / HISTORY_FILE)
    assert len(history.rides) == 1 and history.rides[0].file == str(fit.absolute())


def test_failed_save_on_close_keeps_ride(app, tmp_path, monkeypatch):
    w = new_window(tmp_path)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a: None)
    w.enter_free_ride()
    pedal(w.free, 45)
    block(tmp_path / "sorties")
    w.close()
    [path] = rescues(tmp_path)
    ride = read_rescue(path)
    assert ride.mode == "libre" and len(ride.samples) == 45
    asked = []
    w.ask_rescued = lambda parent, ride: asked.append(ride) or rescue_module.LATER
    w.propose_rescued()  # proposition différée arrivée après la fermeture : rien ne s'ouvre
    assert asked == []


def test_rescue_impossible_too_retry_then_success(window, tmp_path):
    pedal(window.session, 30)
    block(tmp_path / "sorties")
    block(tmp_path / "secours")
    asked = []

    def retry(parent, title, duration_s, error):
        asked.append(error)
        (tmp_path / "sorties").unlink()  # OneDrive fermé, place libérée
        return True

    window.ask_unsaved = retry
    window.load(parse_workout("1m@100"))
    assert len(asked) == 1 and len(list((tmp_path / "sorties").glob("*.fit"))) == 1


def test_rescue_impossible_too_discard(window, tmp_path):
    pedal(window.session, 30)
    block(tmp_path / "sorties")
    block(tmp_path / "secours")
    window.ask_unsaved = lambda *a: False  # « Jeter », confirmé
    window.load(parse_workout("1m@100"))
    assert window.session.samples == [] and window.warnings == []


def test_later_keeps_and_discard_removes(window, tmp_path):
    RescueFile(tmp_path / "secours", None, "libre", [sample(i) for i in range(5)]).write("Mode libre", "")
    window.ask_rescued = lambda parent, ride: rescue_module.LATER
    window.propose_rescued()
    assert len(rescues(tmp_path)) == 1
    window.ask_rescued = lambda parent, ride: rescue_module.DISCARD
    window.propose_rescued()
    assert rescues(tmp_path) == [] and list((tmp_path / "sorties").glob("*.fit")) == []
