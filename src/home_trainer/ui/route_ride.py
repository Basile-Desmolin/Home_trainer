"""Parcours GPX : la page affichée quand on roule une trace.

Profil d'altitude complet (coloré selon la pente, position en cours), zoom
sur les 2 prochains kilomètres, petite carte de la trace, et les mesures :
pente, distance et dénivelé restants, vitesse. `RouteSession` porte la
logique (testée sans Qt) ; la fenêtre principale relaie mesures et boutons.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .chart import ACCENT, CADENCE, DEFAULT_FTP, HEART, MUTED, TEXT, hms, paint_panel
from .free_ride import grade_color, grade_text
from .gauges import ZoneBar
from .metric import Metric
from .power import Reading
from .session import DIFFICULTY_STEP, RouteSession, State
from .theme import icon, keycaps, number_font, zone_index

AHEAD_M = 2000.0  # le zoom montre les 2 km à venir
BEHIND_M = 200.0


DESCENT = "#3d8bd9"


def route_grade_color(grade_pct: float) -> QColor:
    """Comme le mode libre (vert → rouge en montée), avec les descentes en bleu."""
    return QColor(DESCENT) if grade_pct <= -1 else grade_color(grade_pct)


def km_text(meters: float, decimals: int = 1) -> str:
    return f"{meters / 1000:.{decimals}f} km".replace(".", ",")


class RouteProfile(QWidget):
    """Profil d'altitude entre deux distances, coloré par pente, avec la position du cycliste.

    `ahead_m` None : tout le parcours ; sinon une fenêtre glissante autour de la position.
    """

    def __init__(self, ahead_m: float | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session: RouteSession | None = None
        self.ahead_m = ahead_m
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_session(self, session: RouteSession | None) -> None:
        self.session = session
        self.update()

    def _span(self, s: RouteSession) -> tuple[float, float]:
        total = s.route.total_m
        if self.ahead_m is None or total <= self.ahead_m + BEHIND_M:
            return 0.0, total
        start = max(0.0, min(s.distance_m - BEHIND_M, total - self.ahead_m - BEHIND_M))
        return start, start + self.ahead_m + BEHIND_M

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        paint_panel(p, self)
        s = self.session
        if s is None:
            return
        route = s.route
        d0, d1 = self._span(s)
        margin_l, margin_r, margin_t, margin_b = 48, 12, 18, 24
        area = QRectF(margin_l, margin_t, self.width() - margin_l - margin_r,
                      self.height() - margin_t - margin_b)
        n = max(2, int(area.width() / 3))
        ds = [d0 + (d1 - d0) * i / n for i in range(n + 1)]
        eles = [route.elevation_at(d) for d in ds]
        lo, hi = min(eles), max(eles)
        # Au moins 50 m de haut (100 m sur le parcours entier) : un faux plat ne doit pas ressembler à un mur.
        room = max(hi - lo, 50.0 if self.ahead_m else 100.0)
        lo -= (room - (hi - lo)) * 0.3 + room * 0.05
        hi = lo + room * 1.15

        def x(d: float) -> float:
            return area.left() + (d - d0) / (d1 - d0) * area.width()

        def y(ele: float) -> float:
            return area.bottom() - (ele - lo) / (hi - lo) * area.height()

        # Terrain coloré par pente : une surface par suite de bandes de même couleur
        # (des bandes séparées laisseraient voir leurs joints).
        runs: list[tuple[int, int, QColor]] = []  # (premier, dernier échantillon, couleur)
        for i in range(n):
            a, b = ds[i], ds[i + 1]
            color = route_grade_color(route.grade_at((a + b) / 2))
            color.setAlpha(90 if b <= s.distance_m else 200)  # le chemin fait est estompé
            if runs and runs[-1][2] == color:
                runs[-1] = (runs[-1][0], i + 1, color)
            else:
                runs.append((i, i + 1, color))
        p.setPen(Qt.NoPen)
        for first, last, color in runs:
            top = [QPointF(x(ds[i]), y(eles[i])) for i in range(first, last + 1)]
            p.setBrush(color)
            p.drawPolygon(QPolygonF([QPointF(x(ds[first]), area.bottom()), *top,
                                     QPointF(x(ds[last]), area.bottom())]))
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath(QPointF(x(ds[0]), y(eles[0])))
        for d, e in zip(ds[1:], eles[1:]):
            path.lineTo(x(d), y(e))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(TEXT), 1.4))
        p.drawPath(path)

        # Graduations : altitude à gauche, kilomètres en bas.
        p.setPen(QColor(MUTED))
        for ele in (lo + (hi - lo) * 0.1, lo + (hi - lo) * 0.9):
            p.drawText(QRectF(0, y(ele) - 8, margin_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{ele:.0f} m")
        span_km = (d1 - d0) / 1000
        step = next(k for k in (0.25, 0.5, 1, 2, 5, 10, 20, 50, 100) if span_km / k <= 10) * 1000
        d = (int(d0 // step) + (d0 % step > 0)) * step
        while d <= d1 + 1e-6:
            text = km_text(d, 0 if step >= 1000 else 2 if step < 500 else 1).replace(",00", "")
            p.drawText(QRectF(x(d) - 30, area.bottom() + 4, 60, 16), Qt.AlignHCenter, text)
            d += step
        if self.ahead_m:
            p.drawText(QRectF(area.left() + 4, 2, 200, 14), Qt.AlignLeft, "À venir")

        # Cycliste : trait vertical et point sur la route.
        here = s.distance_m
        p.setPen(QPen(QColor(ACCENT), 2))
        p.drawLine(QPointF(x(here), area.top()), QPointF(x(here), area.bottom()))
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(QPointF(x(here), y(route.elevation_at(here))), 5, 5)


class RouteMap(QWidget):
    """La trace vue du dessus, avec le chemin fait et la position."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session: RouteSession | None = None
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    def set_session(self, session: RouteSession | None) -> None:
        self.session = session
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        paint_panel(p, self)
        s = self.session
        if s is None:
            return
        points = s.route.points
        k = math.cos(math.radians(sum(pt.lat for pt in points) / len(points)))  # longitudes resserrées
        xs = [pt.lon * k for pt in points]
        ys = [pt.lat for pt in points]
        w, h = (max(xs) - min(xs)) or 1e-9, (max(ys) - min(ys)) or 1e-9
        margin = 12
        scale = min((self.width() - 2 * margin) / w, (self.height() - 2 * margin) / h)
        ox = (self.width() - w * scale) / 2
        oy = (self.height() - h * scale) / 2

        def pos(lat: float, lon: float) -> QPointF:
            return QPointF(ox + (lon * k - min(xs)) * scale, self.height() - oy - (lat - min(ys)) * scale)

        def trace(pts) -> QPainterPath:
            path = QPainterPath(pos(pts[0].lat, pts[0].lon))
            for pt in pts[1:]:
                path.lineTo(pos(pt.lat, pt.lon))
            return path

        p.setPen(QPen(QColor(MUTED), 2))
        p.drawPath(trace(points))
        done = [pt for pt in points if pt.distance_m <= s.distance_m]
        if len(done) > 1:
            p.setPen(QPen(QColor(ACCENT), 3))
            p.drawPath(trace(done))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3fb37f"))
        p.drawEllipse(pos(points[0].lat, points[0].lon), 4, 4)
        p.setBrush(QColor("#e5484d"))
        p.drawEllipse(pos(points[-1].lat, points[-1].lon), 4, 4)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(pos(*s.route.position_at(s.distance_m)), 6, 6)


class RoutePanel(QWidget):
    """Page du parcours GPX : pente en cours, mesures, profils et carte."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(10)

        self.title = QLabel()
        self.title.setObjectName("title")
        layout.addWidget(self.title)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.m_power = Metric("PUISSANCE", 44)
        self.power_zones = ZoneBar()
        self.m_power.add(self.power_zones)
        self.m_heart = Metric("CARDIO", 44, HEART)
        self.m_remaining = Metric("RESTE", 44)
        self.m_cadence = Metric("CADENCE", 26, CADENCE)
        self.m_speed = Metric("VITESSE", 26)
        self.m_time = Metric("TEMPS", 26)
        grid.addWidget(self.m_power, 0, 0)
        grid.addWidget(self._build_grade(), 0, 1, 2, 1)
        grid.addWidget(self.m_heart, 0, 2)
        grid.addWidget(self.m_remaining, 0, 3)
        grid.addWidget(self.m_cadence, 1, 0)
        grid.addWidget(self.m_speed, 1, 2)
        grid.addWidget(self.m_time, 1, 3)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        layout.addLayout(grid)

        self.status_label = QLabel()
        self.status_label.setObjectName("current")
        layout.addWidget(self.status_label)

        middle = QHBoxLayout()
        middle.setSpacing(10)
        self.ahead = RouteProfile(AHEAD_M)
        self.map = RouteMap()
        middle.addWidget(self.ahead, 3)
        middle.addWidget(self.map, 1)
        layout.addLayout(middle, 2)
        self.profile = RouteProfile()
        layout.addWidget(self.profile, 2)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.play_button = QPushButton("Démarrer")
        self.play_button.setObjectName("play")
        self.reset_button = QPushButton("Recommencer")
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
        hint = QLabel(keycaps("[Espace] pause   [↑] [↓] difficulté ±10 %"))
        buttons.addWidget(hint)
        layout.addLayout(buttons)

    def _build_grade(self) -> QWidget:
        box = QFrame()
        box.setObjectName("metric")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(4)
        title = QLabel("PENTE")
        title.setObjectName("metricTitle")
        layout.addWidget(title)
        self.grade_label = QLabel("—")
        self.grade_label.setFont(number_font(72))
        self.grade_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.grade_label, 1)
        self.grade_sub = QLabel("")
        self.grade_sub.setObjectName("metricSub")
        self.grade_sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.grade_sub)
        row = QHBoxLayout()
        self.difficulty_buttons: list[tuple[QPushButton, int]] = []
        self.difficulty_label = QLabel()
        self.difficulty_label.setAlignment(Qt.AlignCenter)
        self.difficulty_label.setToolTip("Difficulté : part de la pente réelle envoyée au home trainer "
                                         "(la vitesse, elle, suit la vraie pente)")
        for delta in (-DIFFICULTY_STEP, +DIFFICULTY_STEP):
            b = QPushButton(f"{delta:+d} %".replace("-", "−"))
            b.setObjectName("adjust")
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(200)
            self.difficulty_buttons.append((b, delta))
        row.addWidget(self.difficulty_buttons[0][0])
        row.addWidget(self.difficulty_label, 1)
        row.addWidget(self.difficulty_buttons[1][0])
        layout.addLayout(row)
        return box

    def set_session(self, session: RouteSession) -> None:
        for chart in (self.ahead, self.profile, self.map):
            chart.set_session(session)
        r = session.route
        self.title.setText(f"{r.name}  ·  {km_text(r.total_m)}  ·  D+ {r.climb_m:.0f} m")

    def refresh(self, session: RouteSession, reading: Reading | None) -> None:
        ftp = session.ftp or DEFAULT_FTP
        r = reading
        average = session.average_power()
        sub = f"{r.power_w / ftp * 100:.0f} % FTP" if r else ""
        if average is not None:
            sub += f"{' · ' if sub else ''}moy. {average:.0f} W"
        self.m_power.set(f"{r.power_w:.0f} W" if r else "—", sub)
        self.power_zones.set_zone(zone_index(r.power_w if r else None, ftp))
        self.m_power.set_tint(route_grade_color(session.grade_pct) if session.state is State.RUNNING else None)
        self.m_cadence.set(f"{r.cadence_rpm:.0f}" if r and r.cadence_rpm is not None else "—", "tr/min")

        grade = session.grade_pct
        self.grade_label.setText(grade_text(grade))
        self.grade_label.setStyleSheet(f"color: {route_grade_color(grade).name()};")
        sent = session.trainer_grade_pct
        self.grade_sub.setText(f"altitude {session.elevation_m:.0f} m"
                               + ("" if sent == grade else f" · home trainer {grade_text(sent)}"))
        self.difficulty_label.setText(f"difficulté {session.difficulty_pct} %")
        self.difficulty_label.setStyleSheet(f"color: {TEXT if session.difficulty_pct == 100 else ACCENT};")

        self.m_remaining.set(km_text(session.remaining_m),
                             f"D+ restant {session.climb_remaining_m:.0f} m · fait {km_text(session.distance_m)}")
        avg = session.average_speed_kmh()
        self.m_speed.set(f"{session.speed_kmh:.1f}".replace(".", ","),
                         "km/h" + (f" · moy. {avg:.1f}".replace(".", ",") if avg else ""))
        self.m_time.set(hms(session.elapsed_s), f"D+ fait {session.climb_done_m:.0f} m")

        self.status_label.setText({
            State.READY: "Démarrez et pédalez : la pente de la route arrive dans le home trainer.",
            State.RUNNING: f"Pente simulée à {grade_text(session.trainer_grade_pct)}"
                           f" (cycliste {session.rider_kg:g} kg)".replace(".", ","),
            State.PAUSED: "En pause : résistance libre",
            State.FINISHED: f"Arrivée ! {km_text(session.route.total_m)} en {hms(session.elapsed_s)}",
        }.get(session.state, ""))
        self.play_button.setText({State.RUNNING: "Pause", State.PAUSED: "Reprendre",
                                  State.FINISHED: "Recommencer"}.get(session.state, "Démarrer"))
        self.play_button.setIcon(icon("pause" if session.state is State.RUNNING else "play", "#18191c"))
        for chart in (self.ahead, self.profile, self.map):
            chart.update()
