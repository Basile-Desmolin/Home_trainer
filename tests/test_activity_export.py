"""Sortie au format choisi (.fit, .tcx, .csv), anti-veille, .fit envoyé depuis un autre dossier."""

import csv
import xml.etree.ElementTree as ET

import pytest

from home_trainer.formats.activity_export import RIDE_FORMATS, write_ride
from home_trainer.formats.fit_activity import ActivityPoint
from home_trainer.keep_awake import KeepAwake
from home_trainer.sync import Outbox

POINTS = [ActivityPoint(1_759_480_000 + i, 200 + i, 90, 140 if i else None, 36.0, lap=i // 30) for i in range(60)]
TCX = "{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}"
TPX = "{http://www.garmin.com/xmlschemas/ActivityExtension/v2}"


def test_fit_is_the_default_format():
    assert next(iter(RIDE_FORMATS)) == "fit"


def test_tcx_has_laps_power_cadence_and_heart_rate(tmp_path):
    root = ET.parse(write_ride(POINTS, tmp_path / "s.tcx")).getroot()
    laps = root.findall(f".//{TCX}Lap")
    assert len(laps) == 2
    points = root.findall(f".//{TCX}Trackpoint")
    assert len(points) == 60
    last = points[-1]
    assert last.find(f"{TCX}Cadence").text == "90"
    assert last.find(f"{TCX}HeartRateBpm/{TCX}Value").text == "140"
    assert last.find(f".//{TPX}Watts").text == "259"
    assert points[0].find(f"{TCX}HeartRateBpm") is None
    assert float(last.find(f"{TCX}DistanceMeters").text) == pytest.approx(600, abs=1)


def test_csv_one_line_per_second(tmp_path):
    path = write_ride(POINTS, tmp_path / "s.csv")
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[0][:4] == ["heure", "temps_s", "puissance_w", "cadence_tr_min"]
    assert len(rows) == 61
    assert rows[-1][2:5] == ["259", "90", "140"] and rows[-1][-1] == "2"


def test_fit_and_unknown_format(tmp_path):
    assert write_ride(POINTS, tmp_path / "s.fit").read_bytes()[8:12] == b".FIT"
    with pytest.raises(ValueError):
        write_ride(POINTS, tmp_path / "s.gpx")


def test_keep_awake_calls_windows_only_on_change():
    calls = []
    awake = KeepAwake(lambda flags: calls.append(flags) or 1)
    awake.set(True)
    awake.set(True)
    awake.set(False)
    assert calls == [0x80000003, 0x80000000]


def test_keep_awake_is_a_no_op_elsewhere(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    awake = KeepAwake()
    awake.set(True)
    assert awake.active and not awake.supported


def test_outbox_sends_a_fit_saved_elsewhere(tmp_path):
    box = Outbox(tmp_path / "sorties")
    dest = tmp_path / "ailleurs" / "Ma sortie.fit"
    path = box.add(POINTS, "Sortie", dest=dest)
    assert path == dest.absolute() and dest.exists()
    [(name, service)] = box.pending(["strava"])
    assert box.dir / name == dest.absolute() and service == "strava"
    box.add(POINTS, "Sortie", dest=dest)  # même fichier : remplacé, toujours une seule entrée
    assert len(box.entries()) == 1
