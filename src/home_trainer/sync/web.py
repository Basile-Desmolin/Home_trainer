"""Requêtes HTTPS (bibliothèque standard) et retour de connexion OAuth sur http://localhost.

`Http` est la fonction qui fait une requête : celle par défaut passe par urllib,
les tests en fournissent une fausse.
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

CALLBACK_PORT = 8765
TIMEOUT_S = 30


class SyncError(Exception):
    """Échec d'un envoi ou d'une connexion, avec un message à montrer tel quel."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self) -> dict:
        try:
            data = json.loads(self.body.decode("utf-8") or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {"data": data}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self, limit: int = 300) -> str:
        return self.body.decode("utf-8", errors="replace")[:limit]


Http = Callable[[str, str, dict, "bytes | None"], Response]


def urllib_http(method: str, url: str, headers: dict, body: bytes | None) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as r:
            return Response(r.status, r.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read())
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise SyncError(f"pas de connexion à {urlparse(url).hostname} ({getattr(e, 'reason', e)})") from e


def multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    """Corps multipart/form-data et son Content-Type."""
    boundary = "----home-trainer-" + secrets.token_hex(12)
    out = bytearray()
    for name, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n").encode("utf-8")
    for name, (filename, data) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


PAGE = """<!doctype html><meta charset="utf-8"><title>Home trainer</title>
<body style="font-family:sans-serif;text-align:center;margin-top:4em">
<h2>{title}</h2><p>{text}</p></body>"""


def local_callback(redirect_uri: str) -> tuple[int, str] | None:
    """(port, chemin) si l'URL de rappel est http://localhost:<port>/… (réponse reçue par l'appli elle-même),
    None sinon (https, autre machine : l'adresse de retour est à recopier à la main)."""
    url = urlparse(redirect_uri.strip())
    if url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1"):
        return None
    try:
        port = url.port or 80
    except ValueError:
        return None
    return port, url.path or "/"


def parse_callback(query: dict[str, str] | str, state: str) -> str:
    """Code d'autorisation de l'adresse de retour (ou de sa partie « ?… ») ; SyncError si refus.

    L'adresse peut être collée telle qu'elle apparaît dans le navigateur, même si la page
    elle-même n'a pas pu s'afficher.
    """
    if isinstance(query, str):
        text = query.strip()
        raw = urlparse(text).query if "://" in text else text.lstrip("?")
        query = {k: v[0] for k, v in parse_qs(raw).items()}
    if "code" not in query:
        reason = query.get("error_description") or query.get("error")
        raise SyncError(f"accès refusé ({reason})" if reason else
                        "pas de code dans cette adresse : copiez toute l'adresse de la page après avoir accepté")
    if query.get("state") not in (None, state):  # pas de state renvoyé : on fait confiance au code
        raise SyncError("réponse inattendue (state différent), recommencez")
    return query["code"]


def wait_for_code(path: str, state: str, port: int = CALLBACK_PORT, timeout_s: float = 300,
                  ready: Callable[[], None] | None = None, cancel: threading.Event | None = None) -> str:
    """Attend que le navigateur revienne sur http://localhost:<port>/<path>?code=… et rend le code.

    `ready` est appelée une fois le serveur à l'écoute (c'est là qu'on ouvre le navigateur) ;
    `cancel` permet d'abandonner l'attente (bouton Annuler).
    """
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (API http.server)
            url = urlparse(self.path)
            if url.path.rstrip("/") != "/" + path.strip("/"):
                self.send_error(404)
                return
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                result["code"] = parse_callback(query, state)
            except SyncError as e:
                result["error"] = str(e)
            ok = "code" in result
            page = PAGE.format(title="Connexion réussie" if ok else "Connexion refusée",
                               text="Vous pouvez fermer cette page et revenir à Home trainer."
                               if ok else result.get("error", ""))
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:  # pas de journal dans la console
            pass

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        raise SyncError(f"le port {port} est déjà pris ({e}) : fermez l'autre fenêtre de connexion") from e
    server.timeout = 1.0
    try:
        if ready is not None:
            ready()
        deadline = time.monotonic() + timeout_s
        while not result and time.monotonic() < deadline and not (cancel and cancel.is_set()):
            server.handle_request()
    finally:
        server.server_close()
    if "code" in result:
        return result["code"]
    if cancel is not None and cancel.is_set():
        raise SyncError("connexion annulée")
    raise SyncError(result.get("error") or "pas de réponse du navigateur, connexion abandonnée")
