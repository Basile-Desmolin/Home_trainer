import pytest

from home_trainer import PowerTarget, Repeat, Step
from home_trainer.bricks import BrickSyntaxError, format_bricks, parse_bricks, parse_duration


@pytest.mark.parametrize("text,seconds", [
    ("10m", 600), ("30s", 30), ("1h", 3600), ("5m30s", 330), ("1h2m3s", 3723), ("1.5m", 90), ("15", 900),
    ("open", None),
])
def test_durations(text, seconds):
    assert parse_duration(text) == seconds


def test_simple_and_repeats():
    items = parse_bricks("10m@150, 3x(4m@105% 2m@55%) 30s@200-220W 5m")
    assert items == [
        Step(600, PowerTarget.watts(150)),
        Repeat(3, [Step(240, PowerTarget.ftp_percent(105)), Step(120, PowerTarget.ftp_percent(55))]),
        Step(30, PowerTarget.watts(200, 220)),
        Step(300),
    ]


def test_nested_repeat_and_spacing():
    items = parse_bricks(" 2 x ( 1m@300 2x(10s@600 20s@100) ) ")
    assert items == [Repeat(2, [Step(60, PowerTarget.watts(300)),
                                Repeat(2, [Step(10, PowerTarget.watts(600)), Step(20, PowerTarget.watts(100))])])]


def test_format_round_trip():
    text = "10m@150W 3x(4m@105% 2m@55%) 1h2m3s@200-220W open@100W 5m"
    assert format_bricks(parse_bricks(text)) == text


@pytest.mark.parametrize("text", [
    "10x@100", "10m@abc", "3x(10m@100", "10m@100)", "0x(1m@100)", "2x()", "10m@250-200", "0s@100",
])
def test_errors(text):
    with pytest.raises(BrickSyntaxError):
        parse_bricks(text)


def test_ramps():
    assert parse_bricks("10m@100>200 5m@50>75%") == [
        Step(600, PowerTarget.watts(100), power_end=PowerTarget.watts(200)),
        Step(300, PowerTarget.ftp_percent(50), power_end=PowerTarget.ftp_percent(75)),
    ]
    assert format_bricks(parse_bricks("10m@100>200")) == "10m@100W>200W"
    with pytest.raises(BrickSyntaxError):
        parse_bricks("open@100>200")
