"""Bibliothèque de séances : un dossier (et ses sous-dossiers) de fichiers .zwo, .mrc, .erg, .fit.

    library = Library.load()                  # dossier mémorisé, sinon Documents/HomeTrainer/Séances
    entries = library.scan()                  # une entrée par séance lisible, triées par dossier puis nom
    [e for e in entries if e.matches("vo2 30")]
    library.set_folder("D:/Entraînement"); library.save()

Le dossier choisi est mémorisé dans `bibliotheque.json`, à côté des appareils
mémorisés (voir `home_trainer.devices`). Les fichiers déjà lus ne sont relus
que s'ils ont changé.
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .devices import default_path as devices_path
from .formats import EXTENSIONS, load_workout
from .workout import Workout

log = logging.getLogger(__name__)

FILE_NAME = "bibliotheque.json"
SUBFOLDER = Path("HomeTrainer") / "Séances"


def settings_path() -> Path:
    return devices_path().with_name(FILE_NAME)


def default_folder(documents: Path | str | None = None) -> Path:
    """Dossier proposé la première fois : Documents/HomeTrainer/Séances."""
    return Path(documents or Path.home() / "Documents") / SUBFOLDER


def fold_text(text: str) -> str:
    """Minuscules sans accents, pour une recherche qui ignore « é » / « e »."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@dataclass
class LibraryEntry:
    path: Path
    folder: str  # sous-dossier relatif à la bibliothèque, "" à la racine
    workout: Workout
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.workout.name or self.path.stem

    @property
    def duration_s(self) -> float:
        return self.workout.total_duration_s

    @property
    def format(self) -> str:
        return self.path.suffix.lower().lstrip(".")

    def stress_score(self, ftp: float) -> float | None:
        """TSS approché (cible tenue à la perfection) ; None si aucune brique n'a de cible."""
        total = 0.0
        any_target = False
        for seg in self.workout.timeline(ftp):
            if not seg.duration_s or seg.low_w is None:
                continue
            any_target = True
            # Rampe : moyenne du carré de l'intensité, linéaire entre début et fin.
            a = (seg.low_w + seg.high_w) / 2 / ftp
            b = (seg.end_low_w + seg.end_high_w) / 2 / ftp
            total += seg.duration_s * (a * a + a * b + b * b) / 3
        return total / 36 if any_target else None

    def matches(self, query: str) -> bool:
        """Vrai si chaque mot de la recherche figure dans le nom, le dossier, le fichier ou la description."""
        haystack = fold_text(" ".join((self.name, self.folder, self.path.name, self.workout.description)))
        return all(word in haystack for word in fold_text(query).split())


@dataclass
class ScanResult:
    entries: list[LibraryEntry]
    errors: list[tuple[Path, str]]  # fichiers illisibles (sorties .fit d'activité, fichiers abîmés…)


class Library:
    def __init__(self, folder: Path | str | None = None, path: Path | str | None = None) -> None:
        self.folder = Path(folder) if folder is not None else default_folder()
        self.path = Path(path) if path is not None else None  # fichier de réglage ; None = rien n'est mémorisé
        self._cache: dict[Path, tuple[tuple[int, int], LibraryEntry | str]] = {}

    @classmethod
    def load(cls, path: Path | str | None = None, documents: Path | str | None = None) -> Library:
        """Lit le dossier mémorisé ; réglage absent ou illisible = dossier par défaut."""
        library = cls(default_folder(documents), settings_path() if path is None else path)
        try:
            data = json.loads(library.path.read_text(encoding="utf-8"))
            folder = data.get("folder") if isinstance(data, dict) else None
            if isinstance(folder, str) and folder:
                library.folder = Path(folder)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("réglage de la bibliothèque illisible (%s) : %s", library.path, e)
        return library

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"folder": str(self.folder)}, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("réglage de la bibliothèque non enregistré (%s) : %s", self.path, e)

    def set_folder(self, folder: Path | str) -> None:
        self.folder = Path(folder)
        self._cache.clear()

    def ensure_folder(self) -> bool:
        """Crée le dossier s'il n'existe pas encore ; faux si c'est impossible."""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("dossier de la bibliothèque impossible à créer (%s) : %s", self.folder, e)
            return False
        return True

    def contains(self, path: Path | str) -> bool:
        try:
            Path(path).resolve().relative_to(self.folder.resolve())
        except (OSError, ValueError):
            return False
        return True

    def files(self) -> list[Path]:
        """Fichiers de séance du dossier et de ses sous-dossiers (dossiers cachés ignorés)."""
        found: list[Path] = []
        if not self.folder.is_dir():
            return found
        for root, dirs, names in os.walk(self.folder, onerror=lambda e: log.warning("%s", e)):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            found.extend(Path(root) / n for n in sorted(names)
                         if Path(n).suffix.lower() in EXTENSIONS and not n.startswith("."))
        return found

    def scan(self) -> ScanResult:
        entries: list[LibraryEntry] = []
        errors: list[tuple[Path, str]] = []
        seen: dict[Path, tuple[tuple[int, int], LibraryEntry | str]] = {}
        for path in self.files():
            try:
                stat = path.stat()
            except OSError as e:
                errors.append((path, str(e)))
                continue
            stamp = (stat.st_mtime_ns, stat.st_size)
            cached = self._cache.get(path)
            result = cached[1] if cached is not None and cached[0] == stamp else self._read(path)
            seen[path] = (stamp, result)
            if isinstance(result, str):
                errors.append((path, result))
            else:
                entries.append(result)
        self._cache = seen
        entries.sort(key=lambda e: (fold_text(e.folder), fold_text(e.name)))
        return ScanResult(entries, errors)

    def _read(self, path: Path) -> LibraryEntry | str:
        warnings: list[str] = []
        try:
            workout = load_workout(path, warnings)
        except Exception as e:  # noqa: BLE001 (un fichier abîmé ne doit pas bloquer la bibliothèque)
            return str(e) or type(e).__name__
        if not workout.steps:
            return "aucune brique"
        folder = path.parent.relative_to(self.folder).as_posix()
        return LibraryEntry(path, "" if folder == "." else folder, workout, warnings)
