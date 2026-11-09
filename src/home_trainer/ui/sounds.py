"""Sons du bip avant un changement de brique, fabriqués et joués par l'application.

Chaque son est un petit fichier WAV (mono, 16 bits, 44,1 kHz) écrit une fois dans le dossier
temporaire, puis joué par `QSoundEffect` (QtMultimedia). Sans QtMultimedia, ou si le son ne se
charge pas, le son système de Windows (`QApplication.beep()`) prend le relais.
"""

from __future__ import annotations

import io
import logging
import math
import struct
import tempfile
import wave
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

RATE = 44_100
VOLUME = 0.5  # sur 1 : assez fort pour couvrir le home trainer, sans saturer
FADE_S = 0.008  # montée et descente de chaque note : pas de clic

# Nom affiché dans le menu, dans l'ordre du menu. « windows » : son système, pas de fichier.
SOUND_NAMES = {"double": "Double bip", "court": "Bip court", "montant": "Bip montant", "windows": "Son de Windows"}
LAST = "dernier"  # son distinct de la dernière seconde


def _note(freq: float, seconds: float, end_freq: float | None = None) -> list[float]:
    """Une note (fréquence qui glisse jusqu'à `end_freq`, si donnée), en échantillons de −1 à 1."""
    n = round(seconds * RATE)
    fade = max(1, round(FADE_S * RATE))
    out, phase = [], 0.0
    for i in range(n):
        f = freq if end_freq is None else freq + (end_freq - freq) * i / n
        phase += 2 * math.pi * f / RATE
        envelope = min(1.0, i / fade, (n - 1 - i) / fade)
        out.append(math.sin(phase) * envelope)
    return out


def _silence(seconds: float) -> list[float]:
    return [0.0] * round(seconds * RATE)


def samples(name: str) -> list[float]:
    """Échantillons d'un son (`SOUND_NAMES` sauf « windows », ou `LAST`)."""
    if name == "court":
        return _note(880, 0.12)
    if name == "double":
        return _note(880, 0.08) + _silence(0.06) + _note(880, 0.08)
    if name == "montant":
        return _note(600, 0.2, 1200)
    if name == LAST:
        return _note(1320, 0.45)
    raise ValueError(f"son inconnu : {name}")


def wav_bytes(name: str) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", round(x * VOLUME * 32767)) for x in samples(name)))
    return buffer.getvalue()


def sound_file(name: str, folder: Path | None = None) -> Path:
    """Fichier WAV du son, écrit au premier appel (réécrit s'il a changé)."""
    folder = folder or Path(tempfile.gettempdir()) / "home-trainer-sons"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.wav"
    data = wav_bytes(name)
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)
    return path


class BeepPlayer:
    """Joue le bip choisi ; les sons sont chargés une fois, au premier usage."""

    def __init__(self) -> None:
        self._effects: dict[str, object] = {}

    def _effect(self, name: str):
        if name in self._effects:
            return self._effects[name]
        effect = None
        try:
            from PySide6.QtMultimedia import QSoundEffect
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(sound_file(name))))
            effect.setVolume(1.0)
        except Exception:  # pas de QtMultimedia, dossier temporaire inaccessible : son de Windows
            log.warning("son %s indisponible, son de Windows à la place", name, exc_info=True)
            effect = None
        self._effects[name] = effect
        return effect

    def preload(self, sound: str) -> None:
        """Charge le son et celui de la dernière seconde, pour que le premier bip parte à l'heure."""
        if sound != "windows":
            self._effect(sound)
            self._effect(LAST)

    def play(self, sound: str, last: bool = False, last_distinct: bool = True) -> None:
        if sound == "windows":
            QApplication.beep()
            return
        effect = self._effect(LAST if last and last_distinct else sound)
        if effect is None:
            QApplication.beep()
            return
        effect.play()
