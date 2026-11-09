"""Vignette toujours au premier plan quand le logiciel passe en arrière-plan pendant une sortie.

Ses valeurs viennent du catalogue d'indicateurs (`indicators.py`) : par défaut cible, puissance et temps
restant ; au clic droit sur une valeur, on la remplace, on en ajoute une (5 au plus) ou on la retire.
Le choix est propre à chaque mode (séance, libre, parcours) et gardé dans le profil (étape 10).
Elle se déplace à la souris ; un clic (sans glisser) ramène la fenêtre principale."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from .indicators import CATALOG, Mode, RideView
from .theme import LINE, MUTED, PANEL, number_font, set_style, ui_font

MARGIN = 16  # écart au bord de l'écran, position par défaut (en haut à droite)
DRAG_PX = 4  # au-delà, le clic devient un déplacement
MAX_PLACES = 5
# Valeurs d'origine de chaque mode : ce que montrait la vignette avant l'étape 10.
DEFAULTS: dict[Mode, list[str]] = {
    Mode.WORKOUT: ["target", "power", "brick_remaining"],
    Mode.FREE: ["target", "power", "elapsed"],
    Mode.ROUTE: ["target", "power", "route_remaining"],
}


def layout_key(mode: Mode) -> str:
    """Clé du choix de la vignette dans `Profile.layouts`, à côté de celles des écrans."""
    return f"mini:{mode.value}"


def choices(mode: Mode) -> list[str]:
    """Indicateurs qui ont un sens dans ce mode, dans l'ordre du catalogue."""
    return [key for key, i in CATALOG.items() if mode in i.modes]


def foreground_window() -> tuple[int, bool] | None:
    """Fenêtre au premier plan selon Windows : (identifiant, vrai si elle est à ce logiciel), 0 si aucune.
    None hors Windows (ou Qt hors écran) : on s'en tient alors à l'état de l'application tenu par Qt.

    Qt peut croire l'application active après le menu du clic droit de la vignette, alors que la fenêtre
    principale est restée derrière : la vignette se cachait (étape 10)."""
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(hwnd), pid.value == os.getpid()


def valid(mode: Mode, keys: object) -> bool:
    return (isinstance(keys, list) and 1 <= len(keys) <= MAX_PLACES and len(set(keys)) == len(keys)
            and all(k in choices(mode) for k in keys))


class _Place(QWidget):
    """Une valeur de la vignette : titre au-dessus, chiffres en dessous."""

    def __init__(self, size: int) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = QLabel("")
        self.title.setFont(ui_font(8, QFont.DemiBold))
        self.title.setStyleSheet(f"color: {MUTED};")
        self.value = QLabel("—")
        self.value.setFont(number_font(size))
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        self.setContextMenuPolicy(Qt.CustomContextMenu)


class MiniWindow(QWidget):
    """Petite fenêtre sans bordure, au-dessus des autres, qui ne prend pas le focus.

    `changed` signale un choix de valeurs (mode, liste d'indicateurs ; None : valeurs d'origine)."""

    clicked = Signal()
    changed = Signal(object, object)

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Home trainer")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Cliquer pour revenir au logiciel · glisser pour déplacer · clic droit : valeurs affichées")
        self._press: QPoint | None = None  # point de la vignette saisi par la souris
        self._dragged = False
        self._placed = False  # position par défaut tant qu'on ne l'a pas déplacée
        self.menu_open = False  # menu du clic droit ouvert : la vignette reste affichée
        self.mode = Mode.WORKOUT
        self.chosen: dict[Mode, list[str]] = {}  # choix de l'utilisateur, mode par mode
        self.keys: list[str] = []
        self.places: list[_Place] = []
        self._view: RideView | None = None

        frame = QFrame(self)
        frame.setObjectName("mini")
        frame.setStyleSheet(f"QFrame#mini {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}"
                            "QLabel { background: transparent; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        body = QVBoxLayout(frame)
        body.setContentsMargins(14, 8, 14, 10)
        body.setSpacing(0)
        self.row = QHBoxLayout()
        self.row.setSpacing(18)
        body.addLayout(self.row)
        self.status = QLabel("")
        self.status.setFont(ui_font(8, QFont.DemiBold))
        self.status.setStyleSheet(f"color: {MUTED};")
        body.addWidget(self.status)
        self.status.hide()
        self._build()

    # --- choix des valeurs ------------------------------------------------

    def set_choices(self, chosen: dict[Mode, list[str] | None]) -> None:
        """Choix gardés dans le profil, mode par mode ; un choix invalide ou absent : valeurs d'origine."""
        self.chosen = {mode: list(keys) for mode, keys in chosen.items() if valid(mode, keys)}
        self._build()

    def keys_for(self, mode: Mode) -> list[str]:
        return list(self.chosen.get(mode, DEFAULTS[mode]))

    def _set(self, keys: list[str]) -> None:
        if keys == DEFAULTS[self.mode]:
            self.chosen.pop(self.mode, None)
        else:
            self.chosen[self.mode] = keys
        self._build()
        self.changed.emit(self.mode, self.chosen.get(self.mode))

    def choose(self, index: int, key: str) -> None:
        """Met `key` à la place `index` ; s'il était déjà affiché ailleurs, les deux places s'échangent."""
        keys = list(self.keys)
        if key in keys:
            keys[keys.index(key)] = keys[index]
        keys[index] = key
        self._set(keys)

    def add(self, index: int, key: str) -> None:
        """Ajoute `key` juste à droite de la place `index`."""
        if len(self.keys) < MAX_PLACES and key not in self.keys:
            self._set(self.keys[:index + 1] + [key] + self.keys[index + 1:])

    def remove(self, index: int) -> None:
        if len(self.keys) > 1:
            self._set(self.keys[:index] + self.keys[index + 1:])

    def reset(self) -> None:
        self._set(list(DEFAULTS[self.mode]))

    def _menu(self, index: int) -> QMenu:
        menu = QMenu(self)
        for key in choices(self.mode):
            action = menu.addAction(CATALOG[key].menu_name)
            action.setCheckable(True)
            action.setChecked(key == self.keys[index])
            action.triggered.connect(lambda _=False, k=key: self.choose(index, k))
        menu.addSeparator()
        more = menu.addMenu("Ajouter une valeur à droite")
        for key in (k for k in choices(self.mode) if k not in self.keys):
            more.addAction(CATALOG[key].menu_name, lambda k=key: self.add(index, k))
        more.setEnabled(len(self.keys) < MAX_PLACES and not more.isEmpty())
        menu.addAction("Retirer cette valeur", lambda: self.remove(index)).setEnabled(len(self.keys) > 1)
        menu.addAction("Valeurs d'origine", self.reset).setEnabled(self.keys != DEFAULTS[self.mode])
        return menu

    def _open_menu(self, index: int, pos: QPoint) -> None:
        menu = self._menu(index)
        menu.setAttribute(Qt.WA_DeleteOnClose)
        self.menu_open = True
        menu.aboutToHide.connect(lambda: setattr(self, "menu_open", False))
        menu.popup(self.places[index].mapToGlobal(pos))

    # --- affichage --------------------------------------------------------

    def _build(self) -> None:
        keys = self.keys_for(self.mode)
        if keys == self.keys and self.places:
            return
        for place in self.places:
            self.row.removeWidget(place)
            place.hide()
            place.deleteLater()
        self.keys = keys
        self.places = []
        for index, _key in enumerate(keys):
            place = _Place(30 if index == 0 else 26)
            place.customContextMenuRequested.connect(lambda pos, i=index: self._open_menu(i, pos))
            self.row.addWidget(place)
            self.places.append(place)
        if self._view is not None:
            self.refresh(self._view, self.status.text())
        self.adjustSize()

    def refresh(self, view: RideView, status: str = "") -> None:
        """Valeurs de la sortie `view` (le mode suit la sortie) et ligne d'état (pause, brique…)."""
        self._view = view
        if view.mode is not self.mode:
            self.mode = view.mode
            self._build()
        for place, key in zip(self.places, self.keys):
            indicator = CATALOG[key]
            shown = indicator.show(view)
            text = shown.value if not shown.unit or shown.value == "—" else f"{shown.value} {shown.unit}"
            title = shown.title or indicator.title
            if place.title.text() != title:
                place.title.setText(title)
            if place.value.text() != text:
                place.value.setText(text)
            set_style(place.value, f"color: {indicator.value_color(shown)};")
        if self.status.text() != status:
            self.status.setText(status)
        self.status.setVisible(bool(status))
        self.adjustSize()

    def texts(self) -> list[tuple[str, str]]:
        """(titre, valeur) de chaque place, de gauche à droite."""
        return [(p.title.text(), p.value.text()) for p in self.places]

    def popup(self, near: QWidget | None = None) -> None:
        """Affiche la vignette sans voler le focus ; la 1re fois, en haut à droite de l'écran de `near`."""
        if self.isVisible():
            return
        self.adjustSize()
        if not self._placed:
            screen = (near.screen() if near is not None else None) or QGuiApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - MARGIN, area.top() + MARGIN)
        self.show()

    # --- souris : glisser pour déplacer, cliquer pour revenir ------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.button() == Qt.LeftButton:
            self._press = event.position().toPoint()
            self._dragged = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if self._press is None:
            return
        offset = event.position().toPoint() - self._press
        if self._dragged or offset.manhattanLength() > DRAG_PX:
            self._dragged = self._placed = True
            self.move(self.pos() + offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.button() == Qt.LeftButton and self._press is not None:
            self._press = None
            if not self._dragged:
                self.clicked.emit()
