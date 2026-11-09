"""Profil d'une séance (briques colorées par zones), partagé par la séance et l'éditeur."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .session import WorkoutSession
from .theme import (ACCENT, BG, CADENCE, HEART, LINE, MUTED, PANEL, POWER, SPEED, TEXT,  # noqa: F401 (réexportés)
                    ZONES, ui_font)

DEFAULT_FTP = 250
RADIUS = 12  # coins des cartes et des graphiques

# Lissage des courbes mesurées (moyenne glissante centrée, en secondes). Une mesure par seconde
# tracée telle quelle fait des dents de scie : à 122 bpm, la ceinture passe sans cesse de 121 à
# 123 et chaque battement devient un trait vertical, alors que le chiffre affiché paraît fixe.
POWER_SMOOTH_S = 5
CADENCE_SMOOTH_S = 5
HEART_SMOOTH_S = 10
SPEED_SMOOTH_S = 5


def smooth(points: list[tuple[float, float | None]], window_s: float) -> list[tuple[float, float | None]]:
    """Moyenne glissante centrée sur `window_s` secondes. Un trou (None) reste un trou et
    coupe la moyenne : on ne lisse pas d'un côté à l'autre d'une coupure du capteur."""
    out: list[tuple[float, float | None]] = []
    run: list[tuple[float, float]] = []

    def flush() -> None:
        lo = hi = 0
        total = 0.0
        half = window_s / 2
        for t, _ in run:
            while hi < len(run) and run[hi][0] <= t + half:
                total += run[hi][1]
                hi += 1
            while run[lo][0] < t - half:
                total -= run[lo][1]
                lo += 1
            out.append((t, total / (hi - lo)))
        run.clear()

    for t, v in points:
        if v is None:
            flush()
            out.append((t, None))
        else:
            run.append((t, v))
    flush()
    return out


def trace(points: list[tuple[float, float | None]], x, y) -> QPainterPath:
    """Chemin d'une courbe mesurée : un point par pixel (moyenne des mesures qui y tombent),
    sinon une longue sortie entasse plusieurs secondes par pixel en zigzags. None coupe la courbe."""
    path = QPainterPath()
    column: int | None = None
    xs: list[float] = []
    vs: list[float] = []
    drawing = False

    def emit() -> None:
        nonlocal drawing
        if not vs:
            return
        point = QPointF(sum(xs) / len(xs), y(sum(vs) / len(vs)))
        if drawing:
            path.lineTo(point)
        else:
            path.moveTo(point)
        drawing = True
        xs.clear()
        vs.clear()

    for t, v in points:
        if v is None:
            emit()
            drawing = False
            column = None
            continue
        px = x(t)
        if column is not None and int(px) != column:
            emit()
        column = int(px)
        xs.append(px)
        vs.append(v)
    emit()
    return path


def zone_color(watts: float | None, ftp: float) -> QColor:
    if watts is None:
        return QColor("#4a505c")
    pct = watts / ftp * 100
    return QColor(next(color for limit, color in ZONES if pct < limit))


# Courbes en plus de la puissance, chacune sur sa propre échelle, sans graduation : une étiquette de sa
# couleur au bout de la courbe, dans la marge de droite, donne la dernière valeur. Le choix des courbes
# affichées (clic droit sur le graphique) est gardé dans le profil.
CURVES = {  # clé : (nom du menu, couleur, unité, lissage en s)
    "cadence": ("Cadence", CADENCE, "tr/min", CADENCE_SMOOTH_S),
    "heart": ("Cardio", HEART, "bpm", HEART_SMOOTH_S),
    "speed": ("Vitesse", SPEED, "km/h", SPEED_SMOOTH_S),
}
DEFAULT_CURVES = frozenset({"cadence", "heart"})  # la vitesse est en option
LABELS_MARGIN = 62  # marge de droite : étiquettes au bout des courbes


def curve_points(run, key: str) -> list[tuple[float, float | None]]:
    if key == "heart":  # 0 bpm : ceinture qui décroche, pas une mesure
        return [(s.t, s.heart_rate_bpm or None) for s in run]
    return [(s.t, s.cadence_rpm if key == "cadence" else s.speed_kmh) for s in run]


def curve_scale(key: str, values: list[float]) -> tuple[float, float]:
    """Échelle d'une courbe (bas, haut), arrondie, avec de la marge au-dessus de la plus forte valeur."""
    top = max(values, default=0.0)
    if key == "heart":
        return min([60.0] + [v - 5 for v in values]), max(200.0, top + 5)
    if key == "cadence":
        return 0.0, float(max(120, (int(top) // 20 + 1) * 20))
    return 0.0, float(max(50, (int(top) // 10 + 1) * 10))


def draw_watts_axis(p: QPainter, area: QRectF, y, peak: float, ftp: float, margin_l: float) -> None:
    """Watts gradués dans la marge de gauche (avec de fines lignes), et la FTP en pointillés."""
    step = next(s for s in (50, 100, 200, 500) if peak / s <= 6)
    p.setPen(QColor(MUTED))
    w = step
    while w < peak:
        level = y(w)
        if abs(level - y(ftp)) > 12 and level - area.top() > 12:  # pas sur l'étiquette FTP ni sur « W »
            p.drawText(QRectF(0, level - 8, margin_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{w}")
        p.setPen(QPen(QColor(LINE), 1))
        p.drawLine(QPointF(area.left(), level), QPointF(area.right(), level))
        p.setPen(QColor(MUTED))
        w += step
    p.drawText(QRectF(0, area.top() - 14, margin_l - 6, 14), Qt.AlignRight | Qt.AlignVCenter, "W")
    p.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
    p.drawLine(QPointF(area.left(), y(ftp)), QPointF(area.right(), y(ftp)))
    p.drawText(QRectF(0, y(ftp) - 8, margin_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, "FTP")


def draw_curves(p: QPainter, area: QRectF, runs, x, shown, scales: dict[str, tuple[float, float]] | None = None,
                labels: bool = True) -> None:
    """Courbes de `shown` (cadence, cardio, vitesse), un tronçon par élément de `runs` (lissé à part),
    et leur dernière valeur au bout, dans la marge de droite. `scales` impose une échelle (cardio
    d'une séance en FC, dont les cibles sont tracées sur la même)."""
    tags: list[tuple[float, str, str]] = []  # (hauteur, texte, couleur)
    for key in ("speed", "cadence", "heart"):
        if key not in shown:
            continue
        _, color, unit, smooth_s = CURVES[key]
        series = [curve_points(run, key) for run in runs]
        values = [v for points in series for _, v in points if v is not None]
        if len(values) < 2:
            continue
        lo, hi = (scales or {}).get(key) or curve_scale(key, values)

        def yv(v: float, lo=lo, hi=hi) -> float:
            return area.bottom() - (v - lo) / (hi - lo) * area.height()

        p.setPen(QPen(QColor(color), 1.4 if key == "heart" else 1.2))
        smoothed = [smooth(points, smooth_s) for points in series]
        for points in smoothed:
            p.drawPath(trace(points, x, yv))
        # Texte : dernière valeur mesurée ; hauteur : bout de la courbe tracée (lissée).
        last = next(v for points in reversed(series) for _, v in reversed(points) if v is not None)
        end = next(v for points in reversed(smoothed) for _, v in reversed(points) if v is not None)
        shown_last = f"{last:.1f}".replace(".", ",") if key == "speed" else f"{last:.0f}"
        tags.append((yv(end), f"{shown_last} {unit}", color))
    if labels:
        draw_end_labels(p, area, tags)


def draw_end_labels(p: QPainter, area: QRectF, tags: list[tuple[float, str, str]]) -> None:
    """Étiquettes au bout des courbes, écartées les unes des autres pour rester lisibles."""
    height = 15.0
    placed: list[tuple[float, str, str]] = []
    for level, text, color in sorted(tags):
        level = min(max(level, area.top() + height / 2), area.bottom() - height / 2)
        if placed and level < placed[-1][0] + height:
            level = placed[-1][0] + height
        placed.append((level, text, color))
    overflow = placed[-1][0] - (area.bottom() - height / 2) if placed else 0
    if overflow > 0:  # trop bas : on remonte le tout
        placed = [(level - overflow, text, color) for level, text, color in placed]
    width = p.device().width() - area.right() - 4
    for level, text, color in placed:
        p.setPen(QColor(color))
        p.drawText(QRectF(area.right() + 6, level - height / 2, width, height), Qt.AlignLeft | Qt.AlignVCenter, text)


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


def draw_power(p: QPainter, samples, x, y) -> None:
    """Puissance réalisée, lissée."""
    if len(samples) < 2:
        return
    path = trace(smooth([(s.t, s.power_w) for s in samples], POWER_SMOOTH_S), x, y)
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
        self.curves: set[str] = set(DEFAULT_CURVES)  # courbes affichées en plus de la puissance
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
        # À droite, la marge porte les étiquettes des courbes (pas en aperçu : rien de réalisé).
        margin_l, margin_r, margin_t, margin_b = 44, 12 if self.preview else LABELS_MARGIN, 14, 26
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

        # Graduations : watts et FTP, axe du temps.
        draw_watts_axis(p, area, y, peak, ftp, margin_l)
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
            draw_power(p, run, x, y)

        # Séance en FC : ses briques sont tracées sur l'échelle de la FC, graduée à l'intérieur, à droite.
        if any(seg.low_w is None and seg.step.heart_rate for seg in s.segments):
            p.setPen(QColor(HEART))
            for bpm in range(int(lo // 20 + 1) * 20, int(hi), 40):
                p.drawText(QRectF(area.right() - 40, yh(bpm) - 8, 38, 16), Qt.AlignRight | Qt.AlignVCenter,
                           f"{bpm}")

        # Cadence, cardio, vitesse (au choix), chacune sur son échelle ; un tronçon par morceau de séance.
        draw_curves(p, area, runs, x, self.curves, {"heart": (lo, hi)}, labels=not self.preview)

        # Curseur de position.
        if self.preview:
            return
        last = samples[-1] if samples else None
        draw_cursor(p, x(position), area.top(), area.bottom(),
                    y(last.power_w) if last is not None and abs(x(last.t) - x(position)) < 6 else None)
