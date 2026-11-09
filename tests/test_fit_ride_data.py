"""Étape 12 : données ajoutées au .fit d'activité (FTP, TSS, IF ; altitude et position du parcours GPX)."""

import os

import pytest

from home_trainer.formats.fit_activity import ActivityPoint, encode_activity
from home_trainer.ride_stats import summarize as ride_summary
from home_trainer.sync import Outbox
from home_trainer.ui.session import Sample, ride_points

T0 = 1_759_480_000


def read_fit(data: bytes) -> dict:
    fit_sdk = pytest.importorskip("garmin_fit_sdk")
    msgs, errors = fit_sdk.Decoder(fit_sdk.Stream.from_byte_array(bytearray(data))).read()
    assert errors == []
    return msgs


# --- 12a : FTP, TSS, IF ----------------------------------------------------------------------------

def test_one_hour_at_200_w_with_ftp_250():
    """Une heure à 200 W constants, FTP 250 : NP 200, IF = 200 / 250 = 0,800, TSS = 1 h × 0,8² × 100
    = 64,0. Écrit 640 (dixièmes) et 800 (millièmes), relus 64,0 et 0,8 par le SDK Garmin."""
    points = [ActivityPoint(T0 + i, 200, 90, timer_s=i + 1) for i in range(3600)]
    msgs = read_fit(encode_activity(points, ftp=250, hr_max=185))
    session = msgs["session_mesgs"][0]
    assert session["normalized_power"] == 200
    assert session["threshold_power"] == 250
    assert session["intensity_factor"] == pytest.approx(0.8)
    assert session["training_stress_score"] == pytest.approx(64.0)
    [zones] = msgs["zones_target_mesgs"]
    assert zones["functional_threshold_power"] == 250 and zones["max_heart_rate"] == 185
    assert zones["pwr_calc_type"] == "percent_ftp" and zones["hr_calc_type"] == "percent_max_hr"


def test_without_ftp_nothing_is_written():
    points = [ActivityPoint(T0 + i, 200, 90) for i in range(60)]
    msgs = read_fit(encode_activity(points))
    session = msgs["session_mesgs"][0]
    assert not {"threshold_power", "intensity_factor", "training_stress_score"} & session.keys()
    assert "zones_target_mesgs" not in msgs
    msgs = read_fit(encode_activity(points, ftp=0, hr_max=None))  # FTP nulle : pas de division par zéro
    assert "zones_target_mesgs" not in msgs and "threshold_power" not in msgs["session_mesgs"][0]


def test_heart_rate_max_alone():
    msgs = read_fit(encode_activity([ActivityPoint(T0 + i, 200) for i in range(60)], hr_max=190))
    [zones] = msgs["zones_target_mesgs"]
    assert zones["max_heart_rate"] == 190 and "functional_threshold_power" not in zones
    assert "threshold_power" not in msgs["session_mesgs"][0]


def intervals() -> list[Sample]:
    """Séance fractionnée de 30 min, une mesure par seconde : 10 min à 150 W, 5 × (1 min à 320 W,
    1 min à 120 W), 10 min à 150 W."""
    powers = [150] * 600 + ([320] * 60 + [120] * 60) * 5 + [150] * 600
    return [Sample(i + 1, p, None, 90, 140, None, 30.0, at=T0 + i) for i, p in enumerate(powers)]


def test_fit_tss_equals_ride_summary():
    """Le TSS et l'IF du .fit sont ceux du bilan de l'application (même NP, même durée)."""
    samples = intervals()
    bilan = ride_summary(samples, 250)
    session = read_fit(encode_activity(ride_points(samples), ftp=250))["session_mesgs"][0]
    assert session["training_stress_score"] == pytest.approx(round(bilan.tss, 1))
    assert session["intensity_factor"] == pytest.approx(round(bilan.intensity, 3))
    assert session["normalized_power"] == round(bilan.normalized_power_w)
    assert 20 < bilan.tss < 40  # ordre de grandeur : 30 min à IF ≈ 0,8


def test_outbox_writes_ftp(tmp_path):
    path = Outbox(tmp_path / "sorties").add([ActivityPoint(T0 + i, 200) for i in range(60)], "Sortie",
                                            ftp=240, hr_max=180)
    session = read_fit(path.read_bytes())["session_mesgs"][0]
    assert session["threshold_power"] == 240


# --- 12b : altitude, dénivelé et position du parcours GPX ------------------------------------------

def col_route():
    """Col de 5 km à 6 % puis 5 km de descente à −6 % (300 m de D+ et de D−), un point tous les 10 m,
    altitudes bruitées de ±1,5 m comme un GPS."""
    import random

    from home_trainer.route import Route

    rng = random.Random(1)
    coords = []
    for i in range(1001):
        d = i * 10.0
        ele = 500 + (0.06 * d if d <= 5000 else 300 - 0.06 * (d - 5000))
        coords.append((45 + d / 111_195, 6.0, ele + rng.uniform(-1.5, 1.5)))
    return Route.from_coordinates("Col d'essai", coords)


def ride_col(watts: float = 250):
    """Le col roulé à `watts`, une mesure par seconde, tour manuel (Tab) au sommet."""
    from home_trainer.ui.session import RouteSession, State

    s = RouteSession(col_route(), ftp=250, rider_kg=70)
    s.start()
    i, summit = 0, None
    while s.state is State.RUNNING:
        s.ride(1, watts)
        s.tick(1)
        s.record(watts, 85, 140, at=T0 + i)
        i += 1
        if summit is None and s.distance_m >= 5000:
            summit = s.distance_m
            s.new_lap()
    return s, summit


def test_route_ascent_matches_the_screen_not_the_gps_noise():
    """D+ du .fit = celui de l'application (altitudes lissées) : 299 m pour 300 m réels, quand la somme
    des montées seconde par seconde sur l'altitude brute en compterait 447 à 483 (essai du plan)."""
    s, summit = ride_col()
    route = s.route
    msgs = read_fit(encode_activity(ride_points(s.samples), ftp=250))
    session = msgs["session_mesgs"][0]
    assert session["total_ascent"] == round(route.climb_between(0, route.total_m)) == 299
    assert session["total_descent"] == round(route.descent_between(0, route.total_m))
    assert 295 <= session["total_descent"] <= 305
    up, down = msgs["lap_mesgs"]
    assert up["total_ascent"] == round(route.climb_between(0, summit))
    assert abs(up["total_ascent"] + down["total_ascent"] - session["total_ascent"]) <= 1
    assert up["total_descent"] <= 2 and down["total_ascent"] <= 2  # bruit de part et d'autre du sommet
    assert session["max_altitude"] == pytest.approx(800, abs=2) and session["min_altitude"] == pytest.approx(500, abs=2)


def test_route_records_altitude_and_position():
    s, _ = ride_col()
    msgs = read_fit(encode_activity(ride_points(s.samples)))
    records, samples = msgs["record_mesgs"], s.samples
    assert len(records) == len(samples)
    for rec, x in ((records[0], samples[0]), (records[len(records) // 2], samples[len(samples) // 2])):
        assert rec["altitude"] == pytest.approx(x.altitude_m, abs=0.2)  # au 1/5 de mètre
        assert rec["enhanced_altitude"] == pytest.approx(x.altitude_m, abs=0.2)
        assert rec["position_lat"] == round(x.lat * 2 ** 31 / 180)  # demi-cercles, comme un Garmin
        assert rec["position_long"] == round(x.lon * 2 ** 31 / 180)
    session = msgs["session_mesgs"][0]
    assert session["sub_sport"] == "virtual_activity"  # Strava : Virtual Ride, hors segments
    assert session["start_position_lat"] == records[0]["position_lat"]
    assert session["end_position_lat"] == records[-1]["position_lat"]
    assert all(lap["sub_sport"] == "virtual_activity" for lap in msgs["lap_mesgs"])


def test_workout_and_free_ride_have_no_altitude_nor_position():
    msgs = read_fit(encode_activity(ride_points(intervals()), ftp=250))
    keys = {"altitude", "enhanced_altitude", "position_lat", "position_long"}
    assert not any(keys & r.keys() for r in msgs["record_mesgs"])
    session = msgs["session_mesgs"][0]
    assert session["sub_sport"] == "indoor_cycling"
    assert not {"total_ascent", "total_descent", "start_position_lat", "max_altitude"} & session.keys()


def test_rescue_keeps_route_data(tmp_path):
    """La sortie de secours garde altitude, D+ et position ; un ancien secours (sans eux) se relit."""
    import json

    from home_trainer.ui.rescue import RescueFile, read_rescue

    s, _ = ride_col()
    rescue = RescueFile(tmp_path, "moi", "parcours", s.samples)
    rescue.write("Col", "")
    back = read_rescue(rescue.path)
    assert back.samples == s.samples
    assert encode_activity(ride_points(back.samples)) == encode_activity(ride_points(s.samples))
    old = tmp_path / "ancien.jsonl"
    old.write_text(json.dumps({"header": {"title": "Ancien"}}) + "\n"
                   + json.dumps({"t": 1, "power_w": 200, "target_w": None, "at": T0}) + "\n", encoding="utf-8")
    assert read_rescue(old).samples[0].altitude_m is None


# --- 12c : .tcx et .csv -----------------------------------------------------------------------------

def test_tcx_and_csv_carry_route_altitude(tmp_path):
    import csv
    import xml.etree.ElementTree as ET

    from home_trainer.formats.activity_export import write_ride

    s, _ = ride_col()
    points = ride_points(s.samples)
    tcx = "{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}"
    track = ET.parse(write_ride(points, tmp_path / "col.tcx")).getroot().findall(f".//{tcx}Trackpoint")
    first = track[0]
    assert [c.tag.removeprefix(tcx) for c in first][:4] == ["Time", "Position", "AltitudeMeters", "DistanceMeters"]
    assert float(first.find(f"{tcx}AltitudeMeters").text) == pytest.approx(points[0].altitude_m, abs=0.05)
    assert float(first.find(f"{tcx}Position/{tcx}LatitudeDegrees").text) == pytest.approx(points[0].lat, abs=1e-7)
    with write_ride(points, tmp_path / "col.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    column = rows[0].index("altitude_m")
    assert float(rows[1][column]) == pytest.approx(points[0].altitude_m, abs=0.05)
    # Hors parcours : ni position ni altitude, colonne vide.
    plain = ride_points(intervals()[:60])
    track = ET.parse(write_ride(plain, tmp_path / "s.tcx")).getroot().findall(f".//{tcx}Trackpoint")
    assert track[0].find(f"{tcx}Position") is None and track[0].find(f"{tcx}AltitudeMeters") is None
    with write_ride(plain, tmp_path / "s.csv").open(encoding="utf-8-sig") as f:
        assert list(csv.reader(f, delimiter=";"))[1][column] == ""


# --- dans la fenêtre ------------------------------------------------------------------------------

@pytest.fixture
def window(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841 (gardée en vie)
    from home_trainer.bricks import parse_workout
    from home_trainer.sync import AccountBook
    from home_trainer.ui.app import MainWindow

    w = MainWindow(parse_workout("2m@150"), 250, accounts=AccountBook(tmp_path / "comptes.json"),
                   outbox=Outbox(tmp_path / "sorties"))
    w.ask_ride_export = lambda parent, default, summary="", **kw: default
    w.confirm_stop = lambda parent, action, question: True
    yield w
    w.close()


def test_saved_ride_carries_the_profile_ftp(window):
    """Sortie enregistrée par la fenêtre : la FTP en cours (250) et le TSS du bilan dans le .fit."""
    session = window.session
    session.start()
    for i in range(120):
        session.record(150, 90, 130, 30.0, at=T0 + i)
        session.tick(1)
    bilan = window.ride_summary(session)
    assert window.end_ride(session, send=False)
    [path] = window.outbox.dir.glob("*.fit")
    fit = read_fit(path.read_bytes())["session_mesgs"][0]
    assert fit["threshold_power"] == 250
    assert fit["training_stress_score"] == pytest.approx(round(bilan.tss, 1))
