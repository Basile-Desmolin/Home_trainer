"""Fenêtre « Historique » : les sorties enregistrées du profil, les totaux des dernières
semaines, la forme (condition et fatigue) et la courbe des meilleures puissances.

Double-clic sur une sortie : son bilan.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..history import RideHistory
from ..ride_stats import CURVE_S, RideSummary, duration_label
from .chart import ACCENT, BG, MUTED, POWER, TEXT, hms, paint_panel
from .metric import Metric
from .ride_summary import SummaryDialog, number, ride_date

HEADERS = ["Date", "Sortie", "Durée", "Distance", "Moy.", "Norm.", "TSS", "FC moy."]
COL_DATE, COL_TITLE, COL_DURATION, COL_DISTANCE, COL_AVG, COL_NP, COL_TSS, COL_HR = range(len(HEADERS))
WEEKS = 12
RECENT_DAYS = 42  # courbe « 6 dernières semaines »


class RideItem(QTreeWidgetItem):
    """Ligne de la liste ; le tri suit les valeurs et non le texte affiché."""

    def __init__(self, ride: RideSummary) -> None:
        super().__init__([ride_date(ride.start), ride.title or "Sortie", hms(ride.duration_s),
                          number(ride.distance_km, "km", 1), number(ride.avg_power_w, "W"),
                          number(ride.normalized_power_w, "W"), number(ride.tss), number(ride.avg_hr_bpm, "bpm")])
        self.ride = ride
        self.keys = {COL_DATE: ride.start, COL_TITLE: (ride.title or "").casefold(), COL_DURATION: ride.duration_s,
                     COL_DISTANCE: ride.distance_km or 0, COL_AVG: ride.avg_power_w,
                     COL_NP: ride.normalized_power_w, COL_TSS: ride.tss, COL_HR: ride.avg_hr_bpm or 0}
        for col in range(COL_DURATION, len(HEADERS)):
            self.setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        col = self.treeWidget().sortColumn() if self.treeWidget() else COL_DATE
        if isinstance(other, RideItem):
            return (self.keys[col], self.ride.start) < (other.keys[col], other.ride.start)
        return super().__lt__(other)


class WeeksChart(QWidget):
    """Heures roulées par semaine (barres) et TSS de la semaine au-dessus."""

    def __init__(self, history: RideHistory, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history = history
        self.setMinimumSize(320, 170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        weeks = self.history.weeks(WEEKS)
        p = QPainter(self)
        paint_panel(p, self)
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(10, 4, self.width() - 20, 18), Qt.AlignLeft | Qt.AlignVCenter, "HEURES PAR SEMAINE")
        area = QRectF(10, 58, self.width() - 20, self.height() - 82)
        top = max([s for _, s, _, _ in weeks] + [3600.0])
        slot = area.width() / len(weeks)
        for i, (monday, seconds, tss, count) in enumerate(weeks):
            x = area.left() + i * slot
            h = area.height() * seconds / top
            current = i == len(weeks) - 1
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT if current else POWER) if seconds else QColor(BG))
            p.drawRoundedRect(QRectF(x + slot * 0.18, area.bottom() - max(h, 2), slot * 0.64, max(h, 2)), 3, 3)
            p.setPen(QColor(TEXT))
            if seconds:
                p.drawText(QRectF(x, area.bottom() - h - 30, slot, 14), Qt.AlignCenter,
                           f"{seconds / 3600:.1f}".replace(".", ","))
                p.setPen(QColor(MUTED))
                p.drawText(QRectF(x, area.bottom() - h - 16, slot, 14), Qt.AlignCenter, f"{tss:.0f}")
            p.setPen(QColor(MUTED))
            if i % 2 == len(weeks) % 2 or current:
                p.drawText(QRectF(x - 6, area.bottom() + 4, slot + 12, 16), Qt.AlignCenter,
                           f"{monday.day}/{monday.month}")
        p.end()


class PowerCurve(QWidget):
    """Meilleures puissances par durée : de toujours et des 6 dernières semaines."""

    def __init__(self, history: RideHistory, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history = history
        self.setMinimumSize(320, 170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        best = self.history.best_powers()
        recent = self.history.best_powers(since=time.time() - RECENT_DAYS * 86400)
        p = QPainter(self)
        paint_panel(p, self)
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(10, 4, self.width() - 20, 18), Qt.AlignLeft | Qt.AlignVCenter, "MEILLEURES PUISSANCES")
        p.setPen(QColor(ACCENT))
        p.drawText(QRectF(10, 4, self.width() - 20, 18), Qt.AlignRight | Qt.AlignVCenter, "toujours")
        p.setPen(QColor(POWER))
        p.drawText(QRectF(10, 4, self.width() - 90, 18), Qt.AlignRight | Qt.AlignVCenter, "6 semaines")
        if not best:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Pas encore de sortie")
            p.end()
            return
        area = QRectF(44, 30, self.width() - 60, self.height() - 54)
        top = max(w for w, _ in best.values()) * 1.1
        lo, hi = math.log(CURVE_S[0]), math.log(CURVE_S[-1])

        def x(seconds: int) -> float:
            return area.left() + (math.log(seconds) - lo) / (hi - lo) * area.width()

        def y(watts: float) -> float:
            return area.bottom() - watts / top * area.height()

        p.setPen(QPen(QColor(BG), 1))
        for watts in range(100, int(top), 100 if top < 900 else 200):
            p.drawLine(QPointF(area.left(), y(watts)), QPointF(area.right(), y(watts)))
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, y(watts) - 8, area.left() - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{watts}")
            p.setPen(QPen(QColor(BG), 1))
        p.setPen(QColor(MUTED))
        for seconds in (5, 60, 300, 1200, 3600):
            p.drawText(QRectF(x(seconds) - 24, area.bottom() + 4, 48, 16), Qt.AlignCenter, duration_label(seconds))
        for curve, color, width in ((best, ACCENT, 4), (recent, POWER, 2)):
            path = QPainterPath()
            points = [(s, curve[s][0]) for s in CURVE_S if s in curve]
            for i, (s, w) in enumerate(points):
                (path.lineTo if i else path.moveTo)(QPointF(x(s), y(w)))
            p.setPen(QPen(QColor(color), width))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.end()


class HistoryDialog(QDialog):
    def __init__(self, history: RideHistory, profile_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history = history
        self.setWindowTitle(f"Historique · {profile_name}" if profile_name else "Historique")
        self.resize(960, 700)
        layout = QVBoxLayout(self)

        tiles = QHBoxLayout()
        self.week_tile = Metric("CETTE SEMAINE", 22)
        self.month_tile = Metric("4 DERNIÈRES SEMAINES", 22)
        self.fitness_tile = Metric("CONDITION", 22)
        self.form_tile = Metric("FORME", 22)
        self.fitness_tile.setToolTip("Charge d'entraînement des 6 dernières semaines (CTL, moyenne du TSS par jour)")
        self.form_tile.setToolTip("Condition moins fatigue (TSB) : positive, on est frais ; "
                                  "très négative, on en a beaucoup fait ces derniers jours")
        for tile in (self.week_tile, self.month_tile, self.fitness_tile, self.form_tile):
            tiles.addWidget(tile)
        layout.addLayout(tiles)

        charts = QHBoxLayout()
        self.weeks_chart = WeeksChart(history)
        self.curve = PowerCurve(history)
        charts.addWidget(self.weeks_chart, 1)
        charts.addWidget(self.curve, 1)
        layout.addLayout(charts, 2)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemDoubleClicked.connect(lambda item, _: self.show_ride(item.ride))
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.tree, 3)
        self.empty = QLabel("Aucune sortie pour l'instant : chaque sortie enregistrée s'ajoute ici avec son bilan.")
        self.empty.setObjectName("metricSub")
        layout.addWidget(self.empty)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("Voir le bilan")
        self.open_button.clicked.connect(lambda: self.selected() and self.show_ride(self.selected()))
        self.remove_button = QPushButton("Retirer de l'historique")
        self.remove_button.setToolTip("Retire la sortie de la liste ; son fichier reste sur le disque")
        self.remove_button.clicked.connect(self._remove)
        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        for ride in self.history.rides:
            self.tree.addTopLevelItem(RideItem(ride))
        self.tree.sortItems(COL_DATE, Qt.DescendingOrder)
        for col in range(len(HEADERS)):
            if col != COL_TITLE:
                self.tree.resizeColumnToContents(col)
        self.empty.setVisible(not self.history.rides)
        weeks = self.history.weeks(4)
        _, seconds, tss, count = weeks[-1]
        self.week_tile.set(hms(seconds) if seconds else "0:00", f"{count} sortie{'s' * (count > 1)} · TSS {tss:.0f}")
        seconds = sum(w[1] for w in weeks)
        count = sum(w[3] for w in weeks)
        self.month_tile.set(hms(seconds) if seconds else "0:00",
                            f"{count} sortie{'s' * (count > 1)} · TSS {sum(w[2] for w in weeks):.0f}")
        ctl, atl = self.history.fitness()
        self.fitness_tile.set(f"{ctl:.0f}", f"fatigue {atl:.0f}")
        form = ctl - atl
        self.form_tile.set(f"{form:+.0f}", "frais" if form > 5 else "fatigué" if form < -20 else "en charge")
        self._selection_changed()
        self.weeks_chart.update()
        self.curve.update()

    def selected(self) -> RideSummary | None:
        items = self.tree.selectedItems()
        return items[0].ride if items else None

    def _selection_changed(self) -> None:
        has = self.selected() is not None
        self.open_button.setEnabled(has)
        self.remove_button.setEnabled(has)

    def show_ride(self, ride: RideSummary) -> None:
        others = RideHistory()
        others.rides = [r for r in self.history.rides if r.start < ride.start]
        others.mark_records(ride)  # records battus ce jour-là
        SummaryDialog(ride, self).exec()

    def _remove(self) -> None:
        ride = self.selected()
        if ride is None:
            return
        if QMessageBox.question(self, "Retirer de l'historique",
                                f"Retirer « {ride.title or 'Sortie'} » du {ride_date(ride.start, False)} "
                                f"de l'historique ? Son fichier reste sur le disque.") == QMessageBox.Yes:
            self.history.remove(ride)
            self.refresh()
