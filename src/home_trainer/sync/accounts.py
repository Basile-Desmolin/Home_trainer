"""Comptes Strava et Nolio : identifiants de l'appli API créée par l'utilisateur, et jetons d'accès.

Tout reste sur l'ordinateur de l'utilisateur, dans `comptes.json` du dossier de
configuration (%APPDATA%\\HomeTrainer sous Windows, ~/.config/home-trainer
ailleurs ; variable HOME_TRAINER_ACCOUNTS pour un autre fichier). Rien n'est
écrit dans le dépôt.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..config import config_dir

log = logging.getLogger(__name__)

FILE_NAME = "comptes.json"
SERVICES = ("strava", "nolio")


def default_path() -> Path:
    override = os.environ.get("HOME_TRAINER_ACCOUNTS")
    return Path(override) if override else config_dir() / FILE_NAME


@dataclass
class Account:
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0  # heure d'expiration du jeton d'accès (secondes depuis 1970)
    athlete: str = ""  # nom du compte connecté, pour l'affichage
    auto: bool = True  # envoyer chaque sortie dès qu'elle est terminée

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def connected(self) -> bool:
        return self.configured and bool(self.refresh_token or self.access_token)

    def disconnect(self) -> None:
        self.access_token = self.refresh_token = self.athlete = ""
        self.expires_at = 0.0


class AccountBook:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.accounts: dict[str, Account] = {name: Account() for name in SERVICES}

    def __getitem__(self, service: str) -> Account:
        return self.accounts[service]

    @classmethod
    def load(cls, path: Path | str | None = None) -> AccountBook:
        book = cls(default_path() if path is None else path)
        try:
            data = json.loads(book.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return book
        except (OSError, ValueError) as e:
            log.warning("comptes illisibles (%s) : %s", book.path, e)
            return book
        known = {f.name for f in fields(Account)}
        for name in SERVICES:
            item = data.get(name)
            if isinstance(item, dict):
                book.accounts[name] = Account(**{k: v for k, v in item.items() if k in known})
        return book

    def save(self) -> None:
        if self.path is None:
            return
        data = {name: asdict(account) for name, account in self.accounts.items()}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)  # jetons d'accès : lisibles par l'utilisateur seul
            except OSError:
                pass
            tmp.replace(self.path)
        except OSError as e:
            log.warning("comptes non enregistrés (%s) : %s", self.path, e)
