"""Journal des erreurs (audit, « Pas de fichier de log »)."""

import logging
import sys
import threading

import pytest

from home_trainer.logs import LOG_FILE, setup_logging


@pytest.fixture
def clean_logging(monkeypatch):
    """Rend les crochets d'exception et le niveau du journal comme avant le test."""
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in [h for h in root.handlers if h not in handlers]:
        root.removeHandler(h)
        h.close()
    root.setLevel(level)


def text(path):
    for h in logging.getLogger().handlers:
        h.flush()
    return path.read_text(encoding="utf-8")


def test_warnings_and_uncaught_errors_go_to_the_file(tmp_path, clean_logging):
    path = setup_logging(tmp_path / "journaux")
    assert path == tmp_path / "journaux" / LOG_FILE
    logging.getLogger("home_trainer.history").warning("historique illisible : %s", "test")
    try:
        raise ValueError("boum dans la fenêtre")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    worker = threading.Thread(target=lambda: 1 / 0, name="envoi des sorties")
    worker.start()
    worker.join()
    content = text(path)
    assert "WARNING home_trainer.history : historique illisible : test" in content
    assert "ValueError: boum dans la fenêtre" in content
    assert "tâche « envoi des sorties »" in content and "ZeroDivisionError" in content


def test_set_up_twice_writes_once(tmp_path, clean_logging):
    path = setup_logging(tmp_path)
    setup_logging(tmp_path)
    logging.getLogger("home_trainer").warning("une seule fois")
    assert text(path).count("une seule fois") == 1


def test_unwritable_folder_does_not_stop_the_app(tmp_path, clean_logging):
    blocker = tmp_path / "fichier"
    blocker.write_text("")
    assert setup_logging(blocker / "journaux") is None
