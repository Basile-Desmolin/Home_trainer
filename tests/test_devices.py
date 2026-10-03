import json

from home_trainer.devices import DeviceBook, ant_ident, default_path
from home_trainer.sensors.ant import DeviceNumberProbe, parse_channel_id


def test_remember_keeps_chosen_name_and_moves_to_front(tmp_path):
    book = DeviceBook(tmp_path / "appareils.json")
    kickr = book.remember("trainer", "ble", "AA:BB:CC:DD:EE:FF", "Wahoo Bluetooth KICKR CORE 5D21")
    book.remember("trainer", "ant", "12345", "Wahoo ANT+ n°12345")
    book.rename(kickr, "  Kickr du salon ")
    again = book.remember("trainer", "ble", "aa:bb:cc:dd:ee:ff", "autre nom")  # casse différente
    assert again is kickr and kickr.name == "Kickr du salon"
    assert [d.ident for d in book.devices("trainer")] == ["AA:BB:CC:DD:EE:FF", "12345"]
    assert book.devices("hr") == []
    assert book.last("trainer") == ("ble", "AA:BB:CC:DD:EE:FF")


def test_save_and_reload(tmp_path):
    path = tmp_path / "sous-dossier" / "appareils.json"
    book = DeviceBook(path)
    belt = book.remember("hr", "ant", "4321", "Cardio ANT+ n°4321")
    book.rename(belt, "Ceinture Garmin")
    book.set_last("trainer", "sim")
    book.save()
    loaded = DeviceBook.load(path)
    assert [(d.kind, d.ident, d.name) for d in loaded.devices("hr")] == [("ant", "4321", "Ceinture Garmin")]
    assert loaded.last("hr") == ("ant", "4321") and loaded.last("trainer") == ("sim", None)
    assert loaded.devices("hr")[0].connect_args() == {"address": None, "device_number": 4321}


def test_empty_name_falls_back_to_ident(tmp_path):
    book = DeviceBook(tmp_path / "a.json")
    d = book.remember("hr", "ble", "11:22", "")
    assert d.name == "11:22"
    book.rename(d, "   ")
    assert d.name == "11:22"


def test_forget_clears_last_ident(tmp_path):
    book = DeviceBook(tmp_path / "a.json")
    d = book.remember("trainer", "ble", "AA", "Kickr")
    book.forget(d)
    assert book.devices("trainer") == []
    assert book.last("trainer") == ("ble", None)  # on reste en Bluetooth, premier trouvé


def test_missing_or_broken_file_is_empty(tmp_path):
    assert DeviceBook.load(tmp_path / "absent.json").devices("trainer") == []
    broken = tmp_path / "casse.json"
    broken.write_text("{pas du json", encoding="utf-8")
    assert DeviceBook.load(broken).last("hr") is None
    odd = tmp_path / "bizarre.json"
    odd.write_text(json.dumps({"devices": [{"role": "trainer"}, {"role": "x", "kind": "ble", "ident": "A",
                                                                "name": "n"}], "last": []}), encoding="utf-8")
    assert DeviceBook.load(odd).devices("trainer") == []


def test_startup_choice(tmp_path):
    book = DeviceBook(tmp_path / "a.json")
    assert book.startup_choice("trainer", ("sim", "ble", "ant")) == ("sim", None, 0)
    book.set_last("trainer", "ble", "AA")
    assert book.startup_choice("trainer", ("sim", "ble", "ant")) == ("ble", "AA", 0)
    book.set_last("trainer", "ant", "12345")
    assert book.startup_choice("trainer", ("sim", "ble", "ant")) == ("ant", None, 12345)
    book.set_last("hr", None)
    assert book.startup_choice("hr", ("sim", "ble", "ant", None)) == (None, None, 0)
    assert book.startup_choice("hr", ("sim", "ble", "ant")) == ("sim", None, 0)  # « aucun » interdit ici
    book.set_last("hr", "sim")  # ancien cardio simulé, retiré : on démarre sans cardio
    assert book.startup_choice("hr", ("ble", "ant", None), default=None) == (None, None, 0)


def test_default_path_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME_TRAINER_DEVICES", str(tmp_path / "x.json"))
    assert default_path() == tmp_path / "x.json"
    monkeypatch.delenv("HOME_TRAINER_DEVICES")
    assert default_path().name == "appareils.json"


def test_ant_ident():
    assert ant_ident(0) is None and ant_ident(12345) == "12345"


def test_ant_channel_id_response():
    # Réponse Channel ID après le numéro de canal : numéro 0x3039 = 12345, type 17 (FE-C), transmission 5
    assert parse_channel_id([0x39, 0x30, 17, 5]) == 12345


def test_device_number_probe_asks_once_and_only_in_wildcard():
    found = []

    class FakeChannel:
        asked = 0

        def request_message(self, _message_id):
            FakeChannel.asked += 1
            return 0, 0x51, [0x39, 0x30, 17, 5]

    probe = DeviceNumberProbe(0, found.append)
    probe._ask(FakeChannel())
    assert found == [12345]

    fixed = DeviceNumberProbe(4321, found.append)
    fixed.seen(FakeChannel())  # numéro déjà connu : rien à demander
    assert FakeChannel.asked == 1
