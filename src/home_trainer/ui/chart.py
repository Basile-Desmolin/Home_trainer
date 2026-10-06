"""Profil d'une séance (briques colorées par zones), partagé par la séance et l'éditeur."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .session import WorkoutSession

DEFAULT_FTP = 250

BG = "#16181d"
PANEL = "#20242c"
TEXT = "#e8eaed"
MUTED = "#8b919c"
ACCENT = "#ffd24a"
POWER = "#4fc3f7"
HEART = "#ff5c6c"
CADENCE = "#b6e36b"

# Zones de Coggan (borne haute en % FTP) et couleurs associées.
ZONES = [(55, "#7f8c9a"), (76, "#3d8bd9"), (91, "#3fb37f"), (106, "#e7c43a"),
         (121, "#f08a2c"), (151, "#e5484d"), (10_000, "#b84ae0")]


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
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(PANEL))
        s = self.session
        if s is None or not s.segments:
            return
        ftp = s.ftp or DEFAULT_FTP
        # À droite, la marge porte l'échelle de la cadence (pas en aperçu : rien de réalisé).
        margin_l, margin_r, margin_t, margin_b = 44, 12 if self.preview else 36, 14, 26
        area = QRectF(margin_l, margin_t, self.width() - margin_l - margin_r,
                      self.height() - margin_t - margin_b)
        total = max(s.total_s, 1.0)
        peak = max([seg.high_w or 0 for seg in s.segments] + [seg.end_high_w or 0 for seg in s.segments]
                   + [x.power_w for x in s.samples] + [ftp])
        peak *= max(s.intensity_pct, 100) / 100 * 1.12

        def x(t: float) -> float:
            return area.left() + t / total * area.width()

        def y(w: float) -> float:
            return area.bottom() - w / peak * area.height()

        # Échelle de la fréquence cardiaque (cibles d'une séance en FC et FC mesurée).
        heart = [(x_.t, x_.heart_rate_bpm) for x_ in s.samples if x_.heart_rate_bpm]
        bpms = [b for _, b in heart] + [seg.target_bpm(t) for seg in s.segments if seg.step.heart_rate
                                         for t in (0, seg.duration_s or 0)]
        lo = min([60] + [b - 5 for b in bpms])
        hi = max([200] + [b + 5 for b in bpms])

        def yh(bpm: float) -> float:
            return area.bottom() - (bpm - lo) / (hi - lo) * area.height()

        # Graduations : FTP et axe du temps.
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
        p.drawLine(QPointF(area.left(), y(ftp)), QPointF(area.right(), y(ftp)))
        p.drawText(QRectF(0, y(ftp) - 8, margin_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, "FTP")
        step = next(v for v in (60, 300, 600, 900, 1800, 3600, 7200) if total / v <= 12)
        p.setPen(QColor(MUTED))
        t = 0
        while t <= total:
            p.drawText(QRectF(x(t) - 30, area.bottom() + 4, 60, 18), Qt.AlignHCenter, hms(t))
            t += step

        # Briques (rampes = trapèzes), passées assombries, en cours mise en avant.
        position = s.position_s
        for i, seg in enumerate(s.segments):
            if not seg.duration_s:
                continue
            x0, x1 = x(seg.start_s), x(seg.start_s + seg.duration_s)
            path = QPainterPath(QPointF(x0, area.bottom()))
            if seg.low_w is None and seg.step.heart_rate is not None:  # cible en FC, sans puissance
                path.lineTo(x0, yh(seg.target_bpm(0)))
                path.lineTo(x1, yh(seg.target_bpm(seg.duration_s)))
                color = QColor(HEART)
                color.setAlpha(150)
            else:
                w0 = seg.target_w(0) or 0
                w1 = seg.target_w(seg.duration_s) or 0
                path.lineTo(x0, y(max(w0, peak * 0.03)))
                path.lineTo(x1, y(max(w1, peak * 0.03)))
                color = zone_color(seg.target_w(seg.duration_s / 2), ftp)
            path.lineTo(x1, area.bottom())
            path.closeSubpath()
            if self.preview:
                current = any(seg.step is step for step in self.highlight)
            else:
                current = i == s.index
                if i < s.index:
                    color.setAlpha(min(color.alpha(), 90))
                elif i > s.index:
                    color.setAlpha(min(color.alpha(), 190))
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
        if len(s.samples) > 1:
            path = QPainterPath(QPointF(x(s.samples[0].t), y(s.samples[0].power_w)))
            for sample in s.samples[1:]:
                path.lineTo(x(sample.t), y(sample.power_w))
            p.setPen(QPen(QColor(POWER), 1.6))
            p.drawPath(path)

        # Cadence, sur sa propre échelle (graduée dans la marge de droite).
        draw_cadence(p, area, s.samples, x, area.right() + 6)

        # Fréquence cardiaque, sur sa propre échelle (graduée à droite).
        if len(heart) > 1 or any(seg.low_w is None and seg.step.heart_rate for seg in s.segments):
            p.setPen(QColor(HEART))
            for bpm in range(int(lo // 20 + 1) * 20, int(hi), 40):
                p.drawText(QRectF(area.right() - 40, yh(bpm) - 8, 38, 16), Qt.AlignRight | Qt.AlignVCenter,
                           f"{bpm}")
        if len(heart) > 1:
            path = QPainterPath(QPointF(x(heart[0][0]), yh(heart[0][1])))
            for t_, bpm in heart[1:]:
                path.lineTo(x(t_), yh(bpm))
            p.setPen(QPen(QColor(HEART), 1.4))
            p.drawPath(path)

        # Curseur de position.
        if self.preview:
            return
        p.setPen(QPen(QColor(TEXT), 2))
        p.drawLine(QPointF(x(position), area.top()), QPointF(x(position), area.bottom()))
