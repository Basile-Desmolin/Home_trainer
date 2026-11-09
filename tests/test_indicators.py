"""Catalogue d'indicateurs : texte de chaque carte, dans chaque mode (repris à l'identique des trois écrans)."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.heart_zones import esie_zone  # noqa: E402
from home_trainer.ride_stats import normalized_power  # noqa: E402
from home_trainer.route import load_route  # noqa: E402
from home_trainer.sensors import HeartRateReading  # noqa: E402
from home_trainer.ui.chart import HEART, TEXT, zone_color  # noqa: E402
from home_trainer.ui.indicators import (CATALOG, Mode, RideView, grade_color, grade_text, km_text,  # noqa: E402
                                        route_grade_color)
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


def test_power_shows_ftp_share_and_the_average_in_every_mode():
    for session in (with_samples(workout()), with_samples(FreeRideSession(250)), with_samples(route())):
        shown = show("power", session)
        assert (shown.value, shown.sub, shown.zone) == ("238 W", "95 % FTP · moy. 230 W", zone_index(238, 250))
    shown = show("power", with_samples(FreeRideSession(250)), None)
    assert (shown.value, shown.sub, shown.zone) == ("—", "moy. 230 W", None)
    assert show("power", workout(), None).sub == ""


def test_cadence_shows_its_average_then_speed_and_gear():
    shown = show("cadence", with_samples(workout()), speed_kmh=34.46, gear="12 / 24")
    assert (shown.value, shown.sub) == ("92 tr/min", "moy. 90 · 34,5 km/h · braquet 12 / 24")
    shown = show("cadence", workout(), None)
    assert (shown.value, shown.sub) == ("—", "")
    erg = FreeRideSession(250)
    assert show("cadence", erg, speed_kmh=30.0, gear="12 / 24").sub == "30,0 km/h"  # ERG : pas de braquet
    slope = FreeRideSession(250, mode=FreeMode.SLOPE)
    assert show("cadence", slope, gear="12 / 24").sub == "braquet 12 / 24"
    # Vitesse sur sa propre carte : plus sous la cadence.
    on_screen = frozenset({"cadence", "speed"})
    assert show("cadence", route(), speed_kmh=17.3, gear="12 / 24", on_screen=on_screen).sub == "braquet 12 / 24"
    assert show("cadence", route(), Reading(238.0, None)).value == "—"


def test_heart_rate_off_then_bpm_with_average_max_battery_and_contact():
    shown = show("heart", workout(), heart_off="aucun capteur · pastille Cardio en haut")
    assert (shown.value, shown.sub) == ("Off", "aucun capteur · pastille Cardio en haut")
    shown = show("heart", workout(), heart=HeartRateReading(142))
    assert (shown.value, shown.sub) == ("142 bpm", "")
    shown = show("heart", with_samples(FreeRideSession(250)), heart=HeartRateReading(142, battery_pct=15, contact=False))
    assert shown.sub == "moy. 140 · max 150 · batterie 15 % · mauvais contact"


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
    assert (shown.value, shown.unit, shown.sub) == ("17,3", "km/h", "moy. 19,2")
    with_speeds = with_samples(r)  # vitesse enregistrée : celle du parcours (0 à l'arrêt)
    assert show("speed", with_speeds, speed_kmh=17.34).sub == "moy. 19,2"  # pas de « max 0,0 »


def test_speed_average_and_max_outside_a_route():
    free = FreeRideSession(250)
    for i, speed in enumerate((30.0, 34.0, 32.0)):
        free.record(200, 90, 140, speed, at=1.7e9 + i, t=i + 1)
    shown = show("speed", free, speed_kmh=31.96)
    assert (shown.value, shown.unit, shown.sub) == ("32,0", "km/h", "moy. 32,0 · max 34,0")
    shown = show("speed", FreeRideSession(250))
    assert (shown.value, shown.sub) == ("—", "")


def test_watts_per_kg_uses_the_rider_weight():
    shown = show("watts_per_kg", with_samples(workout()), weight_kg=66.0)
    assert (shown.value, shown.sub) == ("3,6 W/kg", "moy. 3,5 · 66,0 kg")  # 238 / 66 = 3,61 ; 230 / 66 = 3,48
    assert show("watts_per_kg", workout(), None, weight_kg=66.0).value == "—"
    assert show("watts_per_kg", workout()).sub == "poids du cycliste : en haut"


def test_normalized_and_max_power():
    session = workout()
    assert (show("normalized_power", session).value, show("max_power", session).value) == ("—", "—")
    # 30 s à 100 W puis 30 s à 300 W : 31 moyennes glissantes sur 30 s, 100, 106,7 … 300 ;
    # moyenne de leurs puissances 4 : 2,476 × 10⁹ ; racine 4 : 223,07 W (la moyenne simple vaut 200 W).
    for i, p in enumerate([100] * 30 + [300] * 30):
        session.record(p, 90, None, at=1.7e9 + i, t=i + 1)
    rolled = [100 + 200 * k / 30 for k in range(31)]
    expected = (sum(x ** 4 for x in rolled) / 31) ** 0.25
    assert expected == pytest.approx(223.07, abs=0.01)
    assert session.stats.normalized_power == pytest.approx(expected, rel=1e-12)
    assert normalized_power([100.0] * 30 + [300.0] * 30) == pytest.approx(expected, rel=1e-12)
    shown = show("normalized_power", session)
    assert (shown.value, shown.sub) == ("223 W", "89 % FTP · puissance normalisée")
    shown = show("max_power", session)
    assert (shown.value, shown.sub) == ("300 W", "120 % FTP")


def test_brick_average_against_its_target():
    w = workout()  # 10 min à 150 W puis 5 min à 280 W
    assert show("brick", w).value == "—"
    w.start()
    for p in (140, 160):
        w.tick(1)
        w.record(p, 90)
    assert (show("brick", w).value, show("brick", w).sub) == ("150 W", "cible 150 W · 100 %")
    w.next_step()
    assert show("brick", w).value == "—"  # pas encore de mesure dans la nouvelle brique
    w.tick(1)
    w.record(266, 90)
    assert (show("brick", w).value, show("brick", w).sub) == ("266 W", "cible 280 W · 95 %")


def test_balance_left_right():
    shown = show("balance", workout())
    assert (shown.value, shown.sub) == ("—", "capteur double (gauche et droite)")
    session = workout()
    for i, left in enumerate((48.0, 50.0)):
        session.record(200, 90, None, at=1.7e9 + i, t=i + 1, balance_left_pct=left)
    shown = show("balance", session, Reading(238.0, 92.0, balance_left_pct=47.6))
    assert (shown.value, shown.unit, shown.sub) == ("48 / 52", "%", "moy. 49 / 51")


def test_live_stats_match_the_ride_summary_formulas():
    """NP et moyennes au fil de l'eau : mêmes valeurs que les calculs sur la sortie entière, aux arrondis
    près (`sum()` compense les erreurs d'arrondi depuis Python 3.12, pas une addition au fil de l'eau)."""
    import random

    same = dict(rel=1e-12)
    rng = random.Random(6)
    powers = [rng.uniform(80, 450) for _ in range(1500)]
    session = FreeRideSession(250)
    for i, p in enumerate(powers):
        session.record(p, 90, 140 + i % 20, at=1.7e9 + i, t=i + 1)
        if i in (10, 28, 29, 30, 700):  # lu en cours de route, comme l'affichage
            assert session.stats.average_power == pytest.approx(sum(powers[:i + 1]) / (i + 1), **same)
            np = session.stats.normalized_power
            assert np is None if i < 29 else np == pytest.approx(normalized_power(powers[:i + 1]), **same)
    assert session.stats.normalized_power == pytest.approx(normalized_power(powers), **same)
    assert session.average_power() == pytest.approx(sum(powers) / len(powers), **same)
    hr = [140 + i % 20 for i in range(1500)]
    assert session.average_heart_rate() == pytest.approx(sum(hr) / len(hr), **same)
    assert (session.stats.heart_rate_max, session.stats.power_max) == (159, max(powers))
    # Liste remplacée (sortie reprise) ou raccourcie : relue depuis le début.
    session.samples = session.samples[:100]
    assert session.stats.average_power == pytest.approx(sum(powers[:100]) / 100, **same)


def test_each_indicator_has_a_title_and_a_known_gauge():
    for key, indicator in CATALOG.items():
        assert indicator.key == key and indicator.title and indicator.modes
        assert indicator.gauge in (None, "zones", "heart_zones", "progress")


def test_target_in_each_mode_with_its_color():
    """Étape 10 : la cible de la vignette, reprise de `mini_info` (texte, titre et couleur)."""
    s = workout()  # 10 min à 150 W (60 % FTP, zone 2), puis 5 min à 280 W
    shown = show("target", s)
    assert (shown.value, shown.title, shown.color) == ("150 W", None, zone_color(150, 250).name())
    s.start()
    s.tick(660)
    assert show("target", s).value == "280 W"
    easy = WorkoutSession(parse_workout("10m@100"), 250)  # 40 % FTP : zone 1, en blanc
    assert show("target", easy).color is None and CATALOG["target"].value_color(show("target", easy)) == TEXT

    class ByHeart(WorkoutSession):  # séance en FC roulée aux vitesses
        target_w = None
        target_bpm = 132.0
    shown = show("target", ByHeart(parse_workout("10m@100"), 250))
    assert (shown.value, shown.title, shown.color) == ("132 bpm", "CIBLE FC", HEART)

    free = FreeRideSession(250, target_w=200)
    assert (show("target", free).value, show("target", free).color) == ("200 W", zone_color(200, 250).name())
    slope = FreeRideSession(250, mode=FreeMode.SLOPE, grade_pct=4.5)
    shown = show("target", slope)
    assert (shown.value, shown.title, shown.color) == ("4,5 %", "PENTE", grade_color(4.5).name())

    r = route()
    shown = show("target", r)
    assert (shown.value, shown.title) == (grade_text(r.grade_pct), "PENTE")
    assert shown.color == route_grade_color(r.grade_pct).name()


def test_brick_remaining():
    s = workout()
    s.start()
    s.tick(400)  # 6 min 40 dans la brique de 10 min
    assert (show("brick_remaining", s).value, show("brick_remaining", s).sub) == ("3:20", "brique 1 / 2")
    s.tick(900)  # séance finie : plus de brique
    assert show("brick_remaining", s).value == "0:00"
    assert Mode.FREE not in CATALOG["brick_remaining"].modes and Mode.ROUTE not in CATALOG["brick_remaining"].modes


def test_vignette_only_indicators_stay_off_the_screen_menus():
    from home_trainer.ui.metric import MetricGrid, Slot
    for mode in Mode:
        choices = MetricGrid([Slot("power", 0, 0)], mode).choices()
        assert "target" not in choices and "brick_remaining" not in choices and "power" in choices


def test_heart_rate_zones_follow_the_esie_scale():
    # FC max 190 : i1 < 142,5 ; i2 < 161,5 ; i3 < 174,8 ; i4 < 182,4 ; i5 au-delà (75, 85, 92, 96 %).
    assert [esie_zone(bpm, 190) for bpm in (120, 142, 143, 161, 162, 174, 175, 182, 183, 200)] == [
        0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    assert esie_zone(150, None) is None and esie_zone(None, 190) is None and esie_zone(0, 190) is None
    shown = show("heart", with_samples(workout()), heart=HeartRateReading(165), hr_max=190)
    assert (shown.value, shown.sub, shown.zone) == ("165 bpm", "i3 · moy. 140 · max 150", 2)
    shown = show("heart", workout(), heart=HeartRateReading(165))  # sans FC max : pas de zone
    assert (shown.sub, shown.zone) == ("", None)
