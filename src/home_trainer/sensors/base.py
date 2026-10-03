"""Socle commun des capteurs : connexion en tâche de fond, état, dernière mesure.

Un capteur (cardio Bluetooth ou ANT+, plus tard le home trainer Wahoo) tourne
dans son propre fil d'exécution : la radio ne bloque jamais l'interface. Il
publie ses mesures avec `_publish`, et l'interface lit la plus récente avec
`latest()` à son rythme. Une mesure trop vieille (capteur décroché, sangle
retirée) n'est plus renvoyée.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Generic, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class SensorState(str, Enum):
    STOPPED = "arrêté"
    SEARCHING = "recherche…"
    CONNECTED = "connecté"
    ERROR = "erreur"


class SensorUnavailable(RuntimeError):
    """Bibliothèque radio absente ou matériel introuvable (clé ANT+, adaptateur Bluetooth)."""


class BackgroundSensor(Generic[T]):
    """Capteur qui se connecte et lit ses mesures dans un fil d'exécution dédié.

    Les sous-classes implémentent `_run(stop)` : se connecter, publier les
    mesures avec `_publish`, et rendre la main dès que `stop` est levé. Si
    `_run` échoue ou se termine sans qu'on l'ait arrêté, on retente après
    `retry_s` secondes (capteur hors de portée, sangle pas encore portée…).
    """

    name = "capteur"
    retry_s = 3.0
    max_age_s = 5.0  # au-delà, la dernière mesure est considérée comme perdue

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: T | None = None
        self._latest_at = 0.0
        self._state = SensorState.STOPPED
        self._message = ""
        self._listeners: list[Callable[[T], None]] = []

    # --- cycle de vie ----------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"capteur {self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._interrupt()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._set_state(SensorState.STOPPED)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- lecture ---------------------------------------------------------

    def latest(self) -> T | None:
        """Dernière mesure, ou None si elle date de plus de `max_age_s`."""
        with self._lock:
            if self._latest is None or time.monotonic() - self._latest_at > self.max_age_s:
                return None
            return self._latest

    @property
    def state(self) -> SensorState:
        return self._state

    @property
    def status(self) -> str:
        """État lisible : « connecté », « erreur : clé ANT+ introuvable »…"""
        return f"{self._state.value} : {self._message}" if self._message else self._state.value

    def add_listener(self, callback: Callable[[T], None]) -> None:
        """Appelé (dans le fil du capteur) à chaque nouvelle mesure."""
        self._listeners.append(callback)

    # --- pour les sous-classes -------------------------------------------

    def _run(self, stop: threading.Event) -> None:
        raise NotImplementedError

    def _interrupt(self) -> None:
        """Débloque `_run` s'il attend dans une bibliothèque radio (optionnel)."""

    def _publish(self, reading: T) -> None:
        with self._lock:
            self._latest = reading
            self._latest_at = time.monotonic()
        if self._state is not SensorState.CONNECTED:
            self._set_state(SensorState.CONNECTED)
        for callback in self._listeners:
            callback(reading)

    def _set_state(self, state: SensorState, message: str = "") -> None:
        self._state = state
        self._message = message

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._set_state(SensorState.SEARCHING)
            try:
                self._run(self._stop)
            except SensorUnavailable as e:
                log.warning("%s : %s", self.name, e)
                self._set_state(SensorState.ERROR, str(e))
                return  # inutile de réessayer : il manque une bibliothèque ou le matériel
            except Exception as e:  # noqa: BLE001 (une erreur radio ne doit pas tuer le fil)
                log.warning("%s : %s", self.name, e, exc_info=True)
                self._set_state(SensorState.ERROR, str(e) or type(e).__name__)
            self._stop.wait(self.retry_s)
