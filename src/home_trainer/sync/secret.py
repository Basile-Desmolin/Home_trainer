"""Secrets de `comptes.json` (secret de l'appli API, jetons d'accès) chiffrés par Windows (DPAPI).

Audit, « Jetons stockés en clair » : ils étaient lisibles par quiconque copiait le fichier.
DPAPI chiffre avec la session Windows de l'utilisateur : le fichier copié sur un autre PC ou lu
par un autre compte ne se déchiffre pas (il faut alors se reconnecter). Rien à installer : l'API
de Windows est appelée par `ctypes`. Ailleurs que sous Windows, les valeurs restent en clair.

Une valeur chiffrée s'écrit « dpapi:<base64> » ; une valeur sans ce préfixe (fichier d'avant
l'étape 8) est lue telle quelle, puis réécrite chiffrée.
"""

from __future__ import annotations

import base64
import ctypes
import sys

PREFIX = "dpapi:"
_ENTROPY = b"HomeTrainer comptes"  # propre à l'application, en plus de la session Windows
_UI_FORBIDDEN = 0x01  # CRYPTPROTECT_UI_FORBIDDEN : jamais de fenêtre de Windows


class SecretError(RuntimeError):
    """Valeur chiffrée illisible ici (autre PC, autre session Windows, fichier abîmé)."""


def available() -> bool:
    return sys.platform == "win32"


def protect(text: str) -> str:
    """`text` chiffré (« dpapi:… ») ; vide ou hors Windows : inchangé."""
    if not text or not available():
        return text
    return PREFIX + base64.b64encode(_crypt(text.encode("utf-8"), encrypt=True)).decode("ascii")


def unprotect(value: str) -> str:
    """Valeur en clair ; une valeur sans préfixe (ancien fichier) est rendue telle quelle."""
    if not value.startswith(PREFIX):
        return value
    if not available():
        raise SecretError("secret chiffré par Windows, illisible sur ce système")
    try:
        data = base64.b64decode(value[len(PREFIX):], validate=True)
    except ValueError as e:
        raise SecretError(f"secret chiffré abîmé ({e})") from e
    return _crypt(data, encrypt=False).decode("utf-8")


def is_protected(value: str) -> bool:
    return value.startswith(PREFIX)


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _crypt(data: bytes, encrypt: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    source, _keep = _blob(data)
    entropy, _keep_entropy = _blob(_ENTROPY)
    out = _Blob()
    if encrypt:
        ok = crypt32.CryptProtectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                                      _UI_FORBIDDEN, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                                        _UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecretError(f"chiffrement Windows refusé (erreur {ctypes.GetLastError()})")  # type: ignore[attr-defined]
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
