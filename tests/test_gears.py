"""Braquets virtuels et Zwift Click : décodage des boutons, sans Bluetooth."""

import pytest

from home_trainer.devices import DeviceBook
from home_trainer.gears import GEAR_COUNT, NEUTRAL_GEAR, VirtualGears, gear_factor
from home_trainer.sensors import open_zwift_click
from home_trainer.sensors.zwift_click import (BleZwiftClick, ClickDecoder, ZWIFT_MANUFACTURER_ID, controller_type,
                                              is_zwift_click, protobuf_varints)


def test_gears_go_from_easy_to_hard_around_the_bike_gear():
    gears = VirtualGears()
    assert gears.gear == NEUTRAL_GEAR and gears.factor == 1.0
    assert gears.label == "12 / 24"
    assert gears.shift(+1) == 13 and gears.factor == pytest.approx(1.085)
    gears.set(1)
    assert gears.shift(-1) == 1  # bloqué au plus souple
    gears.set(99)
    assert gears.gear == GEAR_COUNT
    assert gear_factor(1) == pytest.approx(0.41, abs=0.01)
    assert gear_factor(GEAR_COUNT) == pytest.approx(2.66, abs=0.01)
    factors = [gear_factor(g) for g in range(1, GEAR_COUNT + 1)]
    assert factors == sorted(factors)


def test_protobuf_varints():
    assert protobuf_varints(bytes([0x08, 0x00, 0x10, 0x01])) == {1: 0, 2: 1}
    assert protobuf_varints(bytes([0x08, 0xFF, 0xFF, 0x03])) == {1: 0xFFFF}
    assert protobuf_varints(bytes([0x1A, 0x02, 0x08, 0x01, 0x10, 0x05])) == {2: 5}  # champ 3 (message) sauté
    with pytest.raises(ValueError):
        protobuf_varints(bytes([0x08, 0x80]))


def test_click_v1_plus_and_minus_on_press_only():
    decoder = ClickDecoder()
    released = bytes([0x37, 0x08, 0x01, 0x10, 0x01])
    plus = bytes([0x37, 0x08, 0x00, 0x10, 0x01])
    minus = bytes([0x37, 0x08, 0x01, 0x10, 0x00])
    assert decoder.feed(released) == []
    assert decoder.feed(plus) == [+1]
    assert decoder.feed(plus) == []  # toujours appuyé : pas de second cran
    assert decoder.feed(released) == []
    assert decoder.feed(minus) == [-1]
    assert decoder.feed(bytes([0x15])) == []  # message vide
    assert decoder.feed(bytes([0x19, 0x08, 0x4B])) == []
    assert decoder.battery_pct == 75


def test_click_v2_button_map():
    decoder = ClickDecoder()

    def ride(buttons: int) -> bytes:  # 0x23 + champ 1 (varint) : masque, bit à 0 = appuyé
        out, value = bytearray([0x23, 0x08]), buttons
        while True:
            byte = value & 0x7F
            value >>= 7
            out.append(byte | (0x80 if value else 0))
            if not value:
                return bytes(out)

    idle = 0xFFFFFFFF
    assert decoder.feed(ride(idle)) == []
    assert decoder.feed(ride(idle & ~0x1000)) == [+1]  # palette droite : plus dur
    assert decoder.feed(ride(idle)) == []
    assert decoder.feed(ride(idle & ~0x0100)) == [-1]  # palette gauche : plus souple
    assert decoder.feed(ride(idle & ~0x0010)) == []  # bouton A : rien


class _Adv:
    def __init__(self, data: dict) -> None:
        self.manufacturer_data = data


def test_click_is_found_by_its_manufacturer_data():
    assert controller_type(_Adv({ZWIFT_MANUFACTURER_ID: bytes([0x09, 0x01])})) == 0x09
    assert is_zwift_click(_Adv({ZWIFT_MANUFACTURER_ID: bytes([0x0A])}))
    assert not is_zwift_click(_Adv({ZWIFT_MANUFACTURER_ID: bytes([0x06])}))  # home trainer Zwift Hub
    assert not is_zwift_click(_Adv({}))


def test_click_sensor_queues_shifts():
    click = open_zwift_click(address="AA:BB")
    assert isinstance(click, BleZwiftClick) and click.address == "AA:BB"
    click._on_shifts([+1, +1], 80)
    click._on_shifts([-1], 80)
    assert click.take_shifts() == [+1, +1, -1]
    assert click.take_shifts() == []
    assert click.latest().battery_pct == 80


def test_click_is_remembered(tmp_path):
    book = DeviceBook(tmp_path / "appareils.json")
    book.remember("click", "ble", "AA:BB", "Zwift Click")
    book.save()
    again = DeviceBook.load(tmp_path / "appareils.json")
    assert [d.name for d in again.devices("click")] == ["Zwift Click"]
    assert again.startup_choice("click", ("ble", None), default=None) == ("ble", "AA:BB", 0)
