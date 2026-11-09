"""Comptes Strava et Nolio : identifiants de l'appli API créée par l'utilisateur, et jetons d'accès.

Tout reste sur l'ordinateur de l'utilisateur, dans `comptes.json` du dossier de
configuration (%APPDATA%\\HomeTrainer sous Windows, ~/.config/home-trainer
ailleurs ; variable HOME_TRAINER_ACCOUNTS pour un autre fichier). Rien n'est
écrit dans le dépôt. Le secret de l'appli API et les jetons y sont chiffrés par
Windows (voir `secret.py`).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from ..config import config_dir
from . import secret
from .secret import SecretError

log = logging.getLogger(__name__)

FILE_NAME = "comptes.json"
SERVICES = ("strava", "nolio")
SECRET_FIELDS = ("client_secret", "access_token", "refresh_token")  # chiffrés dans le fichier (DPAPI)


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
    redirect_uri: str = ""  # URL de rappel déclarée dans l'appli API ("" : http://localhost:8765/<service>)
    # Heure de la connexion (secondes depuis 1970) : les sorties commencées avant ne partent pas d'elles-
    # mêmes (audit, « Arriéré envoyé d'un coup »), seulement par « Envoyer les sorties en attente ».
    # 0 : compte connecté avant l'étape 8, tout part comme avant.
    since: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def connected(self) -> bool:
        return self.configured and bool(self.refresh_token or self.access_token)

    def disconnect(self) -> None:
        self.access_token = self.refresh_token = self.athlete = ""
        self.expires_at = 0.0
        self.since = 0.0


class AccountBook:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.accounts: dict[str, Account] = {name: Account() for name in SERVICES}
        self._damaged = False  # fichier illisible : gardé de côté avant la première écriture

    def __getitem__(self, service: str) -> Account:
        return self.accounts[service]

    @classmethod
    def load(cls, path: Path | str | None = None) -> AccountBook:
        """Lit les comptes ; les secrets chiffrés (`secret.py`) sont déchiffrés. Un ancien fichier en
        clair est réécrit chiffré aussitôt. Un fichier illisible est gardé de côté à la première
        écriture au lieu d'être écrasé (identifiants de l'appli API compris)."""
        book = cls(default_path() if path is None else path)
        try:
            data = json.loads(book.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("contenu inattendu")
        except FileNotFoundError:
            return book
        except (OSError, ValueError) as e:
            log.warning("comptes illisibles (%s) : %s", book.path, e)
            book._damaged = True
            return book
        known = {f.name for f in fields(Account)}
        plain = False
        for name in SERVICES:
            item = data.get(name)
            if not isinstance(item, dict):
                continue
            values = {k: v for k, v in item.items() if k in known}
            for key in SECRET_FIELDS:
                value = values.get(key)
                if not isinstance(value, str) or not value:
                    continue
                plain = plain or not secret.is_protected(value)
                try:
                    values[key] = secret.unprotect(value)
                except SecretError as e:  # autre PC ou autre session Windows : il faut se reconnecter
                    log.warning("%s : %s illisible (%s)", name, key, e)
                    values[key] = ""
            book.accounts[name] = Account(**values)
        if plain and secret.available():
            book.save()  # fichier d'avant l'étape 8 : les secrets ne restent pas en clair
        return book

    def save(self) -> None:
        if self.path is None:
            return
        data = {name: asdict(account) for name, account in self.accounts.items()}
        for item in data.values():
            for key in SECRET_FIELDS:
                try:
                    item[key] = secret.protect(item[key])
                except SecretError as e:  # chiffrement refusé : mieux vaut garder la connexion
                    log.warning("%s non chiffré : %s", key, e)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._damaged and self.path.exists():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                self.path.replace(self.path.with_name(f"{self.path.stem}.illisible-{stamp}.json"))
            self._damaged = False
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)  # jetons d'accès : lisibles par l'utilisateur seul
            except OSError:
                pass
            tmp.replace(self.path)
        except OSError as e:
            log.warning("comptes non enregistrés (%s) : %s", self.path, e)
