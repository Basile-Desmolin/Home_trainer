"""Dossier de configuration de l'utilisateur : %APPDATA%\\HomeTrainer sous Windows,
~/.config/home-trainer ailleurs (ou $XDG_CONFIG_HOME/home-trainer)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "HomeTrainer"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "home-trainer"
