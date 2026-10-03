"""Une grande valeur avec son libellé, partagée par la séance et le mode libre."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from .chart import TEXT


class Metric(QFrame):
    """Une grande valeur avec son libellé."""

    def __init__(self, title: str, size: int = 34, color: str = TEXT) -> None:
        super().__init__()
        self.setObjectName("metric")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(0)
        self.title = QLabel(title)
        self.title.setObjectName("metricTitle")
        self.value = QLabel("—")
        font = QFont()
        font.setPointSize(size)
        font.setBold(True)
        self.value.setFont(font)
        self.value.setStyleSheet(f"color: {color};")
        self.sub = QLabel("")
        self.sub.setObjectName("metricSub")
        for w in (self.title, self.value, self.sub):
            layout.addWidget(w)

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)
