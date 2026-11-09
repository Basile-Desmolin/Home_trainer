"""Réglages communs aux tests.

Fermeture propre de Qt en fin de séance : des fenêtres créées par les tests (par exemple une `ProfileDialog`
gardée en vie par un cycle de références) ne seraient sinon détruites qu'à la fermeture de Python, parfois
après la `QApplication`, ce qui fait planter le processus (violation d'accès) alors que tous les tests passent.
"""

import gc
import sys


def pytest_sessionfinish():
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6 import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    gc.collect()  # détruit les fenêtres prises dans des cycles tant que l'application existe
    for widget in QtWidgets.QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    QtCore.QCoreApplication.processEvents()
    gc.collect()
