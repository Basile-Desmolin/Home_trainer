from datetime import datetime, timezone

import pytest
from garmin_fit_sdk import Decoder, Stream

from home_trainer import PowerTarget, PowerUnit, Repeat, Step, Workout
from home_trainer.bricks import parse_workout
from home_trainer.formats.fit import FitWorkoutError, decode_workout, encode_workout, read_workout, write_workout
from home_trainer.formats.fit_encoder import ENUM, STRING, UINT16, UINT32, FieldDef, FitWriter, crc16
from home_trainer.workout import Intensity


def official_decode(data: bytes):
    messages, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
    assert errors == []
    return messages


def sample_workout() -> Workout:
    return Workout(
        name="Sweet spot ✓",
        steps=[
            Step(600, PowerTarget.watts(120), name="Échauffement", intensity=Intensity.WARMUP),
            Repeat(3, [
                Step(600, PowerTarget.ftp_percent(88, 92), notes="cadence 90"),
                Step(180, PowerTarget.ftp_percent(55), intensity=Intensity.RECOVERY),
            ]),
            Repeat(2, [Step(30, PowerTarget.watts(400)), Repeat(2, [Step(15, PowerTarget.watts(600)), Step(15)])]),
            Step(None, PowerTarget.watts(100)),
            Step(300, PowerTarget.watts(200, 220), intensity=Intensity.COOLDOWN),
        ],
    )


def test_crc_known_value():
    # Valeur de référence : CRC FIT de la chaîne ASCII "123456789".
    assert crc16(b"123456789") == 0xBB3D


def test_official_sdk_reads_our_file():
    created = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
    msgs = official_decode(encode_workout(sample_workout(), created))

    assert msgs["file_id_mesgs"][0]["type"] == "workout"
    assert msgs["file_id_mesgs"][0]["time_created"] == created
    wkt = msgs["workout_mesgs"][0]
    assert (wkt["sport"], wkt["sub_sport"], wkt["wkt_name"]) == ("cycling", "indoor_cycling", "Sweet spot ✓")

    steps = msgs["workout_step_mesgs"]
    assert wkt["num_valid_steps"] == len(steps) == 11
    first = steps[0]
    assert first["duration_time"] == 600
    assert first["target_type"] == "power"
    assert first["custom_target_power_low"] == first["custom_target_power_high"] == 1120
    assert first["wkt_step_name"] == "Échauffement"
    assert first["intensity"] == "warmup"
    assert steps[1]["custom_target_power_low"] == 88 and steps[1]["notes"] == "cadence 90"
    assert steps[3]["duration_type"] == "repeat_until_steps_cmplt"
    assert (steps[3]["duration_step"], steps[3]["repeat_steps"]) == (1, 3)
    assert steps[9]["duration_type"] == "open"


def test_round_trip_preserves_workout():
    original = sample_workout()
    warnings = []
    decoded = decode_workout(encode_workout(original), warnings)
    assert warnings == []
    assert decoded == original


def test_round_trip_through_file(tmp_path):
    path = tmp_path / "seance.fit"
    workout = parse_workout("10m@150 5x(1m@300 1m@150) 10m@120", name="Test")
    write_workout(workout, path)
    assert read_workout(path) == workout
    assert workout.total_duration_s == 30 * 60


def test_zero_watts_is_written_as_zero_percent():
    w = Workout(steps=[Step(60, PowerTarget.watts(0))])
    step = decode_workout(encode_workout(w)).steps[0]
    assert step.power.to_watts(250) == (0, 0)


def test_timeline_converts_ftp_and_unrolls_repeats():
    w = parse_workout("1m@100 2x(30s@50% 30s@200)")
    tl = w.timeline(ftp=300)
    assert [(s.start_s, s.low_w) for s in tl] == [(0, 100), (60, 150), (90, 200), (120, 150), (150, 200)]
    with pytest.raises(ValueError):
        w.timeline()


def test_long_workout_reuses_one_definition():
    w = Workout(steps=[Step(60, PowerTarget.watts(100 + i)) for i in range(40)])
    data = encode_workout(w)
    assert len(official_decode(data)["workout_step_mesgs"]) == 40
    assert decode_workout(data) == w


def test_rejects_non_fit_and_activity_files():
    with pytest.raises(FitWorkoutError, match="pas un fichier FIT"):
        decode_workout(b"hello world, not a fit file")

    writer = FitWriter()
    writer.write(0, [(FieldDef(0, ENUM), 4)])  # file_id de type « activity »
    with pytest.raises(FitWorkoutError, match="activité"):
        decode_workout(writer.to_bytes())


def test_corrupted_file_is_rejected():
    data = bytearray(encode_workout(sample_workout()))
    data[30] ^= 0xFF
    with pytest.raises(FitWorkoutError):
        decode_workout(bytes(data))


def test_reads_foreign_features_with_warnings():
    """Fichier comme en produisent d'autres plateformes : zones, distance, répétition au temps."""
    f_idx, f_dt, f_dv = FieldDef(254, UINT16), FieldDef(1, ENUM), FieldDef(2, UINT32)
    f_tt, f_tv = FieldDef(3, ENUM), FieldDef(4, UINT32)
    f_lo, f_hi = FieldDef(5, UINT32), FieldDef(6, UINT32)
    writer = FitWriter()
    writer.write(0, [(FieldDef(0, ENUM), 5)])
    writer.write(26, [(FieldDef(4, ENUM), 2), (FieldDef(8, STRING, 8), "Import")])
    rows = [
        (0, 0, 300000, 4, 3, None, None),     # 5 min en zone 3
        (1, 1, 500000, 4, 0, 1250, 1250),     # 5 km à 250 W
        (2, 0, 60000, 2, 0, None, None),      # 1 min sans cible
        (3, 7, 1, 0, 600000, None, None),     # répéter 1-2 jusqu'à 10 min
        (4, 0, 120000, 4, 0, 1300, 80),       # bornes incohérentes
    ]
    for idx, dt, dv, tt, tv, lo, hi in rows:
        writer.write(27, [(f_idx, idx), (f_dt, dt), (f_dv, dv), (f_tt, tt), (f_tv, tv), (f_lo, lo), (f_hi, hi)])

    warnings: list[str] = []
    w = decode_workout(writer.to_bytes(), warnings)
    assert w.name == "Import"
    assert w.steps[0] == Step(300, PowerTarget(76, 90, PowerUnit.FTP_PERCENT))
    rep = w.steps[1]
    assert isinstance(rep, Repeat) and rep.count == 1
    assert rep.steps == [Step(None, PowerTarget.watts(250)), Step(60)]
    assert w.steps[2].power == PowerTarget.watts(300)
    assert len(warnings) == 4


def test_ramp_is_written_as_plateaus():
    w = Workout(steps=[Step(150, PowerTarget.watts(100), power_end=PowerTarget.watts(200))])
    steps = decode_workout(encode_workout(w)).steps
    assert [s.duration_s for s in steps] == [50, 50, 50]
    assert [s.power.low for s in steps] == [117, 150, 183]
