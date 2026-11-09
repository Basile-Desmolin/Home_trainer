"""Profils de cycliste : chacun a sa FTP, son poids, ses comptes Strava / Nolio et ses sorties.

La liste est dans `profils.json` du dossier de configuration (%APPDATA%\\HomeTrainer
sous Windows, ~/.config/home-trainer ailleurs ; variable HOME_TRAINER_PROFILES pour
un autre fichier). Chaque profil a son dossier `profils/<id>/` à côté, avec
`comptes.json`. Ses sorties sont dans Documents/HomeTrainer/Sorties/<id>/ quand
l'appli donne ce dossier (`rides_root`), sinon dans `profils/<id>/sorties/`. Les
appareils et le dossier de la bibliothèque restent communs à tous les profils.

Au premier lancement après la mise à jour, les comptes et sorties d'avant les
profils sont déplacés dans un premier profil (`migrate_legacy`), et les sorties
encore dans `profils/<id>/sorties/` passent dans Documents (`migrate_rides`).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import config_dir
from .sensors.trainer import DEFAULT_BIKE_KG

log = logging.getLogger(__name__)

FILE_NAME = "profils.json"
PROFILES_DIR = "profils"
DEFAULT_FTP = 250
DEFAULT_WEIGHT_KG = 66.0  # poids du cycliste seul, comme la case « Poids »
MIN_BIKE_KG, MAX_BIKE_KG = 5.0, 30.0  # poids du vélo saisi dans la fiche Profil (9 kg par défaut)
SPEED_APP, SPEED_TRAINER = "app", "trainer"  # vitesse calculée par l'application, ou celle du home trainer
FIRST_PROFILE_NAME = "Mon profil"
RIDES_SUBFOLDER = Path("HomeTrainer") / "Sorties"
HISTORY_FILE = "historique.json"  # voir home_trainer.history


def default_rides_root(documents: Path | str | None = None) -> Path:
    """Dossier des sorties de tous les profils : Documents/HomeTrainer/Sorties."""
    return Path(documents or Path.home() / "Documents") / RIDES_SUBFOLDER


def default_path() -> Path:
    override = os.environ.get("HOME_TRAINER_PROFILES")
    return Path(override) if override else config_dir() / FILE_NAME


@dataclass
class Profile:
    id: str
    name: str
    ftp: int = DEFAULT_FTP
    weight_kg: float = DEFAULT_WEIGHT_KG
    bike_kg: float = DEFAULT_BIKE_KG  # poids du vélo : vitesse calculée et consignes de pente
    hr_max: int | None = None  # FC max, demandée à la première séance en fréquence cardiaque
    # Indicateurs choisis par écran (« séance », « libre », « parcours ») : un par place de la grille ;
    # écran absent : disposition d'origine.
    layouts: dict[str, list[str]] = field(default_factory=dict)
    power_smoothing_s: int = 0  # puissance affichée : instantanée (0) ou moyenne sur 3, 5, 10, 30 s
    brick_beep: bool = True  # séance : bip aux 3 dernières secondes de chaque brique
    chart_curves: list[str] | None = None  # courbes en plus de la puissance ; None : cadence et cardio
    speed_source: str = SPEED_APP  # vitesse : calculée par l'application, ou celle du home trainer


def _read(path: Path) -> tuple[dict | None, str | None]:
    """(contenu, erreur) : (None, None) si le fichier n'existe pas."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, str(e)
    if not isinstance(data, dict) or not isinstance(data.get("profiles", []), list):
        return None, "contenu inattendu"
    return data, None


def _backup(path: Path) -> Path:
    """Copie de la version précédente : profils.bak.json."""
    return path.with_name(f"{path.stem}.bak.json")


def _number(value, default, low: float, high: float, kind=float):
    """`value` en nombre (`kind`) s'il est lisible et dans [low, high], sinon `default`."""
    if isinstance(value, bool):
        return default
    try:
        number = kind(float(str(value).replace(",", ".")))
    except (TypeError, ValueError, OverflowError):
        return default
    return number if low <= number <= high else default


def _profile_from(item: dict) -> Profile:
    """Profil lu du fichier : un réglage vide ou invalide (fichier modifié à la main) prend sa
    valeur par défaut, au lieu de faire planter le démarrage."""
    from .heart_zones import MAX_HR_MAX, MIN_HR_MAX

    profile = Profile(str(item["id"]), str(item["name"]))
    bad = []

    def take(key: str, value, default):
        if key in item and value == default and item[key] != default:
            bad.append(key)
        return value

    profile.ftp = take("ftp", _number(item.get("ftp"), DEFAULT_FTP, 1, 2000, int), DEFAULT_FTP)
    profile.weight_kg = take("weight_kg", _number(item.get("weight_kg"), DEFAULT_WEIGHT_KG, 20, 300),
                             DEFAULT_WEIGHT_KG)
    profile.bike_kg = take("bike_kg", _number(item.get("bike_kg"), DEFAULT_BIKE_KG, 1, 50), DEFAULT_BIKE_KG)
    hr_max = item.get("hr_max")
    profile.hr_max = None if hr_max is None else take("hr_max", _number(hr_max, None, MIN_HR_MAX, MAX_HR_MAX, int),
                                                      None)
    layouts = item.get("layouts", {})
    if isinstance(layouts, dict):
        profile.layouts = {k: [str(x) for x in v] for k, v in layouts.items()
                           if isinstance(k, str) and isinstance(v, list)}
    else:
        bad.append("layouts")
    profile.power_smoothing_s = take("power_smoothing_s", _number(item.get("power_smoothing_s"), 0, 0, 600, int), 0)
    beep = item.get("brick_beep", True)
    profile.brick_beep = beep if isinstance(beep, bool) else True
    curves = item.get("chart_curves")
    profile.chart_curves = [str(c) for c in curves] if isinstance(curves, list) else None
    source = item.get("speed_source", SPEED_APP)
    profile.speed_source = source if source in (SPEED_APP, SPEED_TRAINER) else SPEED_APP
    if bad:
        log.warning("profil %s : réglages invalides remplacés par leur valeur par défaut : %s",
                    profile.id, ", ".join(bad))
    return profile


def _slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "profil"


class ProfileBook:
    def __init__(self, path: Path | str | None = None, rides_root: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        # Dossier des sorties (un sous-dossier par profil) ; None : dans le dossier du profil.
        self.rides_root = Path(rides_root) if rides_root is not None else None
        self.profiles: list[Profile] = []
        self.last: str | None = None  # dernier profil choisi
        self.remember = False  # au lancement, reprendre le dernier profil sans demander
        self.problem: str | None = None  # fichier illisible à la lecture : message pour l'utilisateur
        self.set_aside: Path | None = None  # où le fichier illisible a été gardé (à la première écriture)
        self._damaged = False  # fichier principal illisible : à garder de côté avant d'écrire

    # --- fichiers du profil ----------------------------------------------

    @property
    def base_dir(self) -> Path:
        return (self.path.parent if self.path is not None else config_dir()) / PROFILES_DIR

    def folder(self, profile: Profile) -> Path:
        return self.base_dir / profile.id

    def accounts_path(self, profile: Profile) -> Path:
        return self.folder(profile) / "comptes.json"

    def legacy_rides_dir(self, profile: Profile) -> Path:
        """Ancien dossier des sorties, dans le dossier de configuration (AppData)."""
        return self.folder(profile) / "sorties"

    def rides_dir(self, profile: Profile) -> Path:
        old = self.legacy_rides_dir(profile)
        if self.rides_root is None:
            return old
        new = self.rides_root / profile.id
        # Tant que les sorties n'ont pas pu être déplacées, on reste dans l'ancien dossier.
        return old if old.is_dir() and not new.exists() else new

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
        while ident in taken or (self.path is not None and self.base_dir.joinpath(ident).exists()) \
                or (self.rides_root is not None and self.rides_root.joinpath(ident).exists()):
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
    def load(cls, path: Path | str | None = None, rides_root: Path | str | None = None) -> ProfileBook:
        """Lit les profils. Un fichier illisible est relu depuis sa copie (`profils.bak.json`) et
        gardé de côté à la première écriture, au lieu d'être écrasé ; `problem` le dit."""
        book = cls(default_path() if path is None else path, rides_root)
        data, error = _read(book.path)
        if error is not None:
            log.warning("profils illisibles (%s) : %s", book.path, error)
            book._damaged = True
            data, _ = _read(_backup(book.path))
            book.problem = (f"Profils illisibles ({error}) : relus depuis la copie de secours." if data is not None
                            else f"Profils illisibles ({error}), sans copie de secours lisible.")
        if data is None:
            return book
        for item in data.get("profiles", []):
            if isinstance(item, dict) and item.get("id") and item.get("name"):
                book.profiles.append(_profile_from(item))
        last = data.get("last")
        book.last = last if isinstance(last, str) else None
        book.remember = bool(data.get("remember", False))
        return book

    def save(self) -> None:
        if self.path is None:
            return
        data = {"profiles": [asdict(p) for p in self.profiles], "last": self.last, "remember": self.remember}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._damaged and self.path.exists():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                aside = self.path.with_name(f"{self.path.stem}.illisible-{stamp}.json")
                self.path.replace(aside)
                self.set_aside = aside
            elif self.path.exists():
                shutil.copyfile(self.path, _backup(self.path))
            self._damaged = False
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
        if accounts.exists():
            try:
                shutil.move(str(accounts), str(self.accounts_path(profile)))
            except OSError as e:
                log.warning("%s non déplacé vers le profil : %s", accounts, e)
        if rides.exists():
            move_rides(rides, self.legacy_rides_dir(profile))
        self.last = profile.id
        self.save()
        return profile

    def migrate_rides(self) -> None:
        """Déplace les sorties restées dans le dossier de configuration (AppData) vers
        Documents, profil par profil, en gardant leur historique. Ce qui n'a pas pu
        être déplacé reste où il était (et y est toujours lu)."""
        if self.rides_root is None:
            return
        for profile in self.profiles:
            old, new = self.legacy_rides_dir(profile), self.rides_root / profile.id
            if old.is_dir() and not new.exists():
                move_rides(old, new)


def move_rides(old: Path, new: Path) -> bool:
    """Déplace un dossier de sorties (sans rien perdre s'il échoue en route) et met à jour
    les chemins des fichiers dans son historique. False s'il est resté où il était."""
    tmp = new.with_name(new.name + ".deplacement")
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            old.rename(new)  # même disque : immédiat
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(old, tmp)  # autre disque (OneDrive…) : copie, puis on efface l'ancien
            tmp.rename(new)
            shutil.rmtree(old, ignore_errors=True)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        log.warning("sorties non déplacées de %s vers %s : %s", old, new, e)
        return False
    _relocate_history(new / HISTORY_FILE, old, new)
    return True


def _relocate_history(path: Path, old: Path, new: Path) -> None:
    """Dans l'historique, les fichiers qui étaient dans `old` sont maintenant dans `new`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        log.warning("historique illisible (%s) : %s", path, e)
        return
    changed = False
    for ride in data.get("rides", []) if isinstance(data, dict) else []:
        file = ride.get("file") if isinstance(ride, dict) else None
        if not file:
            continue
        try:
            relative = Path(file).relative_to(old.absolute())
        except ValueError:
            continue  # enregistrée ailleurs (« Enregistrer sous »), elle n'a pas bougé
        ride["file"] = str(new.absolute() / relative)
        changed = True
    if changed:
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            log.warning("historique non mis à jour (%s) : %s", path, e)
