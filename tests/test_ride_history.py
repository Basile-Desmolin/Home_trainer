"""Bilan d'une sortie (puissances, zones, TSS) et historique des sorties (records, semaines, forme)."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

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


def heart_ride():
    """190 s à 200 W : 60 s à 130 bpm, 60 s à 150, 30 s à 170, 20 s à 180, 10 s à 185, 10 s sans FC."""
    rates = [130] * 60 + [150] * 60 + [170] * 30 + [180] * 20 + [185] * 10 + [None] * 10
    return [S(i + 1, 200, heart_rate_bpm=bpm) for i, bpm in enumerate(rates)]


def test_heart_rate_zones_percent_of_max_and_calories():
    """Étape 11g. FC max 190 : 130 bpm = 68 % (i1), 150 = 79 % (i2), 170 = 89 % (i3), 180 = 95 % (i4),
    185 = 97 % (i5) ; les 10 s sans FC ne vont dans aucune zone. FC moyenne 27 350 / 180 = 151,9 bpm,
    soit 80 % de la FC max ; max 185 = 97 %. 190 s à 200 W = 38 kJ ≈ 38 kcal."""
    r = summarize(heart_ride(), 250, "FC", 1_759_480_000, hr_max=190)
    assert r.hr_max == 190 and r.hr_zone_s == [60, 60, 30, 20, 10]
    assert r.avg_hr_bpm == pytest.approx(27_350 / 180)
    assert r.hr_pct(r.avg_hr_bpm) == pytest.approx(79.97, abs=0.01) and r.hr_pct(r.max_hr_bpm) == pytest.approx(97.37, abs=0.01)
    assert r.work_kj == pytest.approx(38) and r.calories_kcal == pytest.approx(38)
    # Sans FC max (profil sans FC max) ou sans cardio : pas de zones de FC.
    assert summarize(heart_ride(), 250).hr_zone_s == [] and summarize(heart_ride(), 250).hr_pct(150) is None
    assert summarize(ride([200] * 60, heart_rate_bpm=None), 250, hr_max=190).hr_zone_s == []


def test_heart_zones_survive_the_history_and_old_entries_still_load():
    r = summarize(heart_ride(), 250, "FC", 1_759_480_000, hr_max=190)
    again = RideSummary.from_dict(json.loads(json.dumps(r.to_dict())))
    assert again.hr_zone_s == [60, 60, 30, 20, 10] and again.hr_max == 190
    old = r.to_dict()
    del old["hr_zone_s"], old["hr_max"]  # sortie enregistrée avant l'étape 11
    before = RideSummary.from_dict(old)
    assert before.hr_zone_s == [] and before.hr_max is None
    broken = dict(r.to_dict(), hr_zone_s=[1, 2])  # liste incomplète (fichier modifié à la main)
    assert RideSummary.from_dict(broken).hr_zone_s == []


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


def test_unreadable_history_without_backup_is_empty_and_kept_aside(tmp_path):
    """Audit, « Historique écrasé » : un fichier illisible était écrasé par la sortie suivante."""
    path = tmp_path / "historique.json"
    path.write_text("pas du json", encoding="utf-8")
    h = RideHistory.load(path)
    assert h.rides == [] and "sans copie de secours" in h.problem
    h.add(summarize(ride([200] * 600), 250, "Nouvelle", at(date(2026, 11, 8))))
    assert h.set_aside is not None and h.set_aside.read_text(encoding="utf-8") == "pas du json"
    assert h.set_aside.name.startswith("historique.illisible-")
    assert [r.title for r in RideHistory.load(path).rides] == ["Nouvelle"]


def test_unreadable_history_is_read_back_from_its_backup(tmp_path):
    path = tmp_path / "historique.json"
    h = RideHistory.load(path)
    h.add(summarize(ride([200] * 600), 250, "Un", at(date(2026, 10, 1))))
    h.add(summarize(ride([210] * 600), 250, "Deux", at(date(2026, 10, 2))))
    # La copie de secours est la version d'avant la dernière écriture.
    assert [r["title"] for r in json.loads((tmp_path / "historique.bak.json").read_text("utf-8"))["rides"]] == ["Un"]
    path.write_text('{"rides": [', encoding="utf-8")  # fichier tronqué (coupure, OneDrive)
    back = RideHistory.load(path)
    assert [r.title for r in back.rides] == ["Un"] and "copie de secours" in back.problem
    back.add(summarize(ride([220] * 600), 250, "Trois", at(date(2026, 10, 3))))
    assert [r.title for r in RideHistory.load(path).rides] == ["Un", "Trois"]
    assert back.set_aside.read_text(encoding="utf-8") == '{"rides": ['


def test_unreadable_ride_is_kept_as_is(tmp_path):
    path = tmp_path / "historique.json"
    good = summarize(ride([200] * 600), 250, "Bonne", at(date(2026, 10, 1)))
    broken = {"title": "abîmée", "start": "pas une date"}
    path.write_text(json.dumps({"rides": [good.to_dict(), broken]}), encoding="utf-8")
    h = RideHistory.load(path)
    assert [r.title for r in h.rides] == ["Bonne"] and h.problem is None
    h.add(summarize(ride([210] * 600), 250, "Nouvelle", at(date(2026, 10, 2))))
    assert broken in json.loads(path.read_text(encoding="utf-8"))["rides"]


def test_failed_history_write_is_reported_and_changes_nothing(tmp_path, monkeypatch):
    """Audit, « Échec d'écriture de l'historique silencieux »."""
    path = tmp_path / "historique.json"
    h = RideHistory.load(path)
    h.add(summarize(ride([200] * 600), 250, "Un", at(date(2026, 10, 1))))

    def locked(*_a, **_k):
        raise PermissionError("fichier verrouillé")

    monkeypatch.setattr(Path, "write_text", locked)
    with pytest.raises(OSError):
        h.add(summarize(ride([210] * 600), 250, "Deux", at(date(2026, 10, 2))))
    assert [r.title for r in h.rides] == ["Un"]
    with pytest.raises(OSError):
        h.remove(h.rides[0])
    assert [r.title for r in h.rides] == ["Un"]
