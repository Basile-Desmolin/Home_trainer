"""Petites jauges dessinées : barre des 7 zones de puissance et anneau du temps restant sur la brique."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import MUTED, RAISED, TEXT, ZONES, number_font


class ZoneBar(QWidget):
    """Les 7 zones côte à côte, celle de la puissance en cours allumée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.zone: int | None = None
        self.setObjectName("plain")
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_zone(self, zone: int | None) -> None:
        if zone != self.zone:
            self.zone = zone
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        n, gap = len(ZONES), 3.0
        w = (self.width() - gap * (n - 1)) / n
        for i, (_, color) in enumerate(ZONES):
            c = QColor(color)
            on = i == self.zone
            c.setAlpha(255 if on else 70)
            p.setBrush(c)
            h = self.height() if on else self.height() * 0.6
            p.drawRoundedRect(QRectF(i * (w + gap), (self.height() - h) / 2, w, h), 2, 2)


class BrickRing(QWidget):
    """Anneau qui se vide avec le temps restant sur la brique, le temps écrit au centre."""

    def __init__(self, diameter: int = 112, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("plain")
        self.fraction = 0.0  # part de la brique qui reste (1 : elle commence)
        self.text = "—"
        self.caption = ""
        self.color = QColor(TEXT)
        self.setFixedSize(QSize(diameter, diameter))

    def set(self, fraction: float | None, text: str, caption: str = "", color: str | QColor = TEXT) -> None:
        self.fraction = 1.0 if fraction is None else max(0.0, min(1.0, fraction))
        self.text, self.caption, self.color = text, caption, QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = self.width()
        thick = max(6.0, d * 0.075)
        rect = QRectF(thick / 2 + 1, thick / 2 + 1, d - thick - 2, d - thick - 2)
        pen = QPen(QColor(RAISED), thick)
        p.setPen(pen)
        p.drawEllipse(rect)
        if self.fraction > 0:
            pen = QPen(self.color, thick)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, round(self.fraction * 360 * 16))
        p.setPen(QColor(TEXT))
        size = round(d * 0.2) if len(self.text) <= 5 else round(d * 0.16)
        p.setFont(number_font(size))
        text_rect = QRectF(0, 0, d, d * (0.9 if self.caption else 1))
        p.drawText(text_rect, Qt.AlignCenter, self.text)
        if self.caption:
            p.setPen(QColor(MUTED))
            p.setFont(number_font(max(8, round(d * 0.085))))
            p.drawText(QRectF(0, d * 0.58, d, d * 0.16), Qt.AlignCenter, self.caption)
