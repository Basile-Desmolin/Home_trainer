"""Séances .erg dont les cibles sont en fréquence cardiaque : lecture, conversion, fenêtre."""

import os

import pytest

from home_trainer.formats import FormatError, decode_workout, encode_workout
from home_trainer.heart_zones import ftp_percent_for, to_power
from home_trainer.workout import PowerUnit

HR_ERG = b"""[COURSE HEADER]
VERSION = 2
UNITS = ENGLISH
FILE NAME = Endurance FC
MINUTES HR
[END COURSE HEADER]
[COURSE DATA]
0.00\t110
5.00\t130
5.00\t145
35.00\t145
35.00\t0
40.00\t0
[END COURSE DATA]
"""


def test_heart_rate_erg_is_not_read_as_watts():
    w = decode_workout(HR_ERG, ".erg")
    steps = list(w.flatten())
    assert w.uses_heart_rate and not w.has_power_targets
    assert [s.power for s in steps] == [None, None, None]
    assert (steps[0].heart_rate.mid, steps[0].heart_rate_end.mid) == (110, 130)
    assert steps[1].heart_rate.mid == 145 and steps[1].duration_s == 1800
    assert steps[2].heart_rate is None  # 0 : roue libre
    seg = w.timeline(250)[0]
    assert seg.low_w is None and seg.target_bpm(150) == 120


@pytest.mark.parametrize("header", ["MINUTES BPM", "MINUTES HEARTRATE", "minutes heart rate", "MINUTES FC"])
def test_heart_rate_header_variants(header):
    data = HR_ERG.replace(b"MINUTES HR", header.encode())
    assert decode_workout(data, ".erg").uses_heart_rate


def test_heart_rate_erg_round_trip_and_other_formats_refused():
    w = decode_workout(HR_ERG, ".erg")
    again = decode_workout(encode_workout(w, ".erg"), ".erg")
    assert [(s.duration_s, s.heart_rate, s.heart_rate_end) for s in again.flatten()] == \
        [(s.duration_s, s.heart_rate, s.heart_rate_end) for s in w.flatten()]
    for ext in (".zwo", ".mrc", ".fit"):
        with pytest.raises(FormatError):
            encode_workout(w, ext)


def test_conversion_follows_zones():
    # FC max 190 → FC au seuil 171 bpm = FTP.
    assert ftp_percent_for(171, 190) == 100
    assert 55 < ftp_percent_for(120, 190) < 65  # endurance
    assert 75 < ftp_percent_for(145, 190) < 80  # tempo bas
    assert ftp_percent_for(60, 190) == 35 and ftp_percent_for(200, 190) == 120
    assert ftp_percent_for(150, 170) > ftp_percent_for(150, 190)  # FC max plus basse : plus dur


def test_to_power_keeps_heart_rate_and_free_steps():
    w = to_power(decode_workout(HR_ERG, ".erg"), 190)
    steps = list(w.flatten())
    assert steps[0].power.unit is PowerUnit.FTP_PERCENT and steps[0].is_ramp
    assert steps[1].power.mid == ftp_percent_for(145, 190) and steps[1].heart_rate.mid == 145
    assert steps[2].power is None
    seg = w.timeline(250)[1]
    assert seg.target_w(0) == pytest.approx(250 * ftp_percent_for(145, 190) / 100)
    assert encode_workout(w, ".zwo", 250)  # convertie, elle s'enregistre dans tous les formats


# --- fenêtre ---------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def window(tmp_path):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from home_trainer.bricks import parse_workout
    from home_trainer.profiles import ProfileBook
    from home_trainer.ui.app import MainWindow

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    book = ProfileBook(tmp_path / "profils.json")
    profile = book.create("Basile", 250, 70)
    book.save()
    w = MainWindow(parse_workout("2m@150"), 250, profiles=book, profile=profile)
    path = tmp_path / "endurance.erg"
    path.write_bytes(HR_ERG)
    w.hr_file = str(path)
    yield w
    w.close()


def answer(to_power, hr_max=180):
    from home_trainer.ui.heart_rate_mode import HeartRateChoice
    asked = []

    def ask(parent, name, hr_max_now, to_power_now=False):
        asked.append((name, hr_max_now, to_power_now))
        return HeartRateChoice(to_power, hr_max)
    ask.asked = asked
    return ask


def test_ride_without_erg(window):
    window.ask_heart_rate_mode = answer(False)
    assert window.open_path(window.hr_file)
    s = window.session
    assert not s.erg and not window.erg_button.isEnabled()
    assert s.target_w is None and s.target_bpm == 110
    s.start()
    assert s.command.grade_pct == 0  # résistance libre
    window.toggle_erg()
    assert not s.erg
    window._refresh()
    assert window.m_target.title.text() == "CIBLE FC"
    assert window.profile.hr_max is None  # FC max pas utile ici, pas enregistrée


def test_ride_converted_to_power_remembers_hr_max(window):
    window.session.toggle_erg()  # ERG coupé avant : la séance convertie repart en ERG
    window._erg_wanted = False
    ask = answer(True, 180)
    window.ask_heart_rate_mode = ask
    assert window.open_path(window.hr_file)
    s = window.session
    assert s.erg and window.erg_button.isEnabled()
    assert s.target_w == pytest.approx(250 * ftp_percent_for(110, 180) / 100) and s.target_bpm == 110
    assert window.profile.hr_max == 180
    window._refresh()
    assert "FC cible 110 bpm" in window.m_target.sub.text()
    # La fois suivante, la fenêtre propose la FC max et le choix d'avant.
    window.open_path(window.hr_file)
    assert ask.asked[-1] == ("Endurance FC", 180, True)


def test_cancel_keeps_current_workout(window):
    window.ask_heart_rate_mode = lambda *a: None
    before = window.session.workout
    assert not window.open_path(window.hr_file)
    assert window.session.workout is before
