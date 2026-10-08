"""Une grande valeur avec son libellé, partagée par la séance, le mode libre et le parcours."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import TEXT, number_font, tinted_card

# Unité détachée de la valeur et affichée en petit : « 238 W » → « 238 » + « W ».
UNITS = ("W", "km", "%", "bpm", "rpm", "km/h", "tr/min")


def split_unit(text: str) -> tuple[str, str]:
    value, _, unit = text.rpartition(" ")
    return (value, unit) if value and unit in UNITS else (text, "")


class Metric(QFrame):
    """Une grande valeur (chiffres condensés, unité en petit) avec son libellé et une ligne de détail."""

    def __init__(self, title: str, size: int = 34, color: str = TEXT) -> None:
        super().__init__()
        self.setObjectName("metric")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(0)
        self.title = QLabel(title)
        self.title.setObjectName("metricTitle")
        size = round(size * 1.25)  # police condensée : plus haute à largeur égale
        self.value = QLabel("—")
        self.value.setFont(number_font(size))
        self.unit = QLabel("")
        self.unit.setObjectName("unit")
        self.unit.setFont(number_font(max(11, round(size * 0.36)), QFont.DemiBold))
        self.set_color(color)
        self.row = row = QHBoxLayout()  # valeur, unité, puis de la place (anneau de la cible…)
        row.setSpacing(5)
        row.addWidget(self.value, 0, Qt.AlignBottom)
        row.addWidget(self.unit, 0, Qt.AlignBottom)
        row.addStretch(1)
        self._unit_gap = round(size * 0.22)  # l'unité se pose sur la ligne de base des chiffres
        self.unit.setContentsMargins(0, 0, 0, self._unit_gap)
        self.sub = QLabel("")
        self.sub.setObjectName("metricSub")
        self.body = QVBoxLayout()  # zone libre sous la valeur (barre de zones…)
        self.body.setSpacing(6)
        layout.addWidget(self.title)
        layout.addLayout(row)
        layout.addStretch(1)
        layout.addLayout(self.body)
        layout.addWidget(self.sub)

    def add(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def set_color(self, color: str | QColor) -> None:
        self.value.setStyleSheet(f"color: {QColor(color).name()}; background: transparent;")

    def set_tint(self, color: str | QColor | None) -> None:
        """Teinte la carte (zone de puissance en cours) ; None : carte neutre."""
        sheet = tinted_card(color)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)

    def set(self, value: str, sub: str = "", unit: str | None = None) -> None:
        if unit is None:
            value, unit = split_unit(value)
        self.value.setText(value)
        self.unit.setText(unit)
        self.sub.setText(sub)
