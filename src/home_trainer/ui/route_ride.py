"""Parcours GPX : la page affichée quand on roule une trace.

Profil d'altitude complet (coloré selon la pente, position en cours), zoom
sur les 2 prochains kilomètres, petite carte de la trace, et les mesures :
pente, distance et dénivelé restants, vitesse. `RouteSession` porte la
logique (testée sans Qt) ; la fenêtre principale relaie mesures et boutons.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .chart import ACCENT, MUTED, TEXT, hms, paint_panel
from .free_ride import grade_color, grade_text
from .indicators import Mode, RideView, km_text
from .metric import MetricGrid, Slot
from .session import DIFFICULTY_STEP, RouteSession, State
from .theme import hint_label, icon, keycaps, number_font

AHEAD_M = 2000.0  # le zoom montre les 2 km à venir
BEHIND_M = 200.0
# Cartes du catalogue d'indicateurs ; la pente occupe la colonne 1 (lignes 0 et 1).
SLOTS = [Slot("power", 0, 0, 44), Slot("heart", 0, 2, 44), Slot("route_remaining", 0, 3, 44),
         Slot("cadence", 1, 0, 26), Slot("speed", 1, 2, 26), Slot("elapsed", 1, 3, 26)]


DESCENT = "#3d8bd9"


def route_grade_color(grade_pct: float) -> QColor:
    """Comme le mode libre (vert → rouge en montée), avec les descentes en bleu."""
    return QColor(DESCENT) if grade_pct <= -1 else grade_color(grade_pct)


@dataclass(frozen=True)
class _ProfileFrame:
    """Cadre d'un profil : zone de tracé, échantillons (distance, altitude) et échelle d'altitude."""
    area: QRectF
    d0: float
    d1: float
    ds: list[float]
    eles: list[float]
    lo: float
    hi: float

    def x(self, d: float) -> float:
        return self.area.left() + (d - self.d0) / (self.d1 - self.d0) * self.area.width()

    def y(self, ele: float) -> float:
        return self.area.bottom() - (ele - self.lo) / (self.hi - self.lo) * self.area.height()


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
        # Parcours entier : le dessin ne change pas pendant la sortie, sauf la limite du chemin
        # fait. Il est gardé en deux images (tout estompé, tout vif), faites une fois par taille.
        self._key: tuple | None = None
        self._frame: _ProfileFrame | None = None
        self._faded = QPixmap()
        self._bright = QPixmap()

    def set_session(self, session: RouteSession | None) -> None:
        self.session = session
        self._key = None
        self.update()

    def _span(self, s: RouteSession) -> tuple[float, float]:
        total = s.route.total_m
        if self.ahead_m is None or total <= self.ahead_m + BEHIND_M:
            return 0.0, total
        start = max(0.0, min(s.distance_m - BEHIND_M, total - self.ahead_m - BEHIND_M))
        return start, start + self.ahead_m + BEHIND_M

    def _make_frame(self, route, d0: float, d1: float) -> _ProfileFrame:
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
        return _ProfileFrame(area, d0, d1, ds, eles, lo, hi)

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        s = self.session
        if s is None:
            paint_panel(p, self)
            return
        if self.ahead_m is None:
            f = self._paint_cached(p, s)
        else:
            f = self._make_frame(s.route, *self._span(s))
            self._paint_static(p, s.route, f, s.distance_m)
        # Cycliste : trait vertical et point sur la route.
        here = s.distance_m
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(ACCENT), 2))
        p.drawLine(QPointF(f.x(here), f.area.top()), QPointF(f.x(here), f.area.bottom()))
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(QPointF(f.x(here), f.y(s.route.elevation_at(here))), 5, 5)

    def _paint_cached(self, p: QPainter, s: RouteSession) -> _ProfileFrame:
        """Parcours entier : l'image vive, recouverte à gauche de la limite du chemin fait par l'estompée."""
        dpr = self.devicePixelRatioF()
        key = (s.route, self.width(), self.height(), dpr)  # Route se compare par identité
        if self._key != key or self._frame is None:
            self._frame = self._make_frame(s.route, 0.0, s.route.total_m)
            self._faded = self._render(s.route, self._frame, math.inf, dpr)
            self._bright = self._render(s.route, self._frame, -math.inf, dpr)
            self._key = key
        f = self._frame
        p.drawPixmap(0, 0, self._bright)
        # Une bande est estompée quand sa fin est passée : la limite est le dernier échantillon dépassé.
        j = bisect.bisect_right(f.ds, s.distance_m) - 1
        if j > 0:
            p.save()
            p.setClipRect(QRectF(0, 0, f.x(f.ds[j]), self.height()))
            p.drawPixmap(0, 0, self._faded)
            p.restore()
        return f

    def _render(self, route, f: _ProfileFrame, done_m: float, dpr: float) -> QPixmap:
        pixmap = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self._paint_static(painter, route, f, done_m)
        painter.end()
        return pixmap

    def _paint_static(self, p: QPainter, route, f: _ProfileFrame, done_m: float) -> None:
        """Fond, terrain coloré (estompé jusqu'à `done_m`), ligne du profil et graduations."""
        paint_panel(p, self)
        area, ds, eles, lo, hi, d0, d1 = f.area, f.ds, f.eles, f.lo, f.hi, f.d0, f.d1
        x, y = f.x, f.y
        n = len(ds) - 1
        margin_l = area.left()

        # Terrain coloré par pente : une surface par suite de bandes de même couleur
        # (des bandes séparées laisseraient voir leurs joints).
        runs: list[tuple[int, int, QColor]] = []  # (premier, dernier échantillon, couleur)
        for i in range(n):
            a, b = ds[i], ds[i + 1]
            color = route_grade_color(route.grade_at((a + b) / 2))
            color.setAlpha(90 if b <= done_m else 200)  # le chemin fait est estompé
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


MAP_MARGIN = 12
MAP_MIN_STEP_PX = 1.0  # tracé simplifié : un point plus proche du précédent ne se verrait pas


class RouteMap(QWidget):
    """La trace vue du dessus, avec le chemin fait et la position.

    Le tracé est mis en pixels une seule fois par parcours et par taille de la carte
    (`_geometry`), puis simplifié au pixel : redessiner la carte 5 fois par seconde
    ne coûte plus que le chemin fait et le marqueur, quel que soit le nombre de points.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session: RouteSession | None = None
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._key: tuple | None = None  # (parcours, largeur, hauteur) du tracé en cache
        self._transform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)  # k, min x, min y, échelle, ox, oy
        self._trace_xy: list[QPointF] = []  # tracé simplifié, en pixels
        self._trace_d: list[float] = []  # distance (m) de chaque point gardé
        self._trace_path = QPainterPath()

    def set_session(self, session: RouteSession | None) -> None:
        self.session = session
        self._key = None
        self.update()

    def map_point(self, lat: float, lon: float) -> QPointF:
        """Pixel d'un point (lat, lon) ; `_geometry` doit avoir été appelée pour le parcours en cours."""
        k, min_x, min_y, scale, ox, oy = self._transform
        return QPointF(ox + (lon * k - min_x) * scale, self.height() - oy - (lat - min_y) * scale)

    def _geometry(self, route) -> None:
        key = (route, self.width(), self.height())  # Route se compare par identité
        if self._key == key:
            return
        points = route.points
        k = math.cos(math.radians(sum(pt.lat for pt in points) / len(points)))  # longitudes resserrées
        xs = [pt.lon * k for pt in points]
        ys = [pt.lat for pt in points]
        min_x, min_y = min(xs), min(ys)
        w, h = (max(xs) - min_x) or 1e-9, (max(ys) - min_y) or 1e-9
        scale = min((self.width() - 2 * MAP_MARGIN) / w, (self.height() - 2 * MAP_MARGIN) / h)
        ox = (self.width() - w * scale) / 2
        oy = (self.height() - h * scale) / 2
        self._transform = (k, min_x, min_y, scale, ox, oy)
        xy: list[QPointF] = []
        ds: list[float] = []
        last = len(points) - 1
        for i, pt in enumerate(points):
            q = self.map_point(pt.lat, pt.lon)
            if xy and i != last and abs(q.x() - xy[-1].x()) + abs(q.y() - xy[-1].y()) < MAP_MIN_STEP_PX:
                continue
            xy.append(q)
            ds.append(pt.distance_m)
        self._trace_xy, self._trace_d = xy, ds
        path = QPainterPath(xy[0])
        for q in xy[1:]:
            path.lineTo(q)
        self._trace_path = path
        self._key = key

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        paint_panel(p, self)
        s = self.session
        if s is None:
            return
        route = s.route
        self._geometry(route)
        p.setPen(QPen(QColor(MUTED), 2))
        p.drawPath(self._trace_path)
        here = self.map_point(*route.position_at(s.distance_m))
        if s.distance_m > 0:  # chemin fait : les points passés, puis la position exacte
            done = self._trace_xy[:bisect.bisect_right(self._trace_d, s.distance_m)]
            p.setPen(QPen(QColor(ACCENT), 3))
            p.drawPolyline(QPolygonF([*done, here]))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3fb37f"))
        p.drawEllipse(self._trace_xy[0], 4, 4)
        p.setBrush(QColor("#e5484d"))
        p.drawEllipse(self._trace_xy[-1], 4, 4)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(here, 6, 6)


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

        self.grid = grid = MetricGrid(SLOTS, Mode.ROUTE)
        self.cards = grid.cards
        grid.addWidget(self._build_grade(), 0, 1, 2, 1)
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
        hint = hint_label(keycaps("[Espace] pause   [↑] [↓] difficulté ±10 %   [Maj+↑] [Maj+↓] braquet   [Tab] tour"))
        buttons.addWidget(hint, 1)
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

    def refresh(self, view: RideView) -> None:
        """Cartes du catalogue, pente et difficulté."""
        session = view.session
        assert isinstance(session, RouteSession)
        self.grid.refresh(view)
        if "power" in self.cards:  # la place a pu recevoir un autre indicateur
            self.cards["power"].set_tint(route_grade_color(session.grade_pct) if session.state is State.RUNNING
                                         else None)

        grade = session.grade_pct
        self.grade_label.setText(grade_text(grade))
        self.grade_label.setStyleSheet(f"color: {route_grade_color(grade).name()};")
        sent = session.trainer_grade_pct
        self.grade_sub.setText(f"altitude {session.elevation_m:.0f} m"
                               + ("" if sent == grade else f" · home trainer {grade_text(sent)}"))
        self.difficulty_label.setText(f"difficulté {session.difficulty_pct} %")
        self.difficulty_label.setStyleSheet(f"color: {TEXT if session.difficulty_pct == 100 else ACCENT};")

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
