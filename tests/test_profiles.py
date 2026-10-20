"""Profils de cycliste : liste, fichier, reprise des réglages d'avant les profils."""

import json

from home_trainer.profiles import FIRST_PROFILE_NAME, ProfileBook


def test_create_save_and_load(tmp_path):
    book = ProfileBook(tmp_path / "profils.json")
    alice = book.create("Élodie", 230, 58.5)
    book.create("Élodie")  # même nom : un autre dossier
    book.last, book.remember = alice.id, True
    book.save()
    loaded = ProfileBook.load(tmp_path / "profils.json")
    assert [p.id for p in loaded.profiles] == ["elodie", "elodie-2"]
    assert loaded.startup_profile() == alice
    assert loaded.find("ÉLODIE").ftp == 230
    assert loaded.accounts_path(alice) == tmp_path / "profils" / "elodie" / "comptes.json"
    assert loaded.rides_dir(alice) == tmp_path / "profils" / "elodie" / "sorties"


def test_startup_asks_unless_remembered(tmp_path):
    book = ProfileBook(tmp_path / "profils.json")
    book.last = book.create("Basile").id
    assert book.startup_profile() is None
    book.remember = True
    assert book.startup_profile().name == "Basile"
    book.remove(book.profiles[0])
    assert book.startup_profile() is None


def test_removed_profile_folder_is_not_reused(tmp_path):
    book = ProfileBook(tmp_path / "profils.json")
    old = book.create("Basile")
    book.rides_dir(old).mkdir(parents=True)
    book.remove(old)
    assert book.create("Basile").id == "basile-2"


def test_legacy_accounts_and_rides_move_to_first_profile(tmp_path):
    (tmp_path / "comptes.json").write_text(json.dumps({"strava": {"client_id": "42"}}), encoding="utf-8")
    (tmp_path / "sorties").mkdir()
    (tmp_path / "sorties" / "sortie.fit").write_bytes(b"fit")
    book = ProfileBook(tmp_path / "profils.json")
    profile = book.migrate_legacy()
    assert profile.name == FIRST_PROFILE_NAME
    assert json.loads(book.accounts_path(profile).read_text(encoding="utf-8"))["strava"]["client_id"] == "42"
    assert (book.rides_dir(profile) / "sortie.fit").read_bytes() == b"fit"
    assert not (tmp_path / "comptes.json").exists() and not (tmp_path / "sorties").exists()
    assert ProfileBook.load(tmp_path / "profils.json").last == profile.id
    assert book.migrate_legacy() is None  # une seule fois


def test_nothing_to_migrate_on_first_install(tmp_path):
    book = ProfileBook(tmp_path / "profils.json")
    assert book.migrate_legacy() is None
    assert book.profiles == []


def test_unreadable_file_gives_empty_book(tmp_path):
    (tmp_path / "profils.json").write_text("{pas du json", encoding="utf-8")
    assert ProfileBook.load(tmp_path / "profils.json").profiles == []


def test_rides_go_to_documents(tmp_path):
    book = ProfileBook(tmp_path / "config" / "profils.json", rides_root=tmp_path / "Documents" / "HomeTrainer" / "Sorties")
    basile = book.create("Basile")
    assert book.rides_dir(basile) == tmp_path / "Documents" / "HomeTrainer" / "Sorties" / "basile"
    book.rides_dir(basile).mkdir(parents=True)
    book.remove(basile)
    assert book.create("Basile").id == "basile-2"  # dossier de sorties resté : pas repris


def test_appdata_rides_move_to_documents_with_their_history(tmp_path):
    book = ProfileBook(tmp_path / "config" / "profils.json", rides_root=tmp_path / "Documents")
    basile = book.create("Basile")
    old = book.legacy_rides_dir(basile)
    old.mkdir(parents=True)
    (old / "sortie.fit").write_bytes(b"fit")
    elsewhere = tmp_path / "Bureau" / "col.gpx"
    history = {"rides": [{"file": str(old.absolute() / "sortie.fit")}, {"file": str(elsewhere)}]}
    (old / "historique.json").write_text(json.dumps(history), encoding="utf-8")
    assert book.rides_dir(basile) == old  # pas encore déplacées : on les lit là où elles sont
    book.migrate_rides()
    new = tmp_path / "Documents" / "basile"
    assert book.rides_dir(basile) == new and not old.exists()
    assert (new / "sortie.fit").read_bytes() == b"fit"
    files = [r["file"] for r in json.loads((new / "historique.json").read_text(encoding="utf-8"))["rides"]]
    assert files == [str(new.absolute() / "sortie.fit"), str(elsewhere)]
    book.migrate_rides()  # une seule fois
    assert (new / "sortie.fit").exists()


def test_rides_stay_in_appdata_when_the_move_fails(tmp_path, monkeypatch):
    import shutil
    book = ProfileBook(tmp_path / "config" / "profils.json", rides_root=tmp_path / "Documents")
    basile = book.create("Basile")
    old = book.legacy_rides_dir(basile)
    old.mkdir(parents=True)
    (old / "sortie.fit").write_bytes(b"fit")

    def no_rename(self, target):
        raise OSError("autre disque")

    def full_disk(*args, **kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr(type(old), "rename", no_rename)
    monkeypatch.setattr(shutil, "copytree", full_disk)
    book.migrate_rides()
    assert book.rides_dir(basile) == old and (old / "sortie.fit").exists()
    assert not (tmp_path / "Documents" / "basile").exists()
