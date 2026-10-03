import pytest

from home_trainer.bricks import BrickSyntaxError
from home_trainer.ui.editing import (StepFields, convert_fields, fields_from_step, format_duration, parse_count_field,
                                     parse_duration_field, step_from_fields)
from home_trainer.workout import PowerTarget, PowerUnit, Step

W, PCT = PowerUnit.WATTS, PowerUnit.FTP_PERCENT


@pytest.mark.parametrize("text,seconds", [
    ("10:00", 600), ("1:30:00", 5400), ("0:30", 30), ("10", 600), ("10m", 600), ("30s", 30),
    ("5m30s", 330), ("1h", 3600), ("tour", None), ("open", None),
])
def test_durations(text, seconds):
    assert parse_duration_field(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "0:00", "1:75", "-5"])
def test_bad_durations(text):
    with pytest.raises(BrickSyntaxError):
        parse_duration_field(text)


def test_duration_round_trip():
    for seconds in (30, 600, 5400, 3725):
        assert parse_duration_field(format_duration(seconds)) == seconds
    assert format_duration(None) == "tour"


@pytest.mark.parametrize("text,count", [("3", 3), ("3x", 3), ("×4", 4), ("5 fois", 5), ("2×", 2)])
def test_counts(text, count):
    assert parse_count_field(text) == count


@pytest.mark.parametrize("text", ["0", "", "x", "-2"])
def test_bad_counts(text):
    with pytest.raises(BrickSyntaxError):
        parse_count_field(text)


def test_step_from_fields():
    step = step_from_fields(StepFields("10:00", "150", "", W, " Échauffement "))
    assert step == Step(600, PowerTarget.watts(150), name="Échauffement")
    ramp = step_from_fields(StepFields("10m", "50", "75%", PCT))
    assert ramp.power == PowerTarget.ftp_percent(50) and ramp.power_end == PowerTarget.ftp_percent(75)
    assert step_from_fields(StepFields("5:00", "200-220", "", W)).power == PowerTarget.watts(200, 220)
    assert step_from_fields(StepFields("5:00", "", "", W)).power is None
    assert step_from_fields(StepFields("tour", "120", "", W)).duration_s is None


@pytest.mark.parametrize("fields,message", [
    (StepFields("10:00", "abc", "", W), "puissance"),
    (StepFields("10:00", "", "200", W), "départ"),
    (StepFields("tour", "100", "200", W), "durée"),
    (StepFields("10:00", "100", "x", W), "fin de rampe"),
])
def test_step_errors(fields, message):
    with pytest.raises(BrickSyntaxError, match=message):
        step_from_fields(fields)


def test_fields_round_trip():
    for step in (Step(600, PowerTarget.watts(150), name="a"), Step(300, PowerTarget.ftp_percent(50),
                 power_end=PowerTarget.ftp_percent(75)), Step(None, PowerTarget.watts(120)), Step(60)):
        assert step_from_fields(fields_from_step(step)) == step


def test_convert_units():
    f = convert_fields(StepFields("10:00", "150", "250", W, "x"), PCT, ftp=250)
    assert (f.power, f.power_end, f.unit, f.name) == ("60", "100", PCT, "x")
    back = convert_fields(f, W, ftp=250)
    assert (back.power, back.power_end, back.unit) == ("150", "250", W)
