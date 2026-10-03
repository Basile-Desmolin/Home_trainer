import math

import pytest

from home_trainer.route import Route, RouteError, load_route, parse_gpx
from home_trainer.sensors import Slope
from home_trainer.ui.power import road_speed_kmh
from home_trainer.ui.session import RouteSession, State

M_PER_DEG_LAT = 6_371_000 * math.pi / 180


def gpx(profile, name="Col test", tag="trk", step_m=50):
    """GPX vers le nord : `profile` = [(longueur m, pente %), …], un point tous les `step_m`."""
    pts, d, ele = [(0.0, 100.0)], 0.0, 100.0
    for length, grade in profile:
        for _ in range(int(length / step_m)):
            d += step_m
            ele += step_m * grade / 100
            pts.append((d, ele))
    point = "trkpt" if tag == "trk" else "rtept"
    body = "".join(f'<{point} lat="{45 + d / M_PER_DEG_LAT:.8f}" lon="6.0"><ele>{e:.2f}</ele></{point}>'
                   for d, e in pts)
    inner = f"<trkseg>{body}</trkseg>" if tag == "trk" else body
    return (f'<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            f"<{tag}><name>{name}</name>{inner}</{tag}></gpx>").encode()


def test_reads_distance_name_and_grade():
    r = parse_gpx(gpx([(1000, 0), (1000, 6), (1000, -4)]))
    assert r.name == "Col test"
    assert r.total_m == pytest.approx(3000, rel=1e-3)
    assert r.grade_at(500) == pytest.approx(0, abs=0.05)
    assert r.grade_at(1500) == pytest.approx(6, abs=0.05)
    assert r.grade_at(2500) == pytest.approx(-4, abs=0.05)
    assert r.grade_at(1000) == pytest.approx(3, abs=0.2)  # lissée sur 100 m au changement de pente
    assert r.climb_m == pytest.approx(60, abs=1)
    assert r.climb_between(1500, 3000) == pytest.approx(30, abs=1)


def test_reads_routes_and_interpolates_missing_elevation():
    r = parse_gpx(gpx([(500, 4)], tag="rte"))
    assert r.total_m == pytest.approx(500, rel=1e-3)
    data = (b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
            b'<trkpt lat="45.0" lon="6.0"><ele>100</ele></trkpt>'
            b'<trkpt lat="45.0009" lon="6.0"></trkpt>'
            b'<trkpt lat="45.0009" lon="6.0"><ele>999</ele></trkpt>'  # point en double, ignoré
            b'<trkpt lat="45.0018" lon="6.0"><ele>110</ele></trkpt></trkseg></trk></gpx>')
    r = parse_gpx(data, default_name="sans-nom")
    assert r.name == "sans-nom" and len(r.points) == 3
    assert r.points[1].ele_m == pytest.approx(105)


def test_rejects_bad_files(tmp_path):
    with pytest.raises(RouteError):
        parse_gpx(b"pas du xml")
    with pytest.raises(RouteError):
        parse_gpx(b'<gpx><trk><trkseg><trkpt lat="45" lon="6"/><trkpt lat="45.1" lon="6"/></trkseg></trk></gpx>')
    path = tmp_path / "boucle.gpx"
    path.write_bytes(gpx([(200, 0)], name=""))
    assert load_route(path).name == "boucle"


def test_session_advances_with_power_and_sends_route_grade():
    s = RouteSession(parse_gpx(gpx([(2000, 0), (1000, 6)])), ftp=250, rider_kg=70)
    s.ride(10, 250)
    assert s.distance_m == 0  # pas démarrée
    s.start()
    for _ in range(600):
        s.ride(0.2, 200)
        s.tick(0.2)
    flat_speed = road_speed_kmh(200, Slope(0, 70))
    assert s.distance_m == pytest.approx(flat_speed / 3.6 * 120, rel=0.1)
    assert s.command == Slope(0.0, 70)
    while s.distance_m < 2500:
        s.ride(0.2, 200)
    assert s.grade_pct == pytest.approx(6, abs=0.1)
    assert s.speed_kmh == pytest.approx(road_speed_kmh(200, Slope(6, 70)), rel=0.05)
    s.adjust_difficulty(-50)
    assert s.difficulty_pct == 50 and s.command.grade_pct == pytest.approx(3, abs=0.1)
    s.record(200, 85, 150)
    assert s.samples[-1].grade_pct == s.trainer_grade_pct
    assert s.remaining_m == pytest.approx(s.route.total_m - s.distance_m)
    while s.state is State.RUNNING:
        s.ride(1, 300)
    assert s.state is State.FINISHED and s.remaining_m == 0


def test_rolls_downhill_without_pedalling():
    assert road_speed_kmh(0, Slope(-6, 70)) > 30
    assert road_speed_kmh(0, Slope(0, 70)) == 0
    assert road_speed_kmh(200, Slope(-6, 70)) > road_speed_kmh(0, Slope(-6, 70))
