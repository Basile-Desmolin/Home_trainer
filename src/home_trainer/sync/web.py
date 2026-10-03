"""Requêtes HTTPS (bibliothèque standard) et retour de connexion OAuth sur http://localhost.

`Http` est la fonction qui fait une requête : celle par défaut passe par urllib,
les tests en fournissent une fausse.
"""

from __future__ import annotations

import json
import secrets
import socket
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


def wait_for_code(path: str, state: str, port: int = CALLBACK_PORT, timeout_s: float = 300,
                  ready: Callable[[], None] | None = None) -> str:
    """Attend que le navigateur revienne sur http://localhost:<port>/<path>?code=… et rend le code.

    `ready` est appelée une fois le serveur à l'écoute (c'est là qu'on ouvre le navigateur).
    """
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (API http.server)
            url = urlparse(self.path)
            if url.path.rstrip("/") != "/" + path.strip("/"):
                self.send_error(404)
                return
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if query.get("state") != state:
                result["error"] = "réponse inattendue (state différent), recommencez"
            elif "code" in query:
                result["code"] = query["code"]
            else:
                result["error"] = query.get("error_description") or query.get("error") or "accès refusé"
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
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "code" in result:
        return result["code"]
    raise SyncError(result.get("error") or "pas de réponse du navigateur, connexion abandonnée")
