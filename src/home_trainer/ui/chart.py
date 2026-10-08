"""Profil d'une séance (briques colorées par zones), partagé par la séance et l'éditeur."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .session import WorkoutSession
from .theme import (ACCENT, BG, CADENCE, HEART, MUTED, PANEL, POWER, TEXT, ZONES,  # noqa: F401 (réexportés)
                    ui_font)

DEFAULT_FTP = 250
RADIUS = 12  # coins des cartes et des graphiques


def zone_color(watts: float | None, ftp: float) -> QColor:
    if watts is None:
        return QColor("#4a505c")
    pct = watts / ftp * 100
    return QColor(next(color for limit, color in ZONES if pct < limit))


def draw_cadence(p: QPainter, area: QRectF, samples, x, labels_left: float) -> None:
    """Cadence réalisée sur sa propre échelle (0 à 120 tr/min au moins), graduée dans la marge
    de droite à partir de `labels_left`. Un trou dans les mesures coupe la courbe."""
    points = [(s.t, s.cadence_rpm) for s in samples]
    values = [c for _, c in points if c is not None]
    if len(values) < 2:
        return
    hi = max(120, (int(max(values)) // 20 + 1) * 20)

    def yc(rpm: float) -> float:
        return area.bottom() - rpm / hi * area.height()

    p.setPen(QColor(CADENCE))
    width = p.device().width() - labels_left - 2
    for rpm in range(30, hi, 30):
        p.drawText(QRectF(labels_left, yc(rpm) - 8, width, 16), Qt.AlignLeft | Qt.AlignVCenter, f"{rpm}")
    p.drawText(QRectF(labels_left, area.top() - 12, width, 14), Qt.AlignLeft | Qt.AlignVCenter, "rpm")
    path = QPainterPath()
    drawing = False
    for t, rpm in points:
        if rpm is None:
            drawing = False
        elif drawing:
            path.lineTo(x(t), yc(rpm))
        else:
            path.moveTo(x(t), yc(rpm))
            drawing = True
    p.setPen(QPen(QColor(CADENCE), 1.2))
    p.drawPath(path)


def paint_panel(p: QPainter, widget: QWidget) -> None:
    """Fond d'un graphique : carte aux coins arrondis, comme les mesures."""
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(widget.rect(), QColor(BG))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(PANEL))
    p.drawRoundedRect(QRectF(widget.rect()), RADIUS, RADIUS)
    p.setBrush(Qt.NoBrush)
    p.setFont(ui_font(8.5))


def block_path(x0: float, x1: float, top0: float, top1: float, bottom: float, radius: float = 4) -> QPainterPath:
    """Brique : rectangle (ou trapèze pour une rampe) aux coins du haut arrondis."""
    r = max(0.0, min(radius, (x1 - x0) / 2, (bottom - max(top0, top1)) / 2))
    path = QPainterPath(QPointF(x0, bottom))
    if r == 0:
        path.lineTo(x0, top0)
        path.lineTo(x1, top1)
    else:
        slope = (top1 - top0) / (x1 - x0)
        path.lineTo(x0, top0 + r)
        path.quadTo(x0, top0, x0 + r, top0 + slope * r)
        path.lineTo(x1 - r, top1 - slope * r)
        path.quadTo(x1, top1, x1, top1 + r)
    path.lineTo(x1, bottom)
    path.closeSubpath()
    return path


def draw_power(p: QPainter, points: list[tuple[float, float]]) -> None:
    """Puissance réalisée."""
    if len(points) < 2:
        return
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(*point)
    pen = QPen(QColor(POWER), 2)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPath(path)


def draw_cursor(p: QPainter, x: float, top: float, bottom: float, power_y: float | None) -> None:
    """Instant présent : trait blanc, et un point sur la dernière puissance mesurée."""
    p.setPen(QPen(QColor(TEXT), 2))
    p.drawLine(QPointF(x, top), QPointF(x, bottom))
    if power_y is not None:
        p.setPen(QPen(QColor(PANEL), 2))
        p.setBrush(QColor(POWER))
        p.drawEllipse(QPointF(x, power_y), 5, 5)
        p.setBrush(Qt.NoBrush)


def hms(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class WorkoutChart(QWidget):
    """Profil complet de la séance, avancement et puissance réalisée.

    En aperçu (`preview=True`, éditeur), ni curseur ni briques passées : seules
    les briques de `highlight` (objets `Step`) sont mises en avant.
    """

    def __init__(self, parent: QWidget | None = None, preview: bool = False) -> None:
        super().__init__(parent)
        self.session: WorkoutSession | None = None
        self.preview = preview
        self.highlight: list = []
        self.setMinimumHeight(160 if preview else 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_session(self, session: WorkoutSession) -> None:
        self.session = session
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        paint_panel(p, self)
        s = self.session
        if s is None or not s.segments:
            return
        ftp = s.ftp or DEFAULT_FTP
        # À droite, la marge porte l'échelle de la cadence (pas en aperçu : rien de réalisé).
        margin_l, margin_r, margin_t, margin_b = 44, 12 if self.preview else 36, 14, 26
        area = QRectF(margin_l, margin_t, self.width() - margin_l - margin_r,
                      self.height() - margin_t - margin_b)
        total = max(s.total_s, 1.0)
        runs = s.chart_runs  # sur l'axe de la séance, qui saute quand on passe une brique
        samples = [x_ for run in runs for x_ in run]
        peak = max([seg.high_w or 0 for seg in s.segments] + [seg.end_high_w or 0 for seg in s.segments]
                   + [x.power_w for x in samples] + [ftp])
        peak *= max(s.intensity_pct, 100) / 100 * 1.12

        def x(t: float) -> float:
            return area.left() + t / total * area.width()

        def y(w: float) -> float:
            return area.bottom() - w / peak * area.height()

        # Échelle de la fréquence cardiaque (cibles d'une séance en FC et FC mesurée).
        heart_runs = [[(x_.t, x_.heart_rate_bpm) for x_ in run if x_.heart_rate_bpm] for run in runs]
        heart = [point for run in heart_runs for point in run]
        bpms = [b for _, b in heart] + [seg.target_bpm(t) for seg in s.segments if seg.step.heart_rate
                                         for t in (0, seg.duration_s or 0)]
        lo = min([60] + [b - 5 for b in bpms])
        hi = max([200] + [b + 5 for b in bpms])

        def yh(bpm: float) -> float:
            return area.bottom() - (bpm - lo) / (hi - lo) * area.height()

        # Graduations : FTP et axe du temps.
        p.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
        p.drawLine(QPointF(area.left(), y(ftp)), QPointF(area.right(), y(ftp)))
        p.drawText(QRectF(0, y(ftp) - 8, margin_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, "FTP")
        step = next(v for v in (60, 300, 600, 900, 1800, 3600, 7200) if total / v <= 12)
        p.setPen(QColor(MUTED))
        t = 0
        while t <= total:
            p.drawText(QRectF(x(t) - 30, area.bottom() + 4, 60, 18), Qt.AlignHCenter, hms(t))
            t += step

        # Briques (rampes = trapèzes) aux coins arrondis : passées estompées, à venir un peu
        # adoucies, en cours pleine couleur et cerclée de blanc.
        position = s.position_s
        for i, seg in enumerate(s.segments):
            if not seg.duration_s:
                continue
            x0, x1 = x(seg.start_s), x(seg.start_s + seg.duration_s)
            gap = 1.0 if x1 - x0 > 4 else 0.0
            if seg.low_w is None and seg.step.heart_rate is not None:  # cible en FC, sans puissance
                top0, top1 = yh(seg.target_bpm(0)), yh(seg.target_bpm(seg.duration_s))
                color = QColor(HEART)
                color.setAlpha(150)
            else:
                w0 = seg.target_w(0) or 0
                w1 = seg.target_w(seg.duration_s) or 0
                top0, top1 = y(max(w0, peak * 0.03)), y(max(w1, peak * 0.03))
                color = zone_color(seg.target_w(seg.duration_s / 2), ftp)
            path = block_path(x0 + gap, x1 - gap, top0, top1, area.bottom())
            if self.preview:
                current = any(seg.step is step for step in self.highlight)
            else:
                current = i == s.index
                if i < s.index:
                    color.setAlpha(min(color.alpha(), 70))
                elif i > s.index:
                    color.setAlpha(min(color.alpha(), 200))
            p.fillPath(path, color)
            if current:
                p.setPen(QPen(QColor(TEXT), 2))
                p.drawPath(path)

        # Consigne ajustée pour la suite de la séance (si l'intensité n'est pas 100 %).
        if s.intensity_pct != 100:
            k = s.intensity_pct / 100
            p.setPen(QPen(QColor(ACCENT), 2, Qt.DashLine))
            for seg in s.segments[s.index:]:
                if not seg.duration_s or seg.low_w is None:
                    continue
                start = max(seg.start_s, position)
                if start >= seg.start_s + seg.duration_s:
                    continue
                a = seg.target_w(start - seg.start_s) * k
                b = seg.target_w(seg.duration_s) * k
                p.drawLine(QPointF(x(start), y(a)), QPointF(x(seg.start_s + seg.duration_s), y(b)))

        # Puissance réalisée.
        for run in runs:
            draw_power(p, [(x(x_.t), y(x_.power_w)) for x_ in run])

        # Cadence, sur sa propre échelle (graduée dans la marge de droite) ; coupée entre deux tronçons.
        cadence = [x_ for j, run in enumerate(runs)
                   for x_ in ([replace(run[0], cadence_rpm=None)] if j else []) + run]
        draw_cadence(p, area, cadence, x, area.right() + 6)

        # Fréquence cardiaque, sur sa propre échelle (graduée à droite).
        if len(heart) > 1 or any(seg.low_w is None and seg.step.heart_rate for seg in s.segments):
            p.setPen(QColor(HEART))
            for bpm in range(int(lo // 20 + 1) * 20, int(hi), 40):
                p.drawText(QRectF(area.right() - 40, yh(bpm) - 8, 38, 16), Qt.AlignRight | Qt.AlignVCenter,
                           f"{bpm}")
        if len(heart) > 1:
            path = QPainterPath()
            for run in heart_runs:
                for i, (t_, bpm) in enumerate(run):
                    if i:
                        path.lineTo(x(t_), yh(bpm))
                    else:
                        path.moveTo(x(t_), yh(bpm))
            p.setPen(QPen(QColor(HEART), 1.4))
            p.drawPath(path)

        # Curseur de position.
        if self.preview:
            return
        last = samples[-1] if samples else None
        draw_cursor(p, x(position), area.top(), area.bottom(),
                    y(last.power_w) if last is not None and abs(x(last.t) - x(position)) < 6 else None)
