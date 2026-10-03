"""Ouverture d'une séance depuis un fichier, pour l'interface.

S'appuie sur les modules de `home_trainer.formats` sans les modifier. Si un
point d'entrée commun `formats.read_workout(chemin)` existe, il est utilisé ;
sinon on choisit le décodeur d'après l'extension.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from ..workout import PowerUnit, Workout

FILE_FILTER = "Séances (*.zwo *.mrc *.erg *.fit);;Tous les fichiers (*)"


def load_workout(path: str | Path, warnings: list[str] | None = None) -> Workout:
    path = Path(path)
    if warnings is None:
        warnings = []
    formats = importlib.import_module("home_trainer.formats")
    if hasattr(formats, "read_workout"):
        return formats.read_workout(path, warnings)

    ext = path.suffix.lower()
    name = path.stem
    if ext in (".erg", ".mrc"):
        erg = importlib.import_module("home_trainer.formats.erg")
        unit = PowerUnit.WATTS if ext == ".erg" else PowerUnit.FTP_PERCENT
        return erg.decode(path.read_bytes(), unit, warnings, default_name=name)
    if ext == ".zwo":
        try:
            zwo = importlib.import_module("home_trainer.formats.zwo")
        except ImportError:
            raise ValueError("lecture .zwo pas encore disponible") from None
        return zwo.decode(path.read_bytes(), warnings, default_name=name)
    if ext == ".fit":
        fit = importlib.import_module("home_trainer.formats.fit")
        return fit.read_workout(path, warnings)
    raise ValueError(f"format non reconnu : {ext or path.name}")
