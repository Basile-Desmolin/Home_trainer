"""Catalogue d'indicateurs : texte de chaque carte, dans chaque mode (repris à l'identique des trois écrans)."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.route import load_route  # noqa: E402
from home_trainer.sensors import HeartRateReading  # noqa: E402
from home_trainer.ui.indicators import CATALOG, Mode, RideView, km_text  # noqa: E402
from home_trainer.ui.power import Reading  # noqa: E402
from home_trainer.ui.session import FreeMode, FreeRideSession, RouteSession, WorkoutSession  # noqa: E402
from home_trainer.ui.theme import zone_index  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "col-fictif.gpx"
READING = Reading(238.0, 92.0)


def show(key, session, reading=READING, **kw):
    view = RideView(session, reading, kw.pop("ftp", 250), **kw)
    assert view.mode in CATALOG[key].modes
    return CATALOG[key].show(view)


def with_samples(session, powers=(200, 260), bpm=(130, 150)):
    for i, (p, h) in enumerate(zip(powers, bpm)):
        session.record(p, 90, h, at=1.7e9 + i, t=i + 1)
    return session


def workout():
    return WorkoutSession(parse_workout("10m@150 5m@280"), 250)


def route():
    return RouteSession(load_route(EXAMPLE), 250)


def test_mode_follows_the_session():
    assert RideView(workout(), None, 250).mode is Mode.WORKOUT
    assert RideView(FreeRideSession(250), None, 250).mode is Mode.FREE
    assert RideView(route(), None, 250).mode is Mode.ROUTE


def test_power_shows_ftp_share_and_the_average_outside_a_workout():
    shown = show("power", with_samples(workout()))
    assert (shown.value, shown.sub, shown.zone) == ("238 W", "95 % FTP", zone_index(238, 250))
    for session in (with_samples(FreeRideSession(250)), with_samples(route())):
        assert show("power", session).sub == "95 % FTP · moy. 230 W"
    shown = show("power", with_samples(FreeRideSession(250)), None)
    assert (shown.value, shown.sub, shown.zone) == ("—", "moy. 230 W", None)
    assert show("power", workout(), None).sub == ""


def test_cadence_shows_speed_and_gear_where_the_screen_had_them():
    assert show("cadence", workout(), speed_kmh=34.46, gear="12 / 24").sub == "tr/min · 34,5 km/h · braquet 12 / 24"
    shown = show("cadence", workout(), None)
    assert (shown.value, shown.sub) == ("—", "tr/min")
    erg = FreeRideSession(250)
    assert show("cadence", erg, speed_kmh=30.0, gear="12 / 24").sub == "tr/min · 30,0 km/h"  # ERG : pas de braquet
    slope = FreeRideSession(250, mode=FreeMode.SLOPE)
    assert show("cadence", slope, gear="12 / 24").sub == "tr/min · braquet 12 / 24"
    # Parcours : la vitesse a sa propre carte.
    assert show("cadence", route(), speed_kmh=17.3, gear="12 / 24").sub == "tr/min · braquet 12 / 24"
    assert show("cadence", route(), Reading(238.0, None)).value == "—"


def test_heart_rate_off_then_bpm_with_average_battery_and_contact():
    shown = show("heart", workout(), heart_off="aucun capteur · pastille Cardio en haut")
    assert (shown.value, shown.sub) == ("Off", "aucun capteur · pastille Cardio en haut")
    shown = show("heart", workout(), heart=HeartRateReading(142))
    assert (shown.value, shown.sub) == ("142", "bpm")
    shown = show("heart", with_samples(FreeRideSession(250)), heart=HeartRateReading(142, battery_pct=15, contact=False))
    assert shown.sub == "bpm · moy. 140 · batterie 15 % · mauvais contact"


def test_time_and_what_remains():
    free = FreeRideSession(250)
    free.start()
    free.tick(1234)
    assert (show("elapsed", free).value, show("elapsed", free).sub) == ("20:34", "")
    w = workout()
    w.start()
    w.tick(750)
    shown = show("workout_remaining", w)
    assert (shown.value, shown.sub, shown.progress) == ("2:30", "écoulé : 12:30", pytest.approx(750 / 900))
    r = route()
    r.start()
    r.tick(1500)
    r.distance_m = 8000
    assert show("elapsed", r).sub == f"D+ fait {r.climb_done_m:.0f} m"
    shown = show("route_remaining", r)
    assert shown.value == km_text(r.remaining_m) and shown.sub.endswith("fait 8,0 km")
    shown = show("speed", r, speed_kmh=17.34)
    assert (shown.value, shown.sub) == ("17,3", "km/h · moy. 19,2")


def test_each_indicator_has_a_title_and_a_known_gauge():
    for key, indicator in CATALOG.items():
        assert indicator.key == key and indicator.title and indicator.modes
        assert indicator.gauge in (None, "zones", "progress")
