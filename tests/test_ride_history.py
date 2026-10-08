"""Bilan d'une sortie (puissances, zones, TSS) et historique des sorties (records, semaines, forme)."""

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from home_trainer.history import RideHistory
from home_trainer.ride_stats import RideSummary, best_average, normalized_power, summarize, zone_of


@dataclass
class S:
    t: float
    power_w: float
    cadence_rpm: float | None = 90
    heart_rate_bpm: float | None = 140
    speed_kmh: float | None = 36.0


def ride(powers, **kw):
    return [S(i + 1, p, **kw) for i, p in enumerate(powers)]


def test_steady_hour_at_ftp_is_100_tss():
    r = summarize(ride([250] * 3600), 250, "Seuil", 1_759_480_000)
    assert r.duration_s == 3600
    assert r.avg_power_w == pytest.approx(250) and r.normalized_power_w == pytest.approx(250)
    assert r.intensity == pytest.approx(1) and r.tss == pytest.approx(100)
    assert r.work_kj == pytest.approx(900)
    assert r.distance_km == pytest.approx(36)
    assert r.zone_s[3] == 3600 and sum(r.zone_s) == 3600  # tout en Z4 (seuil)
    assert r.best_w[1200] == 250 and r.best_w[3600] == 250
    assert r.avg_hr_bpm == 140 and r.avg_cadence_rpm == 90


def test_intervals_raise_normalized_power_above_average():
    powers = ([400] * 60 + [100] * 60) * 10
    r = summarize(ride(powers), 250)
    assert r.avg_power_w == pytest.approx(250)
    assert r.normalized_power_w > 290
    assert r.best_w[60] == 400 and r.best_w[5] == 400
    assert 3600 not in r.best_w  # sortie plus courte qu'une heure
    assert r.zone_s[0] == 600 and r.zone_s[6] == 600  # 160 % FTP : Z7


def test_helpers():
    assert best_average([1, 5, 5, 1], 2) == 5
    assert best_average([1, 2], 3) is None
    assert normalized_power([200] * 10) == 200
    assert [zone_of(w, 100) for w in (0, 55, 90, 105, 120, 150, 151, 300)] == [0, 1, 2, 3, 4, 5, 6, 6]


def test_without_sensors():
    r = summarize(ride([150] * 60, cadence_rpm=None, heart_rate_bpm=None, speed_kmh=None), 250)
    assert r.distance_km is None and r.avg_hr_bpm is None and r.max_hr_bpm is None and r.avg_cadence_rpm is None


def test_dict_round_trip():
    r = summarize(ride([200] * 400), 250, "Test", 1_759_480_000)
    r.file = "/x/y.fit"
    back = RideSummary.from_dict(r.to_dict())
    assert back == r


def at(day, hour=18):
    return datetime(day.year, day.month, day.day, hour).timestamp()


def test_history_keeps_rides_and_flags_records(tmp_path):
    path = tmp_path / "historique.json"
    h = RideHistory.load(path)
    first = summarize(ride([200] * 600), 250, "Un", at(date(2026, 10, 1)))
    assert h.mark_records(first).records == {}  # première sortie : pas de record à battre
    h.add(first)
    second = summarize(ride([100] * 300 + [300] * 60), 250, "Deux", at(date(2026, 10, 3)))
    h.mark_records(second)
    assert second.records[60] == 200 and second.records[5] == 200
    assert 300 not in second.records  # 5 min : 200 W la première fois, moins cette fois
    h.add(second)

    again = RideHistory.load(path)
    assert [r.title for r in again.rides] == ["Un", "Deux"]
    assert again.best_powers()[60][0] == 300 and again.best_powers()[60][1].title == "Deux"
    again.remove(again.rides[0])
    assert [r.title for r in RideHistory.load(path).rides] == ["Deux"]


def test_weeks_and_fitness():
    h = RideHistory()
    for day in (date(2026, 9, 28), date(2026, 10, 6), date(2026, 10, 7)):
        h.rides.append(summarize(ride([250] * 3600), 250, "", at(day)))
    weeks = h.weeks(3, today=date(2026, 10, 8))
    assert [w[0] for w in weeks] == [date(2026, 9, 21), date(2026, 9, 28), date(2026, 10, 5)]
    assert [w[3] for w in weeks] == [0, 1, 2]
    assert weeks[2][1] == 7200 and weeks[2][2] == pytest.approx(200)
    ctl, atl = h.fitness(today=date(2026, 10, 8))
    assert 0 < ctl < atl  # beaucoup de TSS ces derniers jours : fatigue au-dessus de la condition


def test_unreadable_history_is_empty(tmp_path):
    path = tmp_path / "historique.json"
    path.write_text("pas du json", encoding="utf-8")
    assert RideHistory.load(path).rides == []
