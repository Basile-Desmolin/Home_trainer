"""Une grande valeur avec son libellé, et la grille des cartes du catalogue d'indicateurs,
partagées par la séance, le mode libre et le parcours."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from .gauges import ZoneBar
from .indicators import CATALOG, RideView
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


@dataclass(frozen=True)
class Slot:
    """Place d'un indicateur du catalogue dans la grille d'un écran."""

    key: str
    row: int
    col: int
    size: int = 34  # taille des chiffres
    rows: int = 1
    cols: int = 1


class MetricGrid(QGridLayout):
    """Cartes du catalogue d'indicateurs, placées selon `slots` ; l'écran ajoute ses blocs de commande
    aux places restantes (`addWidget`). `refresh` met toutes les cartes à jour."""

    def __init__(self, slots: list[Slot]) -> None:
        super().__init__()
        self.setSpacing(10)
        self.cards: dict[str, Metric] = {}
        self._gauges: dict[str, ZoneBar | QProgressBar] = {}
        for slot in slots:
            indicator = CATALOG[slot.key]
            card = Metric(indicator.title, slot.size, indicator.color)
            if indicator.gauge == "zones":
                self._gauges[slot.key] = gauge = ZoneBar()
                card.add(gauge)
            elif indicator.gauge == "progress":
                self._gauges[slot.key] = bar = QProgressBar()
                bar.setTextVisible(False)
                bar.setFixedHeight(6)
                bar.setRange(0, 1000)
                card.add(bar)
            self.cards[slot.key] = card
            self.addWidget(card, slot.row, slot.col, slot.rows, slot.cols)

    def refresh(self, view: RideView) -> None:
        for key, card in self.cards.items():
            shown = CATALOG[key].show(view)
            card.set(shown.value, shown.sub, shown.unit)
            gauge = self._gauges.get(key)
            if isinstance(gauge, ZoneBar):
                gauge.set_zone(shown.zone)
            elif gauge is not None:
                gauge.setValue(round(1000 * (shown.progress or 0.0)))
