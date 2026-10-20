"""Mode libre : sans séance, la consigne se règle à la main, en direct.

Deux réglages : ERG (puissance imposée, par pas de 5 W) ou pente simulée
(par pas de 0,5 % ; le home trainer en déduit la résistance avec le poids).

`FreeRidePanel` est la page affichée à la place de la séance ; la fenêtre
principale lui passe les mesures et relaie les boutons (`FreeRideSession`
porte la logique, testée sans Qt).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from .chart import (ACCENT, CADENCE, DEFAULT_FTP, HEART, HEART_SMOOTH_S, MUTED, TEXT, draw_cadence, draw_cursor,
                    draw_power, hms, paint_panel, smooth, trace, zone_color)
from .gauges import ZoneBar
from .metric import Metric
from .power import Reading
from .session import FREE_STEP_W, GRADE_STEP_PCT, FreeMode, FreeRideSession, State
from .theme import icon, keycaps, number_font, zone_index

BIG_STEP_W = 25
BIG_STEP_PCT = 2.0
# Pente : du vert (plat) au rouge (mur).
GRADES = [(1, "#3fb37f"), (4, "#e7c43a"), (8, "#f08a2c"), (1_000, "#e5484d")]


def grade_text(grade_pct: float) -> str:
    """« 4,5 % », « −2 % » : à la française."""
    text = f"{grade_pct:.1f}".rstrip("0").rstrip(".").replace(".", ",").replace("-", "−")
    return f"{text} %"


def grade_color(grade_pct: float) -> QColor:
    return QColor(next(color for limit, color in GRADES if grade_pct < limit))
WINDOW_S = 600  # la courbe montre les 10 dernières minutes


class FreeRideChart(QWidget):
    """Courbe en direct des dernières minutes : consigne, puissance, cadence et cardio."""

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
        paint_panel(p, self)
        s = self.session
        if s is None:
            return
        ftp = s.ftp or DEFAULT_FTP
        margin_l, margin_r, margin_t, margin_b = 44, 36, 14, 26  # à droite : échelle de la cadence
        area = QRectF(margin_l, margin_t, self.width() - margin_l - margin_r,
                      self.height() - margin_t - margin_b)
        end = max(s.elapsed_s, self.window_s)
        start = end - self.window_s
        samples = [x for x in s.samples if x.t >= start]
        erg = s.mode is FreeMode.ERG
        peak = max([x.power_w for x in samples] + [x.target_w or 0 for x in samples]
                   + [s.target_w if erg else 0, ftp]) * 1.15
        # La pente a sa propre échelle, sur les 60 % du bas : le terrain sous la courbe de puissance.
        grades = [x.grade_pct for x in samples if x.grade_pct is not None] + ([] if erg else [s.grade_pct])
        g_lo, g_hi = min(grades + [0.0]) - 2, max(grades + [10.0])

        def x(t: float) -> float:
            return area.left() + (t - start) / self.window_s * area.width()

        def y(w: float) -> float:
            return area.bottom() - w / peak * area.height()

        def yg(grade: float) -> float:
            return area.bottom() - (grade - g_lo) / (g_hi - g_lo) * area.height() * 0.6

        # Graduations : FTP et axe du temps (une marque par minute).
        p.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
        p.drawLine(QPointF(area.left(), y(ftp)), QPointF(area.right(), y(ftp)))
        p.drawText(QRectF(0, y(ftp) - 8, margin_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, "FTP")
        p.setPen(QColor(MUTED))
        t = (int(start) // 60 + (start % 60 > 0)) * 60
        while t <= end:
            p.drawText(QRectF(x(t) - 30, area.bottom() + 4, 60, 18), Qt.AlignHCenter, hms(t))
            t += 120 if self.window_s > 360 else 60

        # Consigne : passée en escalier sur fond coloré (zone en ERG, raideur en pente),
        # actuelle en pointillés jusqu'au bord.
        now = s.elapsed_s
        steps: list[tuple[float, float | None, float | None]] = []  # (début, watts, pente) à chaque changement
        for sample in samples:
            key = (sample.target_w, sample.grade_pct)
            if not steps or key != steps[-1][1:]:
                steps.append((sample.t, *key))
        for i, (t0, w, grade) in enumerate(steps):
            t1 = steps[i + 1][0] if i + 1 < len(steps) else now
            if grade is not None:
                color, top = grade_color(grade), yg(grade)
            else:
                color, top = zone_color(w or 0, ftp), y(w or 0)
            color.setAlpha(110)
            p.fillRect(QRectF(QPointF(x(t0), top), QPointF(x(t1), area.bottom())), color)
        p.setPen(QPen(QColor(ACCENT), 2, Qt.DashLine))
        level = y(s.target_w) if erg else yg(s.grade_pct)
        p.drawLine(QPointF(x(now), level), QPointF(area.right(), level))
        if not erg:
            p.setPen(QColor(ACCENT))
            p.drawText(QRectF(area.right() - 64, level - 18, 60, 16), Qt.AlignRight | Qt.AlignVCenter,
                       grade_text(s.grade_pct))

        # Puissance réalisée.
        draw_power(p, samples, x, y)

        # Cadence, sur sa propre échelle (graduée dans la marge de droite).
        draw_cadence(p, area, samples, x, area.right() + 6)

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
            p.setPen(QPen(QColor(HEART), 1.4))
            p.drawPath(trace(smooth(heart, HEART_SMOOTH_S), x, yh))

        # Instant présent.
        last = samples[-1] if samples else None
        draw_cursor(p, x(now), area.top(), area.bottom(),
                    y(last.power_w) if last is not None and abs(x(last.t) - x(now)) < 6 else None)


class FreeRidePanel(QWidget):
    """Page du mode libre : consigne réglable par pas de 5 W, mesures et courbe en direct."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Mode libre  ·  consigne réglée à la main")
        title.setObjectName("title")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.m_power = Metric("PUISSANCE", 52)
        self.power_zones = ZoneBar()
        self.m_power.add(self.power_zones)
        self.m_heart = Metric("CARDIO", 40, HEART)
        self.m_time = Metric("TEMPS", 30)
        self.m_cadence = Metric("CADENCE", 30, CADENCE)
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
        buttons.setSpacing(8)
        self.play_button = QPushButton("Démarrer")
        self.play_button.setObjectName("play")
        self.reset_button = QPushButton("Remettre à zéro")
        self.reset_button.setIcon(icon("restart"))
        self.finish_button = QPushButton("Terminer")
        self.finish_button.setIcon(icon("flag"))
        self.back_button = QPushButton("Retour à la séance")
        self.back_button.setIcon(icon("back"))
        for b in (self.play_button, self.reset_button, self.finish_button, self.back_button):
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(b)
        buttons.addStretch(1)
        self.hint = QLabel()
        buttons.addWidget(self.hint)
        layout.addLayout(buttons)
        self._set_mode_labels(FreeMode.ERG)

    def _build_target(self) -> QWidget:
        box = QFrame()
        box.setObjectName("metric")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(4)
        top = QHBoxLayout()
        self.target_title = QLabel("CIBLE ERG")
        self.target_title.setObjectName("metricTitle")
        top.addWidget(self.target_title, 1)
        self.mode_buttons: dict[FreeMode, QPushButton] = {}
        group = QButtonGroup(self)
        for mode, text, tip in ((FreeMode.ERG, "ERG", "Puissance imposée, quelle que soit la vitesse"),
                                (FreeMode.SLOPE, "Pente", "Pente simulée : la résistance dépend de la pente, "
                                                          "du poids et de la vitesse, comme sur la route")):
            b = QPushButton(text)
            b.setObjectName("mode")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            group.addButton(b)
            top.addWidget(b)
            self.mode_buttons[mode] = b
        self.mode_buttons[FreeMode.ERG].setChecked(True)
        layout.addLayout(top)
        self.target_label = QLabel("—")
        self.target_label.setFont(number_font(84))
        self.target_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.target_label, 1)
        self.target_sub = QLabel("")
        self.target_sub.setObjectName("metricSub")
        self.target_sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.target_sub)
        row = QHBoxLayout()
        # (bouton, sens, grand pas ?) : le libellé suit le réglage (watts ou pente).
        self.adjust_buttons: list[tuple[QPushButton, int, bool]] = []
        for direction, big in ((-1, True), (-1, False), (+1, False), (+1, True)):
            row.addWidget(self._adjust_button(direction, big))
        layout.addLayout(row)
        return box

    def _adjust_button(self, direction: int, big: bool) -> QPushButton:
        b = QPushButton()
        b.setObjectName("adjust")
        b.setFocusPolicy(Qt.NoFocus)
        b.setAutoRepeat(True)  # rester appuyé fait défiler les valeurs
        b.setAutoRepeatDelay(400)
        b.setAutoRepeatInterval(150)
        self.adjust_buttons.append((b, direction, big))
        return b

    def _set_mode_labels(self, mode: FreeMode) -> None:
        erg = mode is FreeMode.ERG
        self.mode_buttons[mode].setChecked(True)
        self.target_title.setText("CIBLE ERG" if erg else "PENTE SIMULÉE")
        for b, direction, big in self.adjust_buttons:
            if erg:
                text = f"{direction * (BIG_STEP_W if big else FREE_STEP_W):+d}"
            else:
                step = BIG_STEP_PCT if big else GRADE_STEP_PCT
                text = ("+" if direction > 0 else "−") + grade_text(step).replace(" ", "\u202f")
            b.setText(text.replace("-", "−"))
        self.hint.setText(keycaps("[Espace] pause   [↑] [↓] ±5 W   [Pg↑] [Pg↓] ±25 W" if erg
                                  else "[Espace] pause   [↑] [↓] ±0,5 %   [Pg↑] [Pg↓] ±2 %"))

    def refresh(self, session: FreeRideSession, reading: Reading | None) -> None:
        ftp = session.ftp or DEFAULT_FTP
        r = reading
        average = session.average_power()
        sub = f"{r.power_w / ftp * 100:.0f} % FTP" if r else ""
        if average is not None:
            sub += f"{' · ' if sub else ''}moy. {average:.0f} W"
        self.m_power.set(f"{r.power_w:.0f} W" if r else "—", sub)
        self.power_zones.set_zone(zone_index(r.power_w if r else None, ftp))
        erg = session.mode is FreeMode.ERG
        # En ERG, la carte prend la couleur de la zone de la consigne ; en pente, celle de la pente.
        color = zone_color(session.target_w, ftp) if erg else grade_color(session.grade_pct)
        self.m_power.set_tint(color if session.state is State.RUNNING else None)
        dim = erg and zone_index(session.target_w, ftp) == 0  # récup (grise) : chiffres en blanc
        self.target_label.setStyleSheet(f"color: {TEXT if dim else color.name()};")
        self._set_mode_labels(session.mode)
        if erg:
            self.target_label.setText(f"{session.target_w} W")
            self.target_sub.setText(f"{session.target_w / ftp * 100:.0f} % FTP")
        else:
            self.target_label.setText(grade_text(session.grade_pct))
            self.target_sub.setText(f"cycliste {session.rider_kg:g} kg".replace(".", ","))
        speed = getattr(r, "speed_kmh", None)
        sub = "tr/min" if speed is None else f"tr/min · {speed:.1f} km/h".replace(".", ",")
        self.m_cadence.set(f"{r.cadence_rpm:.0f}" if r and r.cadence_rpm is not None else "—", sub)
        self.m_time.set(hms(session.elapsed_s), "")
        running = f"ERG à {session.target_w} W" if erg else f"Pente simulée à {grade_text(session.grade_pct)}"
        self.status_label.setText({
            State.READY: "Réglez la cible puis démarrez : le home trainer passe "
                         + ("en ERG." if erg else "en simulation de pente."),
            State.RUNNING: running,
            State.PAUSED: "En pause : résistance libre",
        }.get(session.state, ""))
        self.play_button.setText({State.RUNNING: "Pause", State.PAUSED: "Reprendre"}.get(session.state,
                                                                                         "Démarrer"))
        self.play_button.setIcon(icon("pause" if session.state is State.RUNNING else "play", "#18191c"))
        self.chart.update()
