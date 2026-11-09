"""Journal des erreurs : en exécutable sans console, c'est le seul endroit où elles restent visibles.

Fichier `journaux/home-trainer.log` dans le dossier de configuration, en rotation (1 Mo,
trois anciens gardés). Les exceptions non rattrapées, de la fenêtre comme des tâches de
fond, y sont aussi écrites.
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import config_dir

LOG_FILE = "home-trainer.log"
MAX_BYTES = 1_000_000
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)s %(name)s : %(message)s"

log = logging.getLogger("home_trainer")


def log_dir() -> Path:
    return config_dir() / "journaux"


def setup_logging(directory: Path | None = None, level: int = logging.INFO) -> Path | None:
    """Branche le journal sur le fichier ; rend son chemin (None s'il n'a pas pu être ouvert :
    l'application tourne quand même)."""
    directory = Path(directory) if directory is not None else log_dir()
    root = logging.getLogger()
    root.setLevel(level)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / LOG_FILE
        handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError as e:
        print(f"journal impossible à ouvrir ({directory}) : {e}", file=sys.stderr)
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.set_name("home-trainer")
    for old in [h for h in root.handlers if h.get_name() == "home-trainer"]:  # un seul, même relancé
        root.removeHandler(old)
        old.close()
    root.addHandler(handler)
    _catch_uncaught()
    log.info("démarrage (Python %s)", sys.version.split()[0])
    return path


def _catch_uncaught() -> None:
    previous, previous_thread = sys.excepthook, threading.excepthook

    def on_error(kind, value, trace) -> None:
        if not issubclass(kind, KeyboardInterrupt):
            log.critical("erreur non rattrapée", exc_info=(kind, value, trace))
        previous(kind, value, trace)

    def on_thread_error(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not SystemExit:
            name = args.thread.name if args.thread is not None else "?"
            log.critical("erreur non rattrapée (tâche « %s »)", name,
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))  # pyright: ignore
        previous_thread(args)

    if getattr(sys.excepthook, "_home_trainer", False):
        return
    on_error._home_trainer = True  # type: ignore[attr-defined]
    sys.excepthook = on_error
    threading.excepthook = on_thread_error
