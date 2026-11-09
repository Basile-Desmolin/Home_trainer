"""Profil d'une séance (briques colorées par zones), partagé par la séance et l'éditeur."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

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


@dataclass(frozen=True)
class TimeAxis:
    """Axe du temps d'un graphique : x = left + (t - origin) × scale. Comparable : une courbe gardée
    en cache (`TracedCurve`) reste valable tant que l'axe ne change pas."""

    left: float
    origin: float
    scale: float  # pixels par seconde

    def __call__(self, t: float) -> float:
        return self.left + (t - self.origin) * self.scale


class _Columns:
    """Points d'une courbe réduits à un par colonne de pixels (moyenne des points qui y tombent),
    prolongeables : on ajoute des points, ou on revient en arrière pour refaire la fin.
    Un élément : [colonne, somme des x, somme des valeurs, nombre, indice du premier point] ;
    colonne None : trou (None) qui coupe la courbe."""

    def __init__(self) -> None:
        self.items: list[list] = []
        self.count = 0  # points déjà placés

    def extend(self, points, x) -> None:
        items = self.items
        i = self.count
        for t, v in points:
            if v is None:
                items.append([None, 0.0, 0.0, 0, i])
            else:
                px = x(t)
                column = int(px)
                last = items[-1] if items else None
                if last is not None and last[0] == column:
                    last[1] += px
                    last[2] += v
                    last[3] += 1
                else:
                    items.append([column, px, v, 1, i])
            i += 1
        self.count = i

    def rewind(self, index: int) -> None:
        """Oublie les points à partir de `index` (et refera la colonne entamée qui les contient)."""
        items = self.items
        while items and items[-1][4] >= index:
            items.pop()
        if items and items[-1][0] is not None:
            self.count = items.pop()[4]
        else:
            self.count = items[-1][4] + 1 if items else 0

    def lines(self, y) -> list[list[QPointF]]:
        lines: list[list[QPointF]] = []
        line: list[QPointF] = []
        for column, sum_x, sum_v, n, _ in self.items:
            if column is None:
                if line:
                    lines.append(line)
                    line = []
            else:
                line.append(QPointF(sum_x / n, y(sum_v / n)))
        if line:
            lines.append(line)
        return lines


def trace(points: list[tuple[float, float | None]], x, y) -> list[list[QPointF]]:
    """Morceaux d'une courbe mesurée : un point par pixel (moyenne des mesures qui y tombent),
    sinon une longue sortie entasse plusieurs secondes par pixel en zigzags. None coupe la courbe
    (un nouveau morceau commence)."""
    columns = _Columns()
    columns.extend(points, x)
    return columns.lines(y)


class TracedCurve:
    """Une courbe d'un tronçon de sortie, lissée puis réduite au pixel, tenue au fil des mesures.

    Refaire le lissage et la réduction de toute une sortie de 3 h à chaque dessin coûtait
    ~60 ms par courbe. Une nouvelle mesure ne change que la fin de la courbe lissée (moyenne
    centrée : la dernière demi-fenêtre) ; seule cette fin est refaite. Même résultat que
    `trace(smooth(...))` sur toute la courbe (à l'arrondi des sommes près)."""

    def __init__(self, window_s: float) -> None:
        self.window_s = window_s
        self.run: list | None = None  # tronçon suivi (liste de mesures, prolongée sur place)
        self.n = 0  # mesures du tronçon déjà lues
        self._last_sample = None
        self.raw: list[tuple[float, float | None]] = []
        self.smoothed: list[tuple[float, float | None]] = []
        self.columns = _Columns()
        self.x: TimeAxis | None = None
        self.values = 0  # valeurs mesurées (hors trous)
        self.low = math.inf
        self.high = -math.inf

    def follow(self, run: list, value, x: TimeAxis) -> TracedCurve:
        """Lit les mesures arrivées dans `run` depuis l'appel précédent (`value(mesure)` : la valeur
        tracée, None pour un trou). Un autre tronçon, ou un tronçon raccourci, repart de zéro."""
        n = self.n
        if run is not self.run or len(run) < n or (n and run[n - 1] is not self._last_sample):
            self.__init__(self.window_s)
            self.run = run
        self._extend([(s.t, value(s)) for s in run[self.n:]], x)
        self.n = len(run)
        self._last_sample = run[-1] if run else None
        return self

    def _extend(self, points: list[tuple[float, float | None]], x: TimeAxis) -> None:
        changed = len(self.smoothed)
        if points:
            raw = self.raw
            previous = raw[-1][0] if raw else None
            raw.extend(points)
            for _, v in points:
                if v is not None:
                    self.values += 1
                    self.low = min(self.low, v)
                    self.high = max(self.high, v)
            if previous is None:
                begin = changed = 0
            else:  # seules changent les moyennes dont la fenêtre atteint les nouveaux points
                begin = bisect.bisect_left(raw, previous - self.window_s, key=_time)
                changed = bisect.bisect_right(raw, previous - self.window_s / 2, key=_time)
            self.smoothed[changed:] = smooth(raw[begin:], self.window_s)[changed - begin:]
        if x != self.x:
            self.x = x
            self.columns = _Columns()
        else:
            self.columns.rewind(changed)
        self.columns.extend(self.smoothed[self.columns.count:], x)

    def lines(self, y) -> list[list[QPointF]]:
        return self.columns.lines(y)

    @property
    def last(self) -> float | None:
        """Dernière valeur mesurée."""
        return next((v for _, v in reversed(self.raw) if v is not None), None)

    @property
    def end(self) -> float | None:
        """Dernière valeur de la courbe lissée (bout de la courbe tracée)."""
        return next((v for _, v in reversed(self.smoothed) if v is not None), None)


def _time(point: tuple[float, float | None]) -> float:
    return point[0]


def follow(cache: dict | None, key, run: list, value, window_s: float, x: TimeAxis) -> TracedCurve:
    """Courbe `key` d'un tronçon, gardée dans `cache` d'un dessin à l'autre (None : calculée pour ce
    dessin seulement)."""
    curve = cache.get(key) if cache is not None else None
    if curve is None or curve.window_s != window_s:
        curve = TracedCurve(window_s)
        if cache is not None:
            cache[key] = curve
    return curve.follow(run, value, x)


def stroke(p: QPainter, pen: QPen, lines: list[list[QPointF]]) -> None:
    """Trace les morceaux d'une courbe en segments séparés, aux bouts arrondis (jonctions invisibles).

    Un chemin d'un seul tenant de 1 500 points anticrénelé coûte ~100 ms à Qt (contour
    calculé d'un bloc), les mêmes segments séparés ~3 ms, pour un rendu identique à l'œil."""
    pen = QPen(pen)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    for line in lines:
        if len(line) == 1:
            p.drawPoint(line[0])
        else:
            p.drawLines([point for a, b in zip(line, line[1:]) for point in (a, b)])


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


def _power(s) -> float:
    return s.power_w


def _heart(s) -> float | None:
    return s.heart_rate_bpm or None  # 0 bpm : ceinture qui décroche, pas une mesure


def _cadence(s) -> float | None:
    return s.cadence_rpm


def _speed(s) -> float | None:
    return s.speed_kmh


CURVE_VALUES = {"power": _power, "heart": _heart, "cadence": _cadence, "speed": _speed}


def curve_scale(key: str, values: list[float]) -> tuple[float, float]:
    """Échelle d'une courbe (bas, haut), arrondie, avec de la marge au-dessus de la plus forte valeur."""
    return curve_range(key, min(values, default=math.inf), max(values, default=0.0))


def curve_range(key: str, low: float, top: float) -> tuple[float, float]:
    """`curve_scale` d'après la plus faible et la plus forte valeur."""
    if key == "heart":
        return min(60.0, low - 5), max(200.0, top + 5)
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


def traced_runs(cache: dict | None, key: str, runs, x: TimeAxis) -> list[TracedCurve]:
    """Courbe `key` (puissance, cardio, cadence, vitesse) de chaque tronçon, lissée, au pixel."""
    smooth_s = POWER_SMOOTH_S if key == "power" else CURVES[key][3]
    return [follow(cache, (key, i), run, CURVE_VALUES[key], smooth_s, x) for i, run in enumerate(runs)]


def draw_curves(p: QPainter, area: QRectF, runs, x: TimeAxis, shown,
                scales: dict[str, tuple[float, float]] | None = None, labels: bool = True,
                cache: dict | None = None) -> None:
    """Courbes de `shown` (cadence, cardio, vitesse), un tronçon par élément de `runs` (lissé à part),
    et leur dernière valeur au bout, dans la marge de droite. `scales` impose une échelle (cardio
    d'une séance en FC, dont les cibles sont tracées sur la même). `cache` garde les courbes d'un
    dessin à l'autre (voir `TracedCurve`)."""
    tags: list[tuple[float, str, str]] = []  # (hauteur, texte, couleur)
    for key in ("speed", "cadence", "heart"):
        if key not in shown:
            continue
        _, color, unit, _smooth = CURVES[key]
        curves = traced_runs(cache, key, runs, x)
        if sum(c.values for c in curves) < 2:
            continue
        lo, hi = (scales or {}).get(key) or curve_range(key, min(c.low for c in curves),
                                                         max(c.high for c in curves))

        def yv(v: float, lo=lo, hi=hi) -> float:
            return area.bottom() - (v - lo) / (hi - lo) * area.height()

        pen = QPen(QColor(color), 1.4 if key == "heart" else 1.2)
        for curve in curves:
            stroke(p, pen, curve.lines(yv))
        # Texte : dernière valeur mesurée ; hauteur : bout de la courbe tracée (lissée).
        last = next(c.last for c in reversed(curves) if c.last is not None)
        end = next(c.end for c in reversed(curves) if c.end is not None)
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


def draw_power(p: QPainter, curves: list[TracedCurve], y) -> None:
    """Puissance réalisée, lissée (un tronçon de deux mesures au moins par courbe, voir `traced_runs`)."""
    pen = QPen(QColor(POWER), 2)
    for curve in curves:
        if curve.values >= 2:
            stroke(p, pen, curve.lines(y))


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
        self._shown: tuple | None = None  # ce que montre le dernier dessin demandé (voir `refresh`)
        self._traces: dict = {}  # courbes tenues d'un dessin à l'autre (voir `TracedCurve`)
        self.setMinimumHeight(160 if preview else 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_session(self, session: WorkoutSession) -> None:
        self.session = session
        self.update()

    def _area(self) -> QRectF:
        # À droite, la marge porte les étiquettes des courbes (pas en aperçu : rien de réalisé).
        margin_l, margin_r, margin_t, margin_b = 44, 12 if self.preview else LABELS_MARGIN, 14, 26
        return QRectF(margin_l, margin_t, self.width() - margin_l - margin_r, self.height() - margin_t - margin_b)

    def refresh(self) -> None:
        """Redessine seulement si quelque chose de visible a changé : nouvelle mesure, curseur passé à un
        autre pixel, brique, intensité ou état (le tic de 200 ms redessinait une sortie de 3 h
        5 fois par seconde, alors que le curseur y avance d'un pixel toutes les quelques secondes).
        Un changement de séance, de courbes ou de taille redessine de lui-même (`update`)."""
        s = self.session
        key = None
        if s is not None and s.segments:
            px = int(s.position_s / max(s.total_s, 1.0) * self._area().width())
            key = (id(s), id(s.samples), len(s.samples), px, s.index, s.intensity_pct, s.state, s.ftp)
        if key != self._shown:
            self._shown = key
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (API Qt)
        p = QPainter(self)
        paint_panel(p, self)
        s = self.session
        if s is None or not s.segments:
            return
        ftp = s.ftp or DEFAULT_FTP
        area = self._area()
        margin_l = area.left()
        total = max(s.total_s, 1.0)
        runs = s.chart_runs  # sur l'axe de la séance, qui saute quand on passe une brique
        x = TimeAxis(area.left(), 0.0, area.width() / total)
        # Puissance et cardio mesurés, lissés et réduits au pixel, tenus d'un dessin à l'autre.
        power = traced_runs(self._traces, "power", runs, x)
        heart = traced_runs(self._traces, "heart", runs, x)
        peak = max([seg.high_w or 0 for seg in s.segments] + [seg.end_high_w or 0 for seg in s.segments]
                   + [c.high for c in power if c.values] + [ftp])
        peak *= max(s.intensity_pct, 100) / 100 * 1.12

        def y(w: float) -> float:
            return area.bottom() - w / peak * area.height()

        # Échelle de la fréquence cardiaque (cibles d'une séance en FC et FC mesurée).
        targets = [seg.target_bpm(t) for seg in s.segments if seg.step.heart_rate for t in (0, seg.duration_s or 0)]
        lo = min([60] + [c.low - 5 for c in heart if c.values] + [b - 5 for b in targets])
        hi = max([200] + [c.high + 5 for c in heart if c.values] + [b + 5 for b in targets])

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
        draw_power(p, power, y)

        # Séance en FC : ses briques sont tracées sur l'échelle de la FC, graduée à l'intérieur, à droite.
        if any(seg.low_w is None and seg.step.heart_rate for seg in s.segments):
            p.setPen(QColor(HEART))
            for bpm in range(int(lo // 20 + 1) * 20, int(hi), 40):
                p.drawText(QRectF(area.right() - 40, yh(bpm) - 8, 38, 16), Qt.AlignRight | Qt.AlignVCenter,
                           f"{bpm}")

        # Cadence, cardio, vitesse (au choix), chacune sur son échelle ; un tronçon par morceau de séance.
        draw_curves(p, area, runs, x, self.curves, {"heart": (lo, hi)}, labels=not self.preview, cache=self._traces)

        # Curseur de position.
        if self.preview:
            return
        last = runs[-1][-1] if runs and runs[-1] else None
        draw_cursor(p, x(position), area.top(), area.bottom(),
                    y(last.power_w) if last is not None and abs(x(last.t) - x(position)) < 6 else None)
