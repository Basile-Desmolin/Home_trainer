"""Mode libre : sans séance, la consigne ERG se règle à la main, en direct.

`FreeRidePanel` est la page affichée à la place de la séance ; la fenêtre
principale lui passe les mesures et relaie les boutons (`FreeRideSession`
porte la logique, testée sans Qt).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .chart import ACCENT, DEFAULT_FTP, HEART, MUTED, PANEL, POWER, TEXT, hms, zone_color
from .metric import Metric
from .power import Reading
from .session import FREE_STEP_W, FreeRideSession, State

BIG_STEP_W = 25
WINDOW_S = 600  # la courbe montre les 10 dernières minutes


class FreeRideChart(QWidget):
    """Courbe en direct des dernières minutes : consigne, puissance et cardio."""

    def __init__(self, parent: QWidget | None = None, window_s: float = WINDOW_S) -> None:
        super().__init__(parent)
        self.session: FreeRideSession | None = None
        self.window_s = window_s
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_session(self, session: FreeRideSession) -> None:
        self.session = session
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(PANEL))
        s = self.session
        if s is None:
            return
        ftp = s.ftp or DEFAULT_FTP
        margin_l, margin_r, margin_t, margin_b = 44, 12, 12, 26
        area = QRectF(margin_l, margin_t, self.width() - margin_l - margin_r,
                      self.height() - margin_t - margin_b)
        end = max(s.elapsed_s, self.window_s)
        start = end - self.window_s
        samples = [x for x in s.samples if x.t >= start]
        peak = max([x.power_w for x in samples] + [x.target_w or 0 for x in samples] + [s.target_w, ftp]) * 1.15

        def x(t: float) -> float:
            return area.left() + (t - start) / self.window_s * area.width()

        def y(w: float) -> float:
            return area.bottom() - w / peak * area.height()

        # Graduations : FTP et axe du temps (une marque par minute).
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
        p.drawLine(QPointF(area.left(), y(ftp)), QPointF(area.right(), y(ftp)))
        p.drawText(QRectF(0, y(ftp) - 8, margin_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, "FTP")
        p.setPen(QColor(MUTED))
        t = (int(start) // 60 + (start % 60 > 0)) * 60
        while t <= end:
            p.drawText(QRectF(x(t) - 30, area.bottom() + 4, 60, 18), Qt.AlignHCenter, hms(t))
            t += 120 if self.window_s > 360 else 60

        # Consigne : passée en escalier sur fond coloré par zone, actuelle en pointillés jusqu'au bord.
        now = s.elapsed_s
        steps: list[tuple[float, float]] = []  # (début, consigne) à chaque changement
        for sample in samples:
            if not steps or sample.target_w != steps[-1][1]:
                steps.append((sample.t, sample.target_w or 0))
        for i, (t0, w) in enumerate(steps):
            t1 = steps[i + 1][0] if i + 1 < len(steps) else now
            color = zone_color(w, ftp)
            color.setAlpha(110)
            p.fillRect(QRectF(QPointF(x(t0), y(w)), QPointF(x(t1), area.bottom())), color)
        p.setPen(QPen(QColor(ACCENT), 2, Qt.DashLine))
        p.drawLine(QPointF(x(now), y(s.target_w)), QPointF(area.right(), y(s.target_w)))

        # Puissance réalisée.
        if len(samples) > 1:
            path = QPainterPath(QPointF(x(samples[0].t), y(samples[0].power_w)))
            for sample in samples[1:]:
                path.lineTo(x(sample.t), y(sample.power_w))
            p.setPen(QPen(QColor(POWER), 1.6))
            p.drawPath(path)

        # Fréquence cardiaque, sur sa propre échelle (graduée à droite).
        heart = [(x_.t, x_.heart_rate_bpm) for x_ in samples if x_.heart_rate_bpm]
        if len(heart) > 1:
            lo = min(60, min(b for _, b in heart) - 5)
            hi = max(200, max(b for _, b in heart) + 5)

            def yh(bpm: float) -> float:
                return area.bottom() - (bpm - lo) / (hi - lo) * area.height()

            p.setPen(QColor(HEART))
            for bpm in range(int(lo // 20 + 1) * 20, int(hi), 40):
                p.drawText(QRectF(area.right() - 40, yh(bpm) - 8, 38, 16), Qt.AlignRight | Qt.AlignVCenter,
                           f"{bpm}")
            path = QPainterPath(QPointF(x(heart[0][0]), yh(heart[0][1])))
            for t_, bpm in heart[1:]:
                path.lineTo(x(t_), yh(bpm))
            p.setPen(QPen(QColor(HEART), 1.4))
            p.drawPath(path)

        # Instant présent.
        p.setPen(QPen(QColor(TEXT), 2))
        p.drawLine(QPointF(x(now), area.top()), QPointF(x(now), area.bottom()))


class FreeRidePanel(QWidget):
    """Page du mode libre : consigne réglable par pas de 5 W, mesures et courbe en direct."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Mode libre  ·  consigne ERG réglée à la main")
        title.setObjectName("title")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.m_power = Metric("PUISSANCE", 52, POWER)
        self.m_heart = Metric("CARDIO", 52, HEART)
        self.m_time = Metric("TEMPS", 30)
        self.m_cadence = Metric("CADENCE", 30)
        grid.addWidget(self.m_power, 0, 0)
        grid.addWidget(self._build_target(), 0, 1, 2, 2)
        grid.addWidget(self.m_heart, 0, 3)
        grid.addWidget(self.m_cadence, 1, 0)
        grid.addWidget(self.m_time, 1, 3)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        layout.addLayout(grid)

        self.status_label = QLabel()
        self.status_label.setObjectName("current")
        layout.addWidget(self.status_label)

        self.chart = FreeRideChart()
        layout.addWidget(self.chart, 1)

        buttons = QHBoxLayout()
        self.play_button = QPushButton("Démarrer")
        self.play_button.setObjectName("play")
        self.reset_button = QPushButton("Remettre à zéro")
        self.back_button = QPushButton("Retour à la séance")
        for b in (self.play_button, self.reset_button, self.back_button):
            b.setFocusPolicy(Qt.NoFocus)
            buttons.addWidget(b)
        buttons.addStretch(1)
        hint = QLabel("Espace : démarrer / pause   ↑ ↓ : ±5 W   Pg↑ Pg↓ : ±25 W")
        hint.setObjectName("metricSub")
        buttons.addWidget(hint)
        layout.addLayout(buttons)

    def _build_target(self) -> QWidget:
        box = QFrame()
        box.setObjectName("metric")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(4)
        title = QLabel("CIBLE ERG")
        title.setObjectName("metricTitle")
        layout.addWidget(title)
        self.target_label = QLabel("—")
        font = QFont()
        font.setPointSize(64)
        font.setBold(True)
        self.target_label.setFont(font)
        self.target_label.setAlignment(Qt.AlignCenter)
        self.target_label.setStyleSheet(f"color: {ACCENT};")
        layout.addWidget(self.target_label, 1)
        self.target_sub = QLabel("")
        self.target_sub.setObjectName("metricSub")
        self.target_sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.target_sub)
        row = QHBoxLayout()
        self.adjust_buttons: list[tuple[QPushButton, int]] = []
        for delta in (-BIG_STEP_W, -FREE_STEP_W, FREE_STEP_W, BIG_STEP_W):
            row.addWidget(self._adjust_button(delta))
        layout.addLayout(row)
        return box

    def _adjust_button(self, delta: int) -> QPushButton:
        b = QPushButton(f"{delta:+d}".replace("-", "−"))
        b.setObjectName("adjust")
        b.setFocusPolicy(Qt.NoFocus)
        b.setAutoRepeat(True)  # rester appuyé fait défiler les watts
        b.setAutoRepeatDelay(400)
        b.setAutoRepeatInterval(150)
        self.adjust_buttons.append((b, delta))
        return b

    def refresh(self, session: FreeRideSession, reading: Reading | None) -> None:
        ftp = session.ftp or DEFAULT_FTP
        r = reading
        average = session.average_power()
        sub = f"{r.power_w / ftp * 100:.0f} % FTP" if r else ""
        if average is not None:
            sub += f"{' · ' if sub else ''}moy. {average:.0f} W"
        self.m_power.set(f"{r.power_w:.0f} W" if r else "—", sub)
        self.target_label.setText(f"{session.target_w} W")
        self.target_sub.setText(f"{session.target_w / ftp * 100:.0f} % FTP")
        self.m_cadence.set(f"{r.cadence_rpm:.0f}" if r and r.cadence_rpm is not None else "—", "tr/min")
        self.m_time.set(hms(session.elapsed_s), "")
        self.status_label.setText({
            State.READY: "Réglez la cible puis démarrez : le home trainer passe en ERG.",
            State.RUNNING: f"ERG à {session.target_w} W",
            State.PAUSED: "En pause : résistance libre",
        }.get(session.state, ""))
        self.play_button.setText({State.RUNNING: "Pause", State.PAUSED: "Reprendre"}.get(session.state,
                                                                                         "Démarrer"))
        self.chart.update()
