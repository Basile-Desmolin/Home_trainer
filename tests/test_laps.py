"""Tours manuels (touche Tab) : sortie, indicateur « TOUR », .fit (lap_trigger), .tcx et .csv."""

import csv
import os
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.formats.activity_export import write_ride  # noqa: E402
from home_trainer.formats.fit_activity import encode_activity  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.indicators import CATALOG, RideView  # noqa: E402
from home_trainer.ui.session import FreeRideSession, State, WorkoutSession, ride_points  # noqa: E402

T0 = 1_759_480_000
TCX = "{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def ride(session, seconds, watts):
    for _ in range(seconds):
        session.tick(1)
        session.record(watts, 90, at=T0 + len(session.samples), t=session.elapsed_s)


def _read_fit(data: bytes):
    fit_sdk = pytest.importorskip("garmin_fit_sdk")
    msgs, errors = fit_sdk.Decoder(fit_sdk.Stream.from_byte_array(bytearray(data))).read(
        convert_types_to_strings=False)
    assert errors == []
    return msgs


def test_a_lap_needs_at_least_one_second_ridden():
    free = FreeRideSession(250)
    assert not free.new_lap()  # avant le départ
    free.start()
    ride(free, 3, 200)
    assert free.new_lap() and free.manual_lap == 1
    assert not free.new_lap()  # deux appuis de suite : pas de tour vide
    ride(free, 2, 250)
    assert [x.manual_lap for x in free.samples] == [0, 0, 0, 1, 1]


def test_free_ride_manual_lap_in_the_fit():
    """40 s à 200 W, Tab, 50 s à 300 W : deux tours dans le .fit, le premier fermé à la main
    (lap_trigger = 0, « manual »), le dernier fermé par la fin de la sortie (champ vide, comme avant)."""
    free = FreeRideSession(250)
    free.start()
    ride(free, 40, 200)
    free.new_lap()
    ride(free, 50, 300)
    msgs = _read_fit(encode_activity(ride_points(free.samples)))
    laps = msgs["lap_mesgs"]
    assert len(laps) == 2 and msgs["session_mesgs"][0]["num_laps"] == 2
    assert [lap.get("lap_trigger") for lap in laps] == [0, None]
    assert [lap["avg_power"] for lap in laps] == [200, 300]
    assert [round(lap["total_timer_time"]) for lap in laps] == [40, 50]


def test_workout_manual_lap_splits_the_brick():
    """Séance 2 min à 150 W puis 2 min à 280 W ; Tab après 20 s, brique suivante après 50 s, 30 s de plus :
    trois tours (20 s, 30 s, 30 s). Seul le premier est fermé à la main ; le deuxième l'est par le
    changement de brique."""
    w = WorkoutSession(parse_workout("2m@150 2m@280"), 250)
    w.start()
    ride(w, 20, 150)
    w.new_lap()
    ride(w, 30, 150)
    w.next_step()
    ride(w, 30, 280)
    points = ride_points(w.samples)
    laps = _read_fit(encode_activity(points))["lap_mesgs"]
    assert [round(lap["total_timer_time"]) for lap in laps] == [20, 30, 30]
    assert [lap["avg_power"] for lap in laps] == [150, 150, 280]
    assert [lap.get("lap_trigger") for lap in laps] == [0, None, None]


def test_tcx_and_csv_follow_the_same_laps(tmp_path):
    free = FreeRideSession(250)
    free.start()
    ride(free, 30, 200)
    free.new_lap()
    ride(free, 30, 250)
    points = ride_points(free.samples)
    root = ET.parse(write_ride(points, tmp_path / "s.tcx")).getroot()
    assert len(root.findall(f".//{TCX}Lap")) == 2
    with write_ride(points, tmp_path / "s.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert [rows[1][-1], rows[30][-1], rows[31][-1], rows[-1][-1]] == ["1", "1", "2", "2"]


def test_the_lap_indicator_shows_time_and_average_of_the_lap():
    free = FreeRideSession(250)
    show = CATALOG["lap"].show
    assert (show(RideView(free, None, 250)).value, show(RideView(free, None, 250)).sub) == (
        "0:00", "tour 1 · Tab : nouveau tour")
    free.start()
    ride(free, 65, 200)
    shown = show(RideView(free, None, 250))
    assert (shown.value, shown.sub) == ("1:05", "tour 1 · moy. 200 W")
    free.new_lap()
    assert show(RideView(free, None, 250)).value == "0:00"  # tour ouvert, pas encore de mesure
    assert show(RideView(free, None, 250)).sub.startswith("tour 2")
    ride(free, 10, 300)
    shown = show(RideView(free, None, 250))
    assert (shown.value, shown.sub) == ("0:10", "tour 2 · moy. 300 W")
    # En séance, une nouvelle brique ouvre aussi un tour (comme dans le .fit).
    w = WorkoutSession(parse_workout("1m@150 1m@280"), 250)
    w.start()
    ride(w, 61, 150)
    assert show(RideView(w, None, 250)).sub == "tour 2 · moy. 150 W"


def test_the_tab_key_opens_a_lap_in_the_window(app):
    window = MainWindow(parse_workout("10m@200"))
    window.timer.stop()
    window.show()
    window.activateWindow()
    assert QTest.qWaitForWindowActive(window, 1000)
    window._toggle()
    assert window.session.state is State.RUNNING
    ride(window.session, 5, 200)
    # Au lancement, la case FTP a le focus clavier : Tab ouvre quand même un tour (pas de changement de case).
    focus = QtWidgets.QApplication.focusWidget() or window
    QTest.keyClick(focus, Qt.Key_Tab)
    assert window.session.manual_lap == 1
    assert QtWidgets.QApplication.focusWidget() is focus
    window.close()
