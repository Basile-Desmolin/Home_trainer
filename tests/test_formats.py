from pathlib import Path

import pytest

from home_trainer import PowerTarget, PowerUnit, Repeat, Step, Workout
from home_trainer.bricks import format_bricks, parse_workout
from home_trainer.formats import (FormatError, decode_workout, encode_workout, load_workout,
                                  save_workout)
from home_trainer.workout import Intensity

DATA = Path(__file__).parent / "data"

ZWIFT_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<workout_file>
    <author>Zwift</author>
    <name>The Gorby</name>
    <description>Un classique</description>
    <sportType>bike</sportType>
    <tags><tag name="INTERVALS"/></tags>
    <workout>
        <Warmup Duration="600" PowerLow="0.25" PowerHigh="0.75" pace="0">
            <textevent timeoffset="10" message="On se chauffe"/>
        </Warmup>
        <IntervalsT Repeat="3" OnDuration="60" OffDuration="60" OnPower="1.2" OffPower="0.5" Cadence="95"/>
        <SteadyState Duration="300" Power="0.88"/>
        <SteadyState Duration="30" Power="1.5"/>
        <SteadyState Duration="30" Power="0.5"/>
        <SteadyState Duration="30" Power="1.5"/>
        <SteadyState Duration="30" Power="0.5"/>
        <freeride duration="120" FlatRoad="1"/>
        <MaxEffort Duration="20"/>
        <Ramp Duration="120" PowerLow="0.6" PowerHigh="0.9"/>
        <Cooldown Duration="300" PowerLow="0.7" PowerHigh="0.3"/>
    </workout>
</workout_file>
"""


def test_reads_zwift_file():
    warnings = []
    w = decode_workout(ZWIFT_SAMPLE, ".zwo", warnings)
    assert warnings == []
    assert (w.name, w.description) == ("The Gorby", "Un classique")
    assert format_bricks(w.steps) == (
        "10m@25%>75% 3x(1m@120% 1m@50%) 5m@88% 2x(30s@150% 30s@50%) 2m 20s 2m@60%>90% 5m@70%>30%")
    assert w.steps[0].intensity is Intensity.WARMUP and w.steps[0].notes == "On se chauffe"
    assert w.steps[-1].intensity is Intensity.COOLDOWN


def test_zwo_round_trip_converts_watts_with_ftp():
    w = parse_workout("10m@100W>200W 3x(4m@105% 2m@55%) 5m 5m@150W", name="Mix")
    with pytest.raises(FormatError, match="FTP"):
        encode_workout(w, ".zwo")
    back = decode_workout(encode_workout(w, ".zwo", ftp=250), ".zwo")
    assert back.name == "Mix"
    assert format_bricks(back.steps) == "10m@40%>80% 3x(4m@105% 2m@55%) 5m 5m@60%"


def test_zwo_unrolls_complex_repeats_and_recovers_them():
    w = parse_workout("2x(1m@100% 1m@50% 1m@75%)")
    xml = encode_workout(w, ".zwo")
    assert xml.count(b"<SteadyState") == 6
    assert format_bricks(decode_workout(xml, ".zwo").steps) == "2x(1m@100% 1m@50% 1m@75%)"


@pytest.mark.parametrize("bad", [b"<oops", b"<html/>", b"<workout_file><workout/></workout_file>"])
def test_zwo_rejects_bad_files(bad):
    with pytest.raises(FormatError):
        decode_workout(bad, ".zwo")


def test_reads_user_erg_file():
    w = load_workout(DATA / "velo-route.erg")
    assert w.name == "Vélo - Route"
    assert w.total_duration_s == 77 * 60 + 40
    text = format_bricks(w.steps)
    assert text == "30m 7x(30s@182W 20s) 3m 7x(30s@182W 20s) 33m"
    # 0 W = pas de consigne
    assert all(s.power is None or s.power.low == 182 for s in w.flatten())


def test_erg_round_trip_with_ramps():
    w = parse_workout("5m@100>200 4x(30s@400 30s@100) 10m@150-170 2m", name="Rampes")
    data = encode_workout(w, ".erg")
    assert b"MINUTES WATTS" in data and b"\r\n" in data
    back = decode_workout(data, ".erg")
    assert back.name == "Rampes"
    # Une plage devient sa valeur moyenne, le reste est conservé.
    assert format_bricks(back.steps) == "5m@100W>200W 4x(30s@400W 30s@100W) 10m@160W 2m"


def test_mrc_round_trip_and_ftp_conversion():
    w = parse_workout("10m@50%>75% 3x(10m@90% 3m@55%) 5m@120W")
    with pytest.raises(FormatError, match="FTP"):
        encode_workout(w, ".mrc")
    data = encode_workout(w, ".mrc", ftp=240)
    assert b"MINUTES PERCENT" in data and b"FTP = 240" in data
    back = decode_workout(data, ".mrc")
    assert back.ftp == 240
    assert format_bricks(back.steps) == "10m@50%>75% 3x(10m@90% 3m@55%) 5m@50%"


def test_erg_to_watts_uses_ftp_from_file():
    mrc = encode_workout(parse_workout("10m@50%"), ".mrc", ftp=300)
    erg = encode_workout(decode_workout(mrc, ".mrc"), ".erg")
    assert format_bricks(decode_workout(erg, ".erg").steps) == "10m@150W"


def test_steady_split_in_points_is_not_a_repeat():
    data = b"[COURSE DATA]\n0 100\n1 100\n2 100\n3 100\n3 200\n4 200\n[END COURSE DATA]"
    assert format_bricks(decode_workout(data, ".erg").steps) == "3m@100W 1m@200W"


def test_erg_tolerates_comments_spaces_and_text_section():
    data = b"""[COURSE HEADER]
; commentaire
DESCRIPTION = Test
MINUTES   WATTS
[END COURSE HEADER]
[COURSE DATA]
0     100
1.5   100   ; fin
1.5,  200
3     200
[END COURSE DATA]
[COURSE TEXT]
0\tAllez\t10
[END COURSE TEXT]
"""
    w = decode_workout(data, ".erg")
    assert w.name == "Test"
    assert format_bricks(w.steps) == "1m30s@100W 1m30s@200W"


@pytest.mark.parametrize("bad", [
    b"",
    b"[COURSE DATA]\n0 100\n[END COURSE DATA]",
    b"[COURSE DATA]\n2 100\n1 100\n[END COURSE DATA]",
    b"[COURSE DATA]\n0 abc\n1 100\n[END COURSE DATA]",
])
def test_erg_rejects_bad_files(bad):
    with pytest.raises(FormatError):
        decode_workout(bad, ".erg")


@pytest.mark.parametrize("ext", [".zwo", ".erg", ".mrc"])
def test_open_steps_cannot_be_written(ext):
    with pytest.raises(FormatError, match="open"):
        encode_workout(parse_workout("open@100%"), ext, ftp=200)


@pytest.mark.parametrize("ext", [".zwo", ".erg", ".mrc", ".fit"])
def test_save_and_load_every_format(tmp_path, ext):
    w = parse_workout("10m@50%>70% 3x(10m@90% 3m@55%) 10m@50%", name="Sweet spot")
    path = tmp_path / f"seance{ext}"
    save_workout(w, path, ftp=250)
    back = load_workout(path)
    assert back.name == "Sweet spot"
    assert back.total_duration_s == w.total_duration_s
    targets = [round(s.target_w(0)) for s in back.timeline(250) if s.low_w is not None]
    assert targets[-1] == 125 and targets.count(225) == 3
    assert 125 <= targets[0] < 135  # rampe 125 → 175 W (en paliers dans le .fit)


def test_unknown_extension():
    with pytest.raises(FormatError, match="non pris en charge"):
        encode_workout(Workout(steps=[Step(60)]), ".txt")


def test_timeline_ramp_target():
    seg = parse_workout("10m@100>200").timeline()[0]
    assert (seg.target_w(0), seg.target_w(300), seg.target_w(600), seg.target_w(900)) == (100, 150, 200, 200)
