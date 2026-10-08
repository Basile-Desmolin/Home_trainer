"""Bilan d'une sortie : chiffres clés, temps par zone de puissance et meilleures puissances.

Montré en haut de la fenêtre « Enregistrer la sortie » à la fin de chaque sortie, et
depuis l'historique (`SummaryDialog`).
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..ride_stats import HIGHLIGHT_S, ZONE_NAMES, RideSummary, duration_label
from .chart import ACCENT, HEART, MUTED, PANEL, POWER, TEXT, ZONES, hms
from .metric import Metric

DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def ride_date(start: float, weekday: bool = True) -> str:
    """« mar. 8 oct. 2026 · 18:30 »."""
    d = datetime.fromtimestamp(start)
    text = f"{d.day} {MONTHS[d.month - 1]} {d.year} · {d:%H:%M}"
    return f"{DAYS[d.weekday()]} {text}" if weekday else text


def number(value: float | None, unit: str = "", digits: int = 0) -> str:
    if value is None:
        return "—"
    text = f"{value:.{digits}f}".replace(".", ",")
    return f"{text} {unit}".rstrip()


class ZoneBars(QWidget):
    """Temps passé dans chaque zone de puissance, une barre par zone."""

    ROW = 20

    def __init__(self, zone_s: list[float], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.zone_s = zone_s
        self.setMinimumHeight(self.ROW * len(ZONE_NAMES) + 4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = sum(self.zone_s) or 1
        longest = max(self.zone_s) or 1
        label_w, value_w = 130, 110
        bar_w = max(10, self.width() - label_w - value_w - 8)
        for i, (seconds, name) in enumerate(zip(self.zone_s, ZONE_NAMES)):
            y = i * self.ROW + 2
            color = QColor(ZONES[i][1])
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, y, label_w, self.ROW - 4), Qt.AlignLeft | Qt.AlignVCenter, f"Z{i + 1}  {name}")
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(PANEL))
            p.drawRoundedRect(QRectF(label_w, y + 3, bar_w, self.ROW - 8), 3, 3)
            if seconds > 0:
                p.setBrush(color)
                p.drawRoundedRect(QRectF(label_w, y + 3, max(4.0, bar_w * seconds / longest), self.ROW - 8), 3, 3)
            p.setPen(QColor(TEXT if seconds else MUTED))
            p.drawText(QRectF(label_w + bar_w + 8, y, value_w, self.ROW - 4), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{hms(seconds)}  ·  {seconds / total * 100:.0f} %")
        p.end()


class SummaryPanel(QWidget):
    """Le bilan d'une sortie."""

    def __init__(self, ride: RideSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ride = ride
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        head = QLabel(ride.title or "Sortie")
        head.setObjectName("title")
        layout.addWidget(head)
        when = QLabel(f"{ride_date(ride.start)} · FTP {ride.ftp:.0f} W" if ride.start else f"FTP {ride.ftp:.0f} W")
        when.setObjectName("metricSub")
        layout.addWidget(when)

        grid = QGridLayout()
        grid.setSpacing(6)
        hr = number(ride.avg_hr_bpm, "bpm")
        tiles = [
            ("DURÉE", hms(ride.duration_s), number(ride.distance_km, "km", 1) if ride.distance_km else ""),
            ("PUISSANCE MOY.", number(ride.avg_power_w, "W"), f"max {number(ride.max_power_w, 'W')}"),
            ("PUISSANCE NORM.", number(ride.normalized_power_w, "W"), f"IF {number(ride.intensity, digits=2)}"),
            ("TSS", number(ride.tss), f"{number(ride.work_kj, 'kJ')}"),
            ("FC MOY.", hr, f"max {number(ride.max_hr_bpm, 'bpm')}" if ride.max_hr_bpm else "pas de cardio"),
            ("CADENCE MOY.", number(ride.avg_cadence_rpm, "rpm"), ""),
        ]
        self.tiles: dict[str, Metric] = {}
        for i, (title, value, sub) in enumerate(tiles):
            tile = Metric(title, 18, HEART if title.startswith("FC") else POWER if "PUISSANCE" in title else TEXT)
            tile.set(value, sub)
            grid.addWidget(tile, i // 3, i % 3)
            self.tiles[title] = tile
        layout.addLayout(grid)

        zones_title = QLabel("TEMPS PAR ZONE")
        zones_title.setObjectName("metricTitle")
        layout.addWidget(zones_title)
        layout.addWidget(ZoneBars(ride.zone_s))

        best_title = QLabel("MEILLEURES PUISSANCES")
        best_title.setObjectName("metricTitle")
        layout.addWidget(best_title)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.best: dict[int, Metric] = {}
        for seconds in HIGHLIGHT_S:
            watts = ride.best_w.get(seconds)
            if watts is None:
                continue
            record = seconds in ride.records
            tile = Metric(duration_label(seconds).upper(), 16, ACCENT if record else TEXT)
            if record:
                previous = ride.records[seconds]
                tile.set(number(watts, "W"), "★ record" + (f"  +{watts - previous:.0f} W" if previous else ""))
                if previous:
                    tile.setToolTip(f"Ancien record : {previous:.0f} W")
                tile.sub.setStyleSheet(f"color: {ACCENT};")
            else:
                tile.set(number(watts, "W"), f"{watts / ride.ftp * 100:.0f} % FTP" if ride.ftp else "")
            row.addWidget(tile)
            self.best[seconds] = tile
        if not self.best:
            none = QLabel("Sortie trop courte.")
            none.setObjectName("metricSub")
            row.addWidget(none)
        row.addStretch(1)
        layout.addLayout(row)


class SummaryDialog(QDialog):
    def __init__(self, ride: RideSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bilan de la sortie")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.addWidget(SummaryPanel(ride))
        if ride.file:
            where = QLabel(f"Fichier : {ride.file}")
            where.setObjectName("metricSub")
            where.setWordWrap(True)
            where.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(where)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
