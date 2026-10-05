"""Empêche le PC de se mettre en veille (et l'écran de s'éteindre) pendant une sortie.

Sous Windows, `SetThreadExecutionState` signale au système que l'appli est
en cours d'utilisation, comme un lecteur vidéo ; l'état vaut pour le fil qui
l'a demandé, donc on l'appelle toujours depuis le même (celui de la fenêtre).
Ailleurs, rien n'est fait.

    awake = KeepAwake()
    awake.set(True)    # sortie en cours : ni veille, ni écran noir
    awake.set(False)   # retour au réglage d'alimentation habituel
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def _windows_call() -> Callable[[int], int] | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return ctypes.windll.kernel32.SetThreadExecutionState
    except (ImportError, AttributeError, OSError) as e:  # pragma: no cover (Windows seulement)
        log.warning("anti-veille indisponible : %s", e)
        return None


class KeepAwake:
    def __init__(self, call: Callable[[int], int] | None = None) -> None:
        self._call = call if call is not None else _windows_call()
        self.active = False

    @property
    def supported(self) -> bool:
        return self._call is not None

    def set(self, active: bool) -> None:
        """Ne fait l'appel système qu'au changement d'état."""
        if active == self.active:
            return
        self.active = active
        if self._call is None:
            return
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if active else 0)
        if not self._call(flags):
            log.warning("SetThreadExecutionState a échoué (%#x)", flags)
