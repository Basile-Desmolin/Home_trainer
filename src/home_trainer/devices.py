"""Appareils mémorisés : home trainers et ceintures cardio déjà connectés, avec un nom choisi.

    book = DeviceBook.load()                       # fichier de l'utilisateur
    device = book.remember("trainer", "ble", "AA:BB:…", "KICKR CORE 5D21")
    book.rename(device, "Kickr du salon")
    book.last("trainer")                           # ("ble", "AA:BB:…") : reconnexion au démarrage
    book.save()

Un appareil est repéré par son adresse Bluetooth ou son numéro ANT+. Le fichier
(JSON) est dans le dossier de configuration de l'utilisateur : %APPDATA%\\HomeTrainer
sous Windows, ~/.config/home-trainer ailleurs ; la variable d'environnement
HOME_TRAINER_DEVICES permet d'en choisir un autre.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import config_dir

log = logging.getLogger(__name__)

ROLES = {"trainer": "home trainer", "hr": "ceinture cardio"}
KINDS = {"ble": "Bluetooth", "ant": "ANT+"}
FILE_NAME = "appareils.json"


def default_path() -> Path:
    override = os.environ.get("HOME_TRAINER_DEVICES")
    if override:
        return Path(override)
    return config_dir() / FILE_NAME


@dataclass
class SavedDevice:
    role: str   # "trainer" ou "hr"
    kind: str   # "ble" ou "ant"
    ident: str  # adresse Bluetooth, ou numéro ANT+ en texte
    name: str   # nom choisi par l'utilisateur

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.kind, self.kind)

    def connect_args(self) -> dict:
        """Arguments de `open_trainer` / `open_heart_rate_sensor` pour s'y reconnecter."""
        if self.kind == "ant":
            return {"address": None, "device_number": int(self.ident)}
        return {"address": self.ident, "device_number": 0}

    def __str__(self) -> str:
        return f"{self.name}  ({self.kind_label})"


def _same_ident(kind: str, a: str, b: str) -> bool:
    # Les adresses Bluetooth s'écrivent selon le système en majuscules ou minuscules.
    return a.lower() == b.lower() if kind == "ble" else a == b


class DeviceBook:
    """Carnet des appareils déjà connectés, le plus récent en premier, et du dernier choix par rôle."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._devices: list[SavedDevice] = []
        self._last: dict[str, dict] = {}

    @classmethod
    def load(cls, path: Path | str | None = None) -> DeviceBook:
        """Lit le carnet ; fichier absent ou illisible = carnet vide (jamais d'erreur au démarrage)."""
        book = cls(default_path() if path is None else path)
        try:
            data = json.loads(book.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return book
        except (OSError, ValueError) as e:
            log.warning("appareils mémorisés illisibles (%s) : %s", book.path, e)
            return book
        for item in data.get("devices", []):
            try:
                device = SavedDevice(str(item["role"]), str(item["kind"]), str(item["ident"]), str(item["name"]))
            except (KeyError, TypeError):
                continue
            if device.role in ROLES and device.kind in KINDS:
                book._devices.append(device)
        last = data.get("last", {})
        if isinstance(last, dict):
            book._last = {role: choice for role, choice in last.items()
                          if role in ROLES and isinstance(choice, dict)}
        return book

    def save(self) -> None:
        if self.path is None:
            return
        data = {"devices": [asdict(d) for d in self._devices], "last": self._last}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:  # dossier protégé, disque plein : l'appli continue sans mémoriser
            log.warning("appareils non enregistrés (%s) : %s", self.path, e)

    # --- appareils ---------------------------------------------------------

    def devices(self, role: str) -> list[SavedDevice]:
        return [d for d in self._devices if d.role == role]

    def find(self, role: str, kind: str, ident: str | None) -> SavedDevice | None:
        if ident is None:
            return None
        return next((d for d in self._devices
                     if d.role == role and d.kind == kind and _same_ident(kind, d.ident, ident)), None)

    def remember(self, role: str, kind: str, ident: str, name: str) -> SavedDevice:
        """Mémorise un appareil connecté (sans écraser le nom déjà choisi) et en fait le dernier utilisé."""
        device = self.find(role, kind, ident)
        if device is None:
            device = SavedDevice(role, kind, ident, name.strip() or ident)
        else:
            self._devices.remove(device)
        self._devices.insert(0, device)
        self.set_last(role, kind, device.ident)
        return device

    def rename(self, device: SavedDevice, name: str) -> None:
        device.name = name.strip() or device.ident

    def forget(self, device: SavedDevice) -> None:
        self._devices = [d for d in self._devices if d is not device]
        last = self._last.get(device.role)
        if last and last.get("kind") == device.kind and _same_ident(device.kind, str(last.get("ident")),
                                                                    device.ident):
            self._last[device.role] = {"kind": device.kind, "ident": None}

    def display_name(self, role: str, kind: str, ident: str | None) -> str | None:
        device = self.find(role, kind, ident)
        return device.name if device else None

    # --- dernier choix -----------------------------------------------------

    def last(self, role: str) -> tuple[str | None, str | None] | None:
        """Dernier choix pour ce rôle : (type, identifiant) ; type None = aucun. None = jamais choisi."""
        choice = self._last.get(role)
        if choice is None:
            return None
        kind, ident = choice.get("kind"), choice.get("ident")
        return kind, (None if ident is None else str(ident))

    def set_last(self, role: str, kind: str | None, ident: str | None = None) -> None:
        self._last[role] = {"kind": kind, "ident": ident}

    def startup_choice(self, role: str, allowed: tuple[str | None, ...],
                       default: str | None = "sim") -> tuple[str | None, str | None, int]:
        """Appareil à rebrancher au lancement : (type, adresse Bluetooth, numéro ANT+)."""
        last = self.last(role)
        if last is None or last[0] not in allowed:
            return default, None, 0
        kind, ident = last
        if kind == "ant":
            return kind, None, int(ident) if ident and ident.isdigit() else 0
        return kind, ident if kind == "ble" else None, 0


def ant_ident(device_number: int) -> str | None:
    """Identifiant mémorisé d'un numéro ANT+ (0 = « le premier trouvé », rien à mémoriser)."""
    return str(device_number) if device_number else None
