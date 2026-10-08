"""Profils de cycliste : chacun a sa FTP, son poids, ses comptes Strava / Nolio et ses sorties.

La liste est dans `profils.json` du dossier de configuration (%APPDATA%\\HomeTrainer
sous Windows, ~/.config/home-trainer ailleurs ; variable HOME_TRAINER_PROFILES pour
un autre fichier). Chaque profil a son dossier `profils/<id>/` à côté, avec
`comptes.json` et `sorties/`. Les appareils et le dossier de la bibliothèque
restent communs à tous les profils.

Au premier lancement après la mise à jour, les comptes et sorties d'avant les
profils sont déplacés dans un premier profil (`migrate_legacy`).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .config import config_dir

log = logging.getLogger(__name__)

FILE_NAME = "profils.json"
PROFILES_DIR = "profils"
DEFAULT_FTP = 250
DEFAULT_WEIGHT_KG = 66.0  # poids du cycliste seul, comme la case « Poids »
FIRST_PROFILE_NAME = "Mon profil"


def default_path() -> Path:
    override = os.environ.get("HOME_TRAINER_PROFILES")
    return Path(override) if override else config_dir() / FILE_NAME


@dataclass
class Profile:
    id: str
    name: str
    ftp: int = DEFAULT_FTP
    weight_kg: float = DEFAULT_WEIGHT_KG
    hr_max: int | None = None  # FC max, demandée à la première séance en fréquence cardiaque


def _slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "profil"


class ProfileBook:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.profiles: list[Profile] = []
        self.last: str | None = None  # dernier profil choisi
        self.remember = False  # au lancement, reprendre le dernier profil sans demander

    # --- fichiers du profil ----------------------------------------------

    @property
    def base_dir(self) -> Path:
        return (self.path.parent if self.path is not None else config_dir()) / PROFILES_DIR

    def folder(self, profile: Profile) -> Path:
        return self.base_dir / profile.id

    def accounts_path(self, profile: Profile) -> Path:
        return self.folder(profile) / "comptes.json"

    def rides_dir(self, profile: Profile) -> Path:
        return self.folder(profile) / "sorties"

    # --- liste -------------------------------------------------------------

    def get(self, profile_id: str | None) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def find(self, name: str) -> Profile | None:
        """Profil par son nom (sans tenir compte des majuscules) ou son identifiant."""
        key = name.strip().casefold()
        return next((p for p in self.profiles if p.name.casefold() == key or p.id == key), None)

    def create(self, name: str, ftp: int = DEFAULT_FTP, weight_kg: float = DEFAULT_WEIGHT_KG) -> Profile:
        name = name.strip() or FIRST_PROFILE_NAME
        profile = Profile(self.new_id(name), name, int(ftp), float(weight_kg))
        self.profiles.append(profile)
        return profile

    def new_id(self, name: str) -> str:
        """Identifiant (et nom du dossier) libre pour un nouveau profil, tiré de son nom."""
        base = _slug(name)
        taken = {p.id for p in self.profiles}
        ident, n = base, 2
        # Un dossier resté d'un profil supprimé n'est pas repris : ses sorties ne sont pas à nous.
        while ident in taken or (self.path is not None and self.base_dir.joinpath(ident).exists()):
            ident, n = f"{base}-{n}", n + 1
        return ident

    def remove(self, profile: Profile) -> None:
        """Retire le profil de la liste ; son dossier (sorties, comptes) reste sur le disque."""
        self.profiles = [p for p in self.profiles if p.id != profile.id]
        if self.last == profile.id:
            self.last = None

    def startup_profile(self) -> Profile | None:
        """Le profil à prendre sans rien demander (« se souvenir de mon choix »), sinon None."""
        return self.get(self.last) if self.remember else None

    # --- fichier -----------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> ProfileBook:
        book = cls(default_path() if path is None else path)
        try:
            data = json.loads(book.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return book
        except (OSError, ValueError) as e:
            log.warning("profils illisibles (%s) : %s", book.path, e)
            return book
        if not isinstance(data, dict):
            return book
        known = {f.name for f in fields(Profile)}
        for item in data.get("profiles", []):
            if isinstance(item, dict) and item.get("id") and item.get("name"):
                book.profiles.append(Profile(**{k: v for k, v in item.items() if k in known}))
        book.last = data.get("last")
        book.remember = bool(data.get("remember", False))
        return book

    def save(self) -> None:
        if self.path is None:
            return
        data = {"profiles": [asdict(p) for p in self.profiles], "last": self.last, "remember": self.remember}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("profils non enregistrés (%s) : %s", self.path, e)

    # --- reprise des réglages d'avant les profils --------------------------

    def migrate_legacy(self) -> Profile | None:
        """S'il n'y a encore aucun profil mais des comptes ou des sorties d'avant, les range
        dans un premier profil ; renvoie ce profil (déjà enregistré), sinon None."""
        if self.profiles or self.path is None:
            return None
        root = self.path.parent
        accounts, rides = root / "comptes.json", root / "sorties"
        if not accounts.exists() and not rides.exists():
            return None
        profile = self.create(FIRST_PROFILE_NAME)
        folder = self.folder(profile)
        folder.mkdir(parents=True, exist_ok=True)
        for source, target in ((accounts, self.accounts_path(profile)), (rides, self.rides_dir(profile))):
            if source.exists():
                try:
                    shutil.move(str(source), str(target))
                except OSError as e:
                    log.warning("%s non déplacé vers le profil : %s", source, e)
        self.last = profile.id
        self.save()
        return profile
