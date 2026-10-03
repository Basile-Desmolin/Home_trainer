import json
import os

from home_trainer.bricks import parse_workout
from home_trainer.formats import save_workout
from home_trainer.library import Library, default_folder


def _library(tmp_path):
    folder = tmp_path / "Séances"
    (folder / "Seuil").mkdir(parents=True)
    (folder / ".cache").mkdir()
    save_workout(parse_workout("10m@50% 3x(10m@90% 3m@55%) 10m@50%", name="Sweet spot"), folder / "sweet.zwo")
    w = parse_workout("15m@150 10x(30s@350 30s@150) 10m@120", name="VO2 30/30")
    w.description = "Intervalles très courts"
    save_workout(w, folder / "Seuil" / "vo2.erg", ftp=250)
    save_workout(parse_workout("60m@100%", name="Heure à la FTP"), folder / "Seuil" / "heure.mrc")
    (folder / "sortie.fit").write_bytes(b"pas une seance")  # ex. une sortie enregistrée : ignorée
    (folder / "notes.txt").write_text("rien")
    save_workout(parse_workout("5m@50%"), folder / ".cache" / "cache.zwo")
    return Library(folder, tmp_path / "bibliotheque.json")


def test_scan_reads_subfolders_and_reports_unreadable_files(tmp_path):
    library = _library(tmp_path)
    result = library.scan()
    assert [(e.folder, e.name) for e in result.entries] == [
        ("", "Sweet spot"), ("Seuil", "Heure à la FTP"), ("Seuil", "VO2 30/30")]
    assert [p.name for p, _ in result.errors] == ["sortie.fit"]
    vo2 = result.entries[2]
    assert vo2.duration_s == 35 * 60 and vo2.format == "erg"


def test_search_ignores_case_and_accents_and_matches_every_word(tmp_path):
    entries = _library(tmp_path).scan().entries
    names = lambda q: [e.name for e in entries if e.matches(q)]  # noqa: E731
    assert names("heure a la") == ["Heure à la FTP"]
    assert names("seuil") == ["Heure à la FTP", "VO2 30/30"]
    assert names("vo2 COURTS") == ["VO2 30/30"]
    assert names("") == ["Sweet spot", "Heure à la FTP", "VO2 30/30"]
    assert names("vo2 sweet") == []


def test_stress_score(tmp_path):
    entries = {e.name: e for e in _library(tmp_path).scan().entries}
    assert round(entries["Heure à la FTP"].stress_score(250)) == 100
    # Rampe de 0 à 100 % sur une heure : moyenne du carré = 1/3.
    from home_trainer.library import LibraryEntry
    ramp = LibraryEntry(tmp_path / "r.zwo", "", parse_workout("60m@0%>100%"))
    assert round(ramp.stress_score(250), 1) == 33.3
    assert LibraryEntry(tmp_path / "x.zwo", "", parse_workout("10m")).stress_score(250) is None


def test_rescan_rereads_only_changed_files(tmp_path, monkeypatch):
    library = _library(tmp_path)
    library.scan()
    read = []
    original = library._read
    monkeypatch.setattr(library, "_read", lambda p: read.append(p.name) or original(p))
    library.scan()
    assert read == []
    path = library.folder / "sweet.zwo"
    save_workout(parse_workout("20m@60%", name="Sweet spot court"), path)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    assert "Sweet spot court" in [e.name for e in library.scan().entries]
    assert read == ["sweet.zwo"]


def test_folder_is_remembered(tmp_path):
    settings = tmp_path / "config" / "bibliotheque.json"
    library = Library.load(settings, documents=tmp_path / "Docs")
    assert library.folder == default_folder(tmp_path / "Docs") == tmp_path / "Docs" / "HomeTrainer" / "Séances"
    library.set_folder(tmp_path / "Mes séances")
    library.save()
    assert json.loads(settings.read_text(encoding="utf-8")) == {"folder": str(tmp_path / "Mes séances")}
    assert Library.load(settings).folder == tmp_path / "Mes séances"
    settings.write_text("{abîmé", encoding="utf-8")
    assert Library.load(settings, documents=tmp_path / "Docs").folder == default_folder(tmp_path / "Docs")


def test_missing_folder_is_empty_then_created(tmp_path):
    library = Library(tmp_path / "a" / "b")
    assert library.scan().entries == []
    assert library.ensure_folder() and library.folder.is_dir()
    assert library.contains(library.folder / "x.zwo") and not library.contains(tmp_path / "x.zwo")
