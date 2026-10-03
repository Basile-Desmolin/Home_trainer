"""Lecture et écriture des séances, format choisi d'après l'extension.

| Extension | Format                         | Puissance      |
| --------- | ------------------------------ | -------------- |
| .zwo      | Zwift (XML)                    | % FTP          |
| .erg      | ERG (TrainerRoad, Golden Cheetah…) | watts      |
| .mrc      | MRC                            | % FTP          |
| .fit      | FIT workout (Garmin)           | watts ou % FTP |
"""

from __future__ import annotations

import os
from pathlib import Path

from ..workout import PowerUnit, Workout
from . import erg, fit, zwo
from .common import FormatError

EXTENSIONS = (".zwo", ".erg", ".mrc", ".fit")


def decode_workout(data: bytes, ext: str, warnings: list[str] | None = None,
                   default_name: str = "Séance") -> Workout:
    ext = _check_ext(ext)
    if ext == ".zwo":
        return zwo.decode(data, warnings, default_name)
    if ext in (".erg", ".mrc"):
        unit = PowerUnit.WATTS if ext == ".erg" else PowerUnit.FTP_PERCENT
        return erg.decode(data, unit, warnings, default_name)
    return fit.decode_workout(data, warnings)


def encode_workout(workout: Workout, ext: str, ftp: float | None = None) -> bytes:
    """Encode la séance. `ftp` sert à convertir watts et % FTP quand le format l'exige."""
    ext = _check_ext(ext)
    if ext == ".zwo":
        return zwo.encode(workout, ftp)
    if ext == ".erg":
        return erg.encode(workout, PowerUnit.WATTS, ftp)
    if ext == ".mrc":
        return erg.encode(workout, PowerUnit.FTP_PERCENT, ftp)
    return fit.encode_workout(workout)


def load_workout(path: str | os.PathLike, warnings: list[str] | None = None) -> Workout:
    path = Path(path)
    return decode_workout(path.read_bytes(), path.suffix, warnings, default_name=path.stem)


def save_workout(workout: Workout, path: str | os.PathLike, ftp: float | None = None) -> None:
    path = Path(path)
    path.write_bytes(encode_workout(workout, path.suffix, ftp))


# Noms alignés sur ceux du module .fit
read_workout = load_workout
write_workout = save_workout


def _check_ext(ext: str) -> str:
    ext = ext.lower() if ext.startswith(".") else "." + ext.lower()
    if ext not in EXTENSIONS:
        raise FormatError(f"format {ext or '(sans extension)'} non pris en charge "
                          f"(attendu : {', '.join(EXTENSIONS)})")
    return ext


__all__ = ["EXTENSIONS", "FormatError", "decode_workout", "encode_workout",
           "load_workout", "read_workout", "save_workout", "write_workout"]
