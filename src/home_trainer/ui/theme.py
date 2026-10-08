"""Habillage de l'appli : polices embarquées, feuille de style, zones de puissance et petits dessins
(pastilles d'état, icônes des boutons) partagés par la séance, le mode libre et le parcours.

Polices : Barlow pour l'interface, Barlow Condensed pour les grands chiffres (licence OFL,
dans assets/fonts). Sans elles (fichiers absents), Qt retombe sur la police du système.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

FONTS = Path(__file__).with_name("assets") / "fonts"
UI_FAMILY = "Barlow"
NUMBER_FAMILY = "Barlow Condensed"
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
RAISED = "#2a2f38"  # boutons, pastilles
HOVER = "#353b46"
LINE = "#2f3540"
GOOD = "#3fb37f"
WAIT = "#e7a23a"
OFF = "#5b616c"

ZONE_NAMES = ["Récup", "Endurance", "Tempo", "Seuil", "VO2max", "Anaérobie", "Neuromusculaire"]

_loaded = False


def load_fonts() -> None:
    """Ajoute les polices embarquées (une fois, après la création de la QApplication)."""
    global _loaded
    if _loaded or QApplication.instance() is None:
        return
    _loaded = True
    for path in sorted(FONTS.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def number_font(point_size: int, weight: QFont.Weight = QFont.Bold) -> QFont:
    """Police des grands chiffres : condensée, chiffres de même largeur (ils ne sautent pas)."""
    load_fonts()
    font = QFont(NUMBER_FAMILY)
    font.setPointSize(point_size)
    font.setWeight(weight)
    if hasattr(QFont, "Tag"):  # Qt 6.7 et plus
        font.setFeature(QFont.Tag("tnum"), 1)
    return font


def ui_font(point_size: float, weight: QFont.Weight = QFont.Normal) -> QFont:
    load_fonts()
    font = QFont(UI_FAMILY)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font


def zone_index(watts: float | None, ftp: float) -> int | None:
    """Zone de Coggan (0 à 6) de cette puissance, None sans puissance."""
    if watts is None or not ftp:
        return None
    pct = watts / ftp * 100
    return next(i for i, (limit, _) in enumerate(ZONES) if pct < limit)


def zone_label(watts: float | None, ftp: float) -> str:
    """« Z4 · Seuil »."""
    i = zone_index(watts, ftp)
    return "" if i is None else f"Z{i + 1} · {ZONE_NAMES[i]}"


def rgba(color: QColor | str, alpha: float) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.2f})"


def tinted_card(color: QColor | str | None) -> str:
    """Feuille de style d'une carte teintée par la couleur de la zone en cours (None : carte neutre)."""
    if color is None:
        return ""
    return (f"QFrame#metric {{ border: 1px solid {rgba(color, 0.45)}; background: qlineargradient("
            f"x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(color, 0.24)}, stop:0.65 {rgba(color, 0.05)}, "
            f"stop:1 {PANEL}); }}")


def pill(color: QColor | str) -> str:
    """Étiquette colorée (« Z4 · Seuil · 95 % FTP »)."""
    return (f"color: {QColor(color).name()}; background: {rgba(color, 0.16)}; border-radius: 6px; "
            f"padding: 3px 9px; font-weight: 600;")


def keycaps(text: str) -> str:
    """Raccourcis en touches : « [Espace] pause  [↑] [↓] ±1 % ». Les touches sont entre crochets."""
    out = []
    for part in text.split("["):
        if "]" in part:
            key, rest = part.split("]", 1)
            out.append(f"<span style='background-color:{RAISED}; color:{TEXT}; font-weight:600;'>"
                       f"&nbsp;{key}&nbsp;</span>{rest.replace(' ', '&nbsp;')}")
        else:
            out.append(part.replace(" ", "&nbsp;"))
    return f"<span style='color:{MUTED};'>{''.join(out)}</span>"


def dot(color: str, size: int = 10) -> QIcon:
    """Pastille d'état (vert : connecté, orange : en cours, gris : rien)."""
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    halo = QColor(color)
    halo.setAlpha(60)
    p.setBrush(halo)
    p.drawEllipse(QRectF(0, 0, size, size))
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(2, 2, size - 4, size - 4))
    p.end()
    return QIcon(pix)


def icon(name: str, color: str = TEXT, size: int = 16) -> QIcon:
    """Icônes dessinées des boutons : play, pause, next, check, restart, flag, back."""
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    s = size
    if name == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(s * .25, s * .15), QPointF(s * .85, s * .5), QPointF(s * .25, s * .85)]))
    elif name == "pause":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(s * .2, s * .15, s * .22, s * .7), 1.5, 1.5)
        p.drawRoundedRect(QRectF(s * .58, s * .15, s * .22, s * .7), 1.5, 1.5)
    elif name == "next":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(s * .15, s * .18), QPointF(s * .62, s * .5), QPointF(s * .15, s * .82)]))
        p.drawRoundedRect(QRectF(s * .66, s * .18, s * .16, s * .64), 1, 1)
    else:
        pen = p.pen()
        pen.setColor(c)
        pen.setWidthF(s * .13)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        if name == "check":
            path.moveTo(s * .18, s * .52)
            path.lineTo(s * .4, s * .74)
            path.lineTo(s * .82, s * .28)
        elif name == "restart":
            path.arcMoveTo(QRectF(s * .18, s * .18, s * .64, s * .64), 120)
            path.arcTo(QRectF(s * .18, s * .18, s * .64, s * .64), 120, 290)
            path.moveTo(s * .2, s * .12)
            path.lineTo(s * .25, s * .36)
            path.lineTo(s * .48, s * .3)
        elif name == "flag":
            path.moveTo(s * .25, s * .88)
            path.lineTo(s * .25, s * .14)
            path.lineTo(s * .78, s * .3)
            path.lineTo(s * .25, s * .48)
        elif name == "back":
            path.moveTo(s * .55, s * .2)
            path.lineTo(s * .25, s * .5)
            path.lineTo(s * .55, s * .8)
            path.moveTo(s * .27, s * .5)
            path.lineTo(s * .82, s * .5)
        p.drawPath(path)
    p.end()
    return QIcon(pix)


STYLE = f"""
QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px 6px; }}
QToolBar#header {{ background: {PANEL}; border: none; border-bottom: 1px solid {LINE}; padding: 6px 10px; spacing: 6px; }}
QToolBar#header QWidget {{ background: transparent; }}
QLabel#appName {{ padding: 0 8px 0 2px; }}
QToolButton#tab {{ background: transparent; border: none; border-radius: 7px; padding: 6px 11px; color: #c4c8cf; font-size: 14px; font-weight: 600; }}
QToolButton#tab:hover {{ background: {RAISED}; }}
QToolButton#tab:checked {{ background: {RAISED}; color: {TEXT}; }}
QToolButton#tab::menu-indicator {{ image: none; }}
QToolButton#chip, QPushButton#chip {{ background: {RAISED}; border: none; border-radius: 14px; padding: 5px 12px; font-size: 13px; font-weight: 600; color: {TEXT}; }}
QToolButton#chip:hover, QPushButton#chip:hover {{ background: {HOVER}; }}
QToolButton#chip::menu-indicator {{ image: none; }}
QToolBar#header QToolButton#chip {{ background: {RAISED}; }}
QToolBar#header QToolButton#chip:hover {{ background: {HOVER}; }}
QToolBar#header QWidget#chipBox {{ background: {RAISED}; border-radius: 14px; }}
QWidget#chipBox QLabel {{ color: {MUTED}; font-size: 13px; font-weight: 600; padding-left: 12px; }}
QWidget#chipBox QSpinBox, QWidget#chipBox QDoubleSpinBox {{ border: none; font-size: 13px; font-weight: 600; padding: 0; color: {TEXT}; }}
QWidget#chipBox QAbstractSpinBox::up-button, QWidget#chipBox QAbstractSpinBox::down-button {{ width: 0; border: none; image: none; }}  /* réglage au clavier ou à la molette */
QMenu {{ background: {RAISED}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}
QFrame#metric {{ background: {PANEL}; border-radius: 12px; }}
QFrame#metric QLabel, QFrame#metric QWidget#plain {{ background: transparent; }}
QLabel#metricTitle {{ color: {MUTED}; font-size: 11px; font-weight: bold; letter-spacing: 1.5px; }}
QLabel#metricSub {{ color: {MUTED}; font-size: 13px; background: transparent; }}
QLabel#unit {{ color: {MUTED}; background: transparent; }}
QLabel#title {{ font-size: 20px; font-weight: bold; }}
QLabel#current {{ font-size: 15px; color: #c4c8cf; }}
QPushButton {{ background: {RAISED}; border: none; border-radius: 9px; padding: 10px 16px; font-size: 14px; font-weight: 600; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton#play {{ background: {ACCENT}; color: #18191c; font-weight: bold; min-width: 120px; }}
QPushButton#play:hover {{ background: #ffdc6e; }}
QPushButton#mode {{ padding: 4px 12px; font-size: 13px; min-width: 52px; border-radius: 7px; }}
QPushButton#mode:checked {{ background: {ACCENT}; color: #18191c; font-weight: bold; }}
QPushButton#erg {{ background: {rgba('#e5484d', 0.16)}; color: #f08a8d; border: 1px solid {rgba('#e5484d', 0.45)}; font-weight: bold; min-width: 96px; }}
QPushButton#erg:checked {{ background: {rgba(GOOD, 0.16)}; color: #6fd3a3; border: 1px solid {rgba(GOOD, 0.45)}; }}
QPushButton#adjust {{ font-size: 17px; font-weight: bold; min-width: 56px; min-height: 40px; padding: 6px 10px; }}
QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {{ background: {RAISED}; border: none; border-radius: 6px; padding: 4px 8px; }}
QPushButton:disabled {{ color: {MUTED}; }}
QProgressBar {{ background: {RAISED}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {TEXT}; border-radius: 3px; }}
QTreeWidget {{ background: {PANEL}; border: none; border-radius: 10px; font-size: 14px; }}
QTreeWidget::item {{ padding: 4px; }}
QTreeWidget::item:selected {{ background: #3a404c; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: none; padding: 4px; font-weight: 600; }}
QStatusBar {{ color: {MUTED}; }}
"""


def install(app: QApplication) -> None:
    """Polices et feuille de style pour toute l'appli."""
    load_fonts()
    app.setFont(ui_font(10.5))
    app.setStyleSheet(STYLE)
