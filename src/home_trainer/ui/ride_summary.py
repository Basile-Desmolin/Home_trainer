"""Bilan d'une sortie : chiffres clés (calories comprises), temps par zone de puissance et de FC
(avec la FC max du profil), meilleures puissances.

Montré en haut de la fenêtre « Enregistrer la sortie » à la fin de chaque sortie, et
depuis l'historique (`SummaryDialog`).
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..heart_zones import ESIE_NAMES
from ..ride_stats import HIGHLIGHT_S, ZONE_NAMES, RideSummary, duration_label
from .chart import ACCENT, HEART, MUTED, PANEL, POWER, TEXT, ZONES, hms
from .metric import Metric
from .theme import HEART_ZONES, ui_font

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


def heart_detail(ride: RideSummary) -> str:
    """Sous la FC moyenne : sa part de la FC max du profil, et celle de la FC max de la sortie."""
    if not ride.max_hr_bpm:
        return "pas de cardio"
    average, top = ride.hr_pct(ride.avg_hr_bpm), ride.hr_pct(ride.max_hr_bpm)
    if average is None or top is None:
        return "FC max du profil : non renseignée"
    return f"{average:.0f} % FC max · max {top:.0f} %"


POWER_LABELS = [f"Z{i + 1}  {name}" for i, name in enumerate(ZONE_NAMES)]
# Zones de FC ESIE, avec leurs bornes en % de la FC max.
HEART_LABELS = [f"{name}  {span}" for name, span in zip(
    ESIE_NAMES, ["< 75 %", "75-85 %", "85-92 %", "92-96 %", "> 96 %"])]


class ZoneBars(QWidget):
    """Temps passé dans chaque zone (de puissance par défaut, ou de FC), une barre par zone."""

    ROW = 20

    def __init__(self, zone_s: list[float], parent: QWidget | None = None, labels: list[str] | None = None,
                 colors: list[str] | None = None) -> None:
        super().__init__(parent)
        self.zone_s = zone_s
        self.labels = labels or POWER_LABELS
        self.colors = colors or [color for _limit, color in ZONES]
        self.setMinimumHeight(self.ROW * len(self.labels) + 4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(ui_font(9.5))
        total = sum(self.zone_s) or 1
        longest = max(self.zone_s) or 1
        label_w, value_w = 130, 110
        bar_w = max(10, self.width() - label_w - value_w - 8)
        for i, (seconds, label) in enumerate(zip(self.zone_s, self.labels)):
            y = i * self.ROW + 2
            color = QColor(self.colors[i])
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, y, label_w, self.ROW - 4), Qt.AlignLeft | Qt.AlignVCenter, label)
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
            ("FC MOY.", hr, heart_detail(ride)),
            ("FC MAX", number(ride.max_hr_bpm, "bpm"), ""),
            ("CADENCE MOY.", number(ride.avg_cadence_rpm, "rpm"), ""),
                # 1 kJ fourni ≈ 1 kcal dépensée.
            ("CALORIES", number(ride.calories_kcal, "kcal"), "1 kJ ≈ 1 kcal"),
        ]
        for c in range(4):
            grid.setColumnStretch(c, 1)
        self.tiles: dict[str, Metric] = {}
        for i, (title, value, sub) in enumerate(tiles):
            tile = Metric(title, 18, HEART if title.startswith("FC") else POWER if "PUISSANCE" in title else TEXT)
            tile.set(value, sub)
            grid.addWidget(tile, i // 4, i % 4)
            self.tiles[title] = tile
        layout.addLayout(grid)

        zones_title = QLabel("TEMPS PAR ZONE DE PUISSANCE")
        zones_title.setObjectName("metricTitle")
        layout.addWidget(zones_title)
        layout.addWidget(ZoneBars(ride.zone_s))
        self.heart_zones: ZoneBars | None = None
        if ride.hr_zone_s:  # avec la FC max du profil et un cardio
            heart_title = QLabel(f"TEMPS PAR ZONE DE FC  ·  FC MAX {ride.hr_max:.0f} BPM")
            heart_title.setObjectName("metricTitle")
            layout.addWidget(heart_title)
            self.heart_zones = ZoneBars(ride.hr_zone_s, labels=HEART_LABELS, colors=HEART_ZONES)
            layout.addWidget(self.heart_zones)

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
        self.setMinimumWidth(700)  # bilan sur 4 colonnes
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
