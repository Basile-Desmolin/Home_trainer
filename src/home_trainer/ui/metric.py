"""Une grande valeur avec son libellé, et la grille des cartes du catalogue d'indicateurs,
partagées par la séance, le mode libre et le parcours."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QProgressBar, QVBoxLayout, QWidget

from .gauges import ZoneBar
from .indicators import CATALOG, Mode, RideView
from .theme import HEART_ZONES, TEXT, number_font, set_style, tinted_card

# Unité détachée de la valeur et affichée en petit : « 238 W » → « 238 » + « W ».
UNITS = ("W", "W/kg", "km", "%", "bpm", "rpm", "km/h", "tr/min")


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
        set_style(self.value, f"color: {QColor(color).name()}; background: transparent;")

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
    aux places restantes (`addWidget`). `refresh` met toutes les cartes à jour.

    Chaque place peut montrer un autre indicateur du mode (clic droit sur la carte) : `keys` donne
    l'indicateur de chaque place, dans l'ordre de `slots`. Choisir un indicateur déjà affiché échange
    les deux places. `changed` signale un choix (None : retour à la disposition d'origine).
    """

    changed = Signal(object)

    def __init__(self, slots: list[Slot], mode: Mode | None = None) -> None:
        super().__init__()
        self.setSpacing(10)
        self.slots = slots
        self.mode = mode
        self.keys = [slot.key for slot in slots]
        self.cards: dict[str, Metric] = {}  # même dictionnaire pour toute la vie de la grille
        self._gauges: dict[str, ZoneBar | QProgressBar] = {}
        self._view: RideView | None = None
        self.menu_hook: Callable[[str, QMenu], None] | None = None  # ajoute au menu d'une carte (indicateur, menu)
        self._build()

    @property
    def defaults(self) -> list[str]:
        return [slot.key for slot in self.slots]

    def choices(self) -> list[str]:
        """Indicateurs qui ont un sens sur cet écran, dans l'ordre du catalogue."""
        return [key for key, i in CATALOG.items() if self.mode is None or self.mode in i.modes]

    def _build(self) -> None:
        for card in self.cards.values():
            self.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards.clear()
        self._gauges.clear()
        for index, (slot, key) in enumerate(zip(self.slots, self.keys)):
            indicator = CATALOG[key]
            card = Metric(indicator.title, slot.size, indicator.color)
            if indicator.gauge in ("zones", "heart_zones"):
                self._gauges[key] = gauge = ZoneBar(colors=HEART_ZONES if indicator.gauge == "heart_zones" else None)
                card.add(gauge)
            elif indicator.gauge == "progress":
                self._gauges[key] = bar = QProgressBar()
                bar.setTextVisible(False)
                bar.setFixedHeight(6)
                bar.setRange(0, 1000)
                card.add(bar)
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda pos, i=index, c=card: self._menu(i).popup(c.mapToGlobal(pos)))
            card.setToolTip("Clic droit : changer d'indicateur")
            self.cards[key] = card
            self.addWidget(card, slot.row, slot.col, slot.rows, slot.cols)
        if self._view is not None:
            self.refresh(self._view)

    def set_keys(self, keys: list[str] | None) -> None:
        """Indicateur de chaque place (choix enregistré) ; None ou un choix invalide : disposition d'origine."""
        valid = (keys is not None and len(keys) == len(self.slots) and len(set(keys)) == len(keys)
                 and all(k in self.choices() for k in keys))
        new = list(keys) if valid else self.defaults
        if new != self.keys:
            self.keys = new
            self._build()

    def choose(self, index: int, key: str) -> None:
        """Met `key` à la place `index` ; s'il était déjà affiché ailleurs, les deux places s'échangent."""
        keys = list(self.keys)
        if key in keys:
            other = keys.index(key)
            keys[other] = keys[index]
        keys[index] = key
        self.set_keys(keys)
        self.changed.emit(None if self.keys == self.defaults else list(self.keys))

    def reset(self) -> None:
        self.set_keys(None)
        self.changed.emit(None)

    def _menu(self, index: int) -> QMenu:
        menu = QMenu(self.parentWidget())
        for key in self.choices():
            action = menu.addAction(CATALOG[key].menu_name)
            action.setCheckable(True)
            action.setChecked(key == self.keys[index])
            action.triggered.connect(lambda _=False, k=key: self.choose(index, k))
        menu.addSeparator()
        menu.addAction("Disposition d'origine", self.reset).setEnabled(self.keys != self.defaults)
        if self.menu_hook is not None:  # réglages propres à l'indicateur (lissage de la puissance…)
            self.menu_hook(self.keys[index], menu)
        return menu

    def refresh(self, view: RideView) -> None:
        self._view = view
        view = replace(view, on_screen=frozenset(self.cards))
        for key, card in self.cards.items():
            shown = CATALOG[key].show(view)
            card.set(shown.value, shown.sub, shown.unit)
            title = shown.title or CATALOG[key].title
            if card.title.text() != title:
                card.title.setText(title)
            gauge = self._gauges.get(key)
            if isinstance(gauge, ZoneBar):
                gauge.set_zone(shown.zone)
                gauge.setVisible(CATALOG[key].gauge == "zones" or view.hr_max is not None)  # zones de FC
            elif gauge is not None:
                gauge.setValue(round(1000 * (shown.progress or 0.0)))
