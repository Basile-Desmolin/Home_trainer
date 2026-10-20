"""Vignette toujours au premier plan quand le logiciel passe en arrière-plan pendant une sortie :
puissance cible, puissance mesurée et temps restant. Elle se déplace à la souris ;
un clic (sans glisser) ramène la fenêtre principale."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from .theme import LINE, MUTED, PANEL, TEXT, number_font, ui_font

MARGIN = 16  # écart au bord de l'écran, position par défaut (en haut à droite)
DRAG_PX = 4  # au-delà, le clic devient un déplacement


@dataclass(frozen=True)
class MiniInfo:
    """Ce qu'affiche la vignette : trois valeurs avec leur libellé, et la couleur de la cible."""

    target: str
    power: str
    remaining: str
    remaining_title: str = "RESTE"
    target_title: str = "CIBLE"
    color: QColor | str | None = None  # zone de la cible (None : blanc)
    status: str = ""  # « En pause »…


class MiniWindow(QWidget):
    """Petite fenêtre sans bordure, au-dessus des autres, qui ne prend pas le focus."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Home trainer")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Cliquer pour revenir au logiciel · glisser pour déplacer")
        self._press: QPoint | None = None  # point de la vignette saisi par la souris
        self._dragged = False
        self._placed = False  # position par défaut tant qu'on ne l'a pas déplacée

        frame = QFrame(self)
        frame.setObjectName("mini")
        frame.setStyleSheet(f"QFrame#mini {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}"
                            "QLabel { background: transparent; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        grid = QGridLayout(frame)
        grid.setContentsMargins(14, 8, 14, 10)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(0)
        self.titles: list[QLabel] = []
        self.values: list[QLabel] = []
        for col, title in enumerate(("CIBLE", "PUISSANCE", "RESTE")):
            label = QLabel(title)
            label.setFont(ui_font(8, QFont.DemiBold))
            label.setStyleSheet(f"color: {MUTED};")
            value = QLabel("—")
            value.setFont(number_font(30 if col == 0 else 26))
            value.setStyleSheet(f"color: {TEXT};")
            grid.addWidget(label, 0, col)
            grid.addWidget(value, 1, col)
            self.titles.append(label)
            self.values.append(value)
        self.status = QLabel("")
        self.status.setFont(ui_font(8, QFont.DemiBold))
        self.status.setStyleSheet(f"color: {MUTED};")
        grid.addWidget(self.status, 2, 0, 1, 3)
        self.status.hide()

    def set_info(self, info: MiniInfo) -> None:
        self.titles[0].setText(info.target_title)
        self.titles[2].setText(info.remaining_title)
        for value, text in zip(self.values, (info.target, info.power, info.remaining)):
            if value.text() != text:
                value.setText(text)
        color = QColor(info.color).name() if info.color is not None else TEXT
        self.values[0].setStyleSheet(f"color: {color};")
        self.status.setText(info.status)
        self.status.setVisible(bool(info.status))
        self.adjustSize()

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
