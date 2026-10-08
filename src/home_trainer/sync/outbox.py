"""Sorties enregistrées et leur envoi : chaque sortie terminée devient un .fit dans le
dossier des sorties, puis part vers les comptes connectés ; un envoi raté (pas de
réseau, appli fermée trop tôt) est retenté au lancement suivant.

Le dossier est `sorties` dans le dossier de configuration (variable
HOME_TRAINER_RIDES pour un autre) ; `envois.json` y note, pour chaque fichier,
où il est parti.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import config_dir
from ..formats.fit_activity import ActivityPoint, activity_file_name, write_activity
from .accounts import SERVICES, AccountBook
from .services import Service, open_service
from .web import CALLBACK_PORT, Http, SyncError, local_callback, parse_callback, urllib_http, wait_for_code

log = logging.getLogger(__name__)

STATUS_FILE = "envois.json"


def default_dir() -> Path:
    override = os.environ.get("HOME_TRAINER_RIDES")
    return Path(override) if override else config_dir() / "sorties"


@dataclass(frozen=True)
class SendReport:
    file: str
    service: str
    ok: bool
    message: str
    url: str | None = None

    def __str__(self) -> str:
        label = {"strava": "Strava", "nolio": "Nolio"}.get(self.service, self.service)
        return f"{label} : {self.message}" if self.ok else self.message


class Outbox:
    def __init__(self, directory: Path | str | None = None) -> None:
        self.dir = Path(directory) if directory is not None else default_dir()
        self._lock = threading.Lock()

    # --- état des envois -------------------------------------------------

    def _read(self) -> dict:
        try:
            data = json.loads((self.dir / STATUS_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("état des envois illisible : %s", e)
            return {}

    def _write(self, data: dict) -> None:
        path = self.dir / STATUS_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def entries(self) -> dict:
        with self._lock:
            return self._read()

    # --- sorties ---------------------------------------------------------

    def add(self, points: list[ActivityPoint], title: str, description: str = "",
            dest: Path | str | None = None) -> Path:
        """Enregistre la sortie en .fit et la met en attente d'envoi.

        Sans `dest`, le fichier prend un nom libre dans le dossier des sorties ; avec, il
        est écrit là (remplacé s'il existe) et envoyé depuis là."""
        start = min(p.at for p in points)
        if dest is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / activity_file_name(start, title)
            n = 2
            while path.exists():
                path = path.with_name(f"{path.stem.rsplit('~', 1)[0]}~{n}.fit")
                n += 1
        else:
            path = Path(dest).absolute()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.dir.mkdir(parents=True, exist_ok=True)  # pour envois.json
        write_activity(points, path)
        # Hors du dossier des sorties, la clé est le chemin complet (dossier / chemin absolu = ce chemin).
        key = path.name if path.parent == self.dir.absolute() else str(path)
        with self._lock:
            data = self._read()
            # Nolio attend un identifiant unique par sortie : l'heure de départ convient.
            data[key] = {"title": title, "description": description, "external_id": str(int(start)),
                         "sent": {}}
            self._write(data)
        return path

    def pending(self, services: list[str]) -> list[tuple[str, str]]:
        """(fichier, service) pas encore envoyés, des plus anciens aux plus récents."""
        out = []
        for name, entry in sorted(self.entries().items()):
            sent = entry.get("sent", {})
            if not (self.dir / name).exists():
                continue
            out += [(name, s) for s in services if not sent.get(s, {}).get("ok")]
        return out

    def mark(self, report: SendReport) -> None:
        with self._lock:
            data = self._read()
            entry = data.setdefault(report.file, {"title": Path(report.file).stem, "sent": {}})
            entry.setdefault("sent", {})[report.service] = {"ok": report.ok, "message": report.message,
                                                            "url": report.url}
            self._write(data)

    def send(self, name: str, service: Service) -> SendReport:
        entry = self.entries().get(name, {})
        try:
            result = service.upload(self.dir / name, entry.get("title") or Path(name).stem,
                                    entry.get("description", ""), entry.get("external_id") or Path(name).stem)
            report = SendReport(name, service.key, True, result.message, result.url)
        except SyncError as e:
            report = SendReport(name, service.key, False, str(e))
        except OSError as e:
            report = SendReport(name, service.key, False, f"{service.label} : fichier illisible ({e})")
        self.mark(report)
        return report


def auto_services(book: AccountBook) -> list[str]:
    return [s for s in SERVICES if book[s].connected and book[s].auto]


def send_pending(outbox: Outbox, book: AccountBook, http: Http = urllib_http,
                 only: str | None = None) -> list[SendReport]:
    """Envoie tout ce qui attend vers les comptes connectés en envoi automatique.

    Les jetons renouvelés sont enregistrés. Un compte qui échoue (réseau, accès
    refusé) n'est pas retenté pour les fichiers suivants de ce passage.
    """
    services = {s: open_service(s, book[s], http) for s in auto_services(book)}
    reports, failed = [], set()
    for name, key in outbox.pending(list(services)):
        if key in failed or (only is not None and name != only):
            continue
        report = outbox.send(name, services[key])
        reports.append(report)
        if not report.ok:
            failed.add(key)
    if services:
        book.save()
    return reports


@dataclass(frozen=True)
class Authorization:
    """Connexion OAuth en cours : page d'accord à ouvrir, et où la réponse reviendra."""

    url: str
    state: str
    redirect_uri: str

    @property
    def local(self) -> tuple[int, str] | None:
        """(port, chemin) quand la réponse revient sur http://localhost ; None : adresse à recopier."""
        return local_callback(self.redirect_uri)


def start_connect(service: Service, port: int = CALLBACK_PORT) -> Authorization:
    if not service.account.configured:
        raise SyncError(f"{service.label} : saisissez d'abord l'identifiant et le secret de votre appli API")
    state = secrets.token_urlsafe(16)
    redirect = service.redirect_uri(port)
    return Authorization(service.authorize_url(redirect, state), state, redirect)


def finish_connect(service: Service, book: AccountBook, auth: Authorization, code: str) -> None:
    """Échange le code contre les jetons et les garde."""
    try:
        service.exchange_code(code, auth.redirect_uri)
    except SyncError as e:
        raise SyncError(f"{e} (URL de rappel envoyée : {auth.redirect_uri}, elle doit être exactement "
                        f"celle déclarée chez {service.label})") from e
    book.save()


def connect(service: Service, book: AccountBook, open_browser: Callable[[str], object],
            port: int = CALLBACK_PORT, timeout_s: float = 300, cancel: threading.Event | None = None,
            paste: Callable[[], str | None] | None = None) -> None:
    """Connexion OAuth : ouvre la page d'accord du service, attend la réponse et garde les jetons.

    Avec une URL de rappel http://localhost, la réponse arrive d'elle-même ; sinon (https…),
    `paste` rend l'adresse de la page affichée après l'accord, recopiée par l'utilisateur.
    """
    auth = start_connect(service, port)
    local = auth.local
    if local is None:
        if paste is None:
            raise SyncError(f"{service.label} : URL de rappel {auth.redirect_uri} non prise en charge")
        open_browser(auth.url)
        pasted = paste()
        if not pasted:
            raise SyncError("connexion annulée")
        code = parse_callback(pasted, auth.state)
    else:
        code = wait_for_code(local[1], auth.state, local[0], timeout_s, ready=lambda: open_browser(auth.url),
                             cancel=cancel)
    finish_connect(service, book, auth, code)
