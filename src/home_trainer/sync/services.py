"""Envoi d'une sortie .fit vers Strava et Nolio (API officielles, OAuth 2).

Chaque utilisateur crée sa propre appli API (gratuite) chez Strava et chez
Nolio, et en saisit l'identifiant et le secret dans l'appli : voir le README.

    service = open_service("strava", book["strava"])
    url = service.authorize_url(service.redirect_uri(), state)   # à ouvrir dans le navigateur
    service.exchange_code(code, service.redirect_uri())         # code reçu sur http://localhost
    result = service.upload(Path("sortie.fit"), "Sweet spot", "…", "2026-10-03_0815_Sweet-spot")
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from .accounts import Account
from .web import CALLBACK_PORT, Http, Response, SyncError, multipart, urllib_http


@dataclass(frozen=True)
class UploadResult:
    url: str | None  # page de l'activité, quand le service la donne
    message: str  # « envoyée », « déjà présente »…


class Service:
    key = ""
    label = ""
    authorize_endpoint = ""
    token_endpoint = ""

    def __init__(self, account: Account, http: Http = urllib_http, sleep=time.sleep) -> None:
        self.account = account
        self.http = http
        self.sleep = sleep

    def default_redirect_uri(self, port: int = CALLBACK_PORT) -> str:
        return f"http://localhost:{port}/{self.key}"

    def redirect_uri(self, port: int = CALLBACK_PORT) -> str:
        """URL de rappel : celle déclarée par l'utilisateur dans son appli API, sinon celle par défaut.
        Elle doit être identique, au caractère près, à celle du portail du service."""
        return self.account.redirect_uri.strip() or self.default_redirect_uri(port)

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        raise NotImplementedError

    def _token_request(self, form: dict) -> dict:
        raise NotImplementedError

    def exchange_code(self, code: str, redirect_uri: str) -> None:
        """Échange le code reçu après accord de l'utilisateur contre les jetons d'accès."""
        self._store(self._token_request({"grant_type": "authorization_code", "code": code,
                                         "redirect_uri": redirect_uri}))

    def access_token(self) -> str:
        """Jeton d'accès valable, renouvelé s'il expire dans la minute."""
        a = self.account
        if not a.connected:
            raise SyncError(f"{self.label} : compte non connecté (bouton Comptes…)")
        if a.access_token and a.expires_at > time.time() + 60:
            return a.access_token
        if not a.refresh_token:
            raise SyncError(f"{self.label} : connexion expirée, reconnectez le compte (Comptes…)")
        self._store(self._token_request({"grant_type": "refresh_token", "refresh_token": a.refresh_token}))
        return a.access_token

    def _store(self, data: dict) -> None:
        a = self.account
        if not data.get("access_token"):
            raise SyncError(f"{self.label} : jeton d'accès absent de la réponse")
        a.access_token = data["access_token"]
        a.refresh_token = data.get("refresh_token") or a.refresh_token
        if "expires_at" in data:
            a.expires_at = float(data["expires_at"])
        else:
            a.expires_at = time.time() + float(data.get("expires_in") or 3600)
        athlete = data.get("athlete")
        if isinstance(athlete, dict):
            a.athlete = " ".join(str(athlete.get(k) or "") for k in ("firstname", "lastname")).strip()

    def _fail(self, what: str, r: Response) -> SyncError:
        data = r.json()
        detail = (data.get("message") or data.get("error_description") or data.get("error")
                  or data.get("detail") or r.text(200))
        if r.status in (401, 403):
            detail = f"accès refusé ({detail}) : reconnectez le compte (Comptes…)"
        return SyncError(f"{self.label} : {what} impossible (HTTP {r.status}) : {detail}")

    def upload(self, path: Path, title: str, description: str, external_id: str) -> UploadResult:
        raise NotImplementedError


class Strava(Service):
    """API Strava v3 : https://developers.strava.com/docs/uploads/"""

    key = "strava"
    label = "Strava"
    authorize_endpoint = "https://www.strava.com/oauth/authorize"
    token_endpoint = "https://www.strava.com/oauth/token"
    api = "https://www.strava.com/api/v3"
    scope = "activity:write"
    poll_s = 2.0
    poll_tries = 30

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        return self.authorize_endpoint + "?" + urlencode({
            "client_id": self.account.client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "approval_prompt": "auto", "scope": self.scope, "state": state})

    def _token_request(self, form: dict) -> dict:
        form = {"client_id": self.account.client_id, "client_secret": self.account.client_secret, **form}
        form.pop("redirect_uri", None)
        r = self.http("POST", self.token_endpoint, {"Content-Type": "application/x-www-form-urlencoded",
                                                    "Accept": "application/json"},
                      urlencode(form).encode())
        if not r.ok:
            raise self._fail("connexion", r)
        return r.json()

    def upload(self, path: Path, title: str, description: str, external_id: str) -> UploadResult:
        token = self.access_token()
        body, content_type = multipart(
            {"data_type": "fit", "name": title, "description": description, "trainer": "1",
             "external_id": external_id},
            {"file": (path.name, path.read_bytes())})
        auth = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        r = self.http("POST", f"{self.api}/uploads", {**auth, "Content-Type": content_type}, body)
        if not r.ok:
            raise self._fail("envoi", r)
        status = r.json()
        # Strava analyse le fichier en différé : on attend que l'activité soit créée.
        for _ in range(self.poll_tries):
            result = self._outcome(status)
            if result is not None:
                return result
            self.sleep(self.poll_s)
            r = self.http("GET", f"{self.api}/uploads/{status.get('id_str') or status.get('id')}", auth, None)
            if not r.ok:
                raise self._fail("suivi de l'envoi", r)
            status = r.json()
        return UploadResult(None, "envoyée, en cours de traitement chez Strava")

    def _outcome(self, status: dict) -> UploadResult | None:
        if status.get("activity_id"):
            return UploadResult(f"https://www.strava.com/activities/{status['activity_id']}", "envoyée")
        error = status.get("error")
        if error:
            duplicate = re.search(r"duplicate of .*?activities/(\d+)", str(error))
            if duplicate or "duplicate" in str(error).lower():
                url = f"https://www.strava.com/activities/{duplicate.group(1)}" if duplicate else None
                return UploadResult(url, "déjà présente sur Strava")
            raise SyncError(f"Strava a refusé le fichier : {re.sub(r'<[^>]+>', '', str(error))}")
        return None


class Nolio(Service):
    """API Nolio : https://github.com/NolioApp/NolioAPI-Documentation/wiki"""

    key = "nolio"
    label = "Nolio"
    authorize_endpoint = "https://www.nolio.io/api/authorize/"
    token_endpoint = "https://www.nolio.io/api/token/"
    upload_endpoint = "https://www.nolio.io/api/upload/file/"

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        return self.authorize_endpoint + "?" + urlencode({
            "response_type": "code", "client_id": self.account.client_id, "redirect_uri": redirect_uri,
            "state": state})

    def _token_request(self, form: dict) -> dict:
        basic = base64.b64encode(f"{self.account.client_id}:{self.account.client_secret}".encode()).decode()
        r = self.http("POST", self.token_endpoint, {"Authorization": f"Basic {basic}",
                                                    "Content-Type": "application/x-www-form-urlencoded",
                                                    "Accept": "application/json"},
                      urlencode(form).encode())
        if not r.ok:
            raise self._fail("connexion", r)
        return r.json()

    def upload(self, path: Path, title: str, description: str, external_id: str) -> UploadResult:
        token = self.access_token()
        payload = {"id_partner": external_id, "format": "fit", "title": title, "comment": description,
                   "data": base64.b64encode(path.read_bytes()).decode("ascii")}
        r = self.http("POST", self.upload_endpoint, {"Authorization": f"Bearer {token}",
                                                     "Content-Type": "application/json",
                                                     "Accept": "application/json"},
                      json.dumps(payload).encode("utf-8"))
        if r.ok:  # 202 : Nolio met le fichier dans sa file de traitement
            return UploadResult("https://www.nolio.io/", "envoyée")
        text = r.text().lower()
        if r.status == 400 and any(word in text for word in ("already", "déjà", "imported")):
            return UploadResult("https://www.nolio.io/", "déjà présente sur Nolio")
        raise self._fail("envoi", r)


SERVICE_CLASSES: dict[str, type[Service]] = {"strava": Strava, "nolio": Nolio}


def open_service(key: str, account: Account, http: Http = urllib_http) -> Service:
    return SERVICE_CLASSES[key](account, http)
