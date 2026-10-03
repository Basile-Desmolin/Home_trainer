"""Fenêtre principale : séance complète, temps restant, puissance, cardio, réglage ±1 %.

    home-trainer-gui [seance.erg] [--ftp 250] [--bricks "10m@150 3x(4m@105% 2m@55%)"]
                     [--trainer sim|ble|ant] [--trainer-address AA:BB:…] [--trainer-ant-id 12345]
                     [--hr sim|ble|ant|aucun] [--hr-address AA:BB:…] [--hr-ant-id 12345]

Raccourcis : Espace = démarrer / pause, ↑ ou + = +1 %, ↓ ou − = −1 %,
→ ou N = brique suivante.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QSizePolicy, QSpinBox, QStackedWidget, QToolBar,
                               QVBoxLayout, QWidget)

from ..bricks import BrickSyntaxError, parse_workout
from ..sensors import (BackgroundSensor, HeartRateReading, SensorState, SimulatedHeartRate, Trainer,
                       open_heart_rate_sensor)
from ..workout import PowerUnit, Segment, Workout
from .loader import FILE_FILTER, load_workout
from .power import PowerSource, SimulatedTrainer, open_power_source
from .session import State, WorkoutSession

DEFAULT_BRICKS = "10m@50%>75% 3x(8m@90% 3m@55%) 5m@120% 10m@60%>45%"
DEFAULT_FTP = 250
TICK_MS = 200

BG = "#16181d"
PANEL = "#20242c"
TEXT = "#e8eaed"
MUTED = "#8b919c"
ACCENT = "#ffd24a"
POWER = "#4fc3f7"
HEART = "#ff5c6c"

# Zones de Coggan (borne haute en % FTP) et couleurs associées.
ZONES = [(55, "#7f8c9a"), (76, "#3d8bd9"), (91, "#3fb37f"), (106, "#e7c43a"),
         (121, "#f08a2c"), (151, "#e5484d"), (10_000, "#b84ae0")]


def zone_color(watts: float | None, ftp: float) -> QColor:
    if watts is None:
        return QColor("#4a505c")
    pct = watts / ftp * 100
    return QColor(next(color for limit, color in ZONES if pct < limit))


def hms(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def describe_segment(seg: Segment | None, ftp: float, intensity_pct: int = 100) -> str:
    if seg is None:
        return "—"
    duration = "jusqu'au tour" if seg.duration_s is None else hms(seg.duration_s)
    p = seg.step.power
    if p is None or seg.low_w is None:
        target = "libre"
    else:
        k = intensity_pct / 100
        start = (seg.low_w + seg.high_w) / 2 * k
        end = (seg.end_low_w + seg.end_high_w) / 2 * k
        target = f"{start:.0f} W" if round(start) == round(end) else f"{start:.0f} → {end:.0f} W"
        if p.unit is PowerUnit.FTP_PERCENT:
            pct = (p.low + p.high) / 2 * k
            target += f"  ({pct:.0f} % FTP)" if not seg.step.is_ramp else ""
    name = f"  ·  {seg.step.name}" if seg.step.name else ""
    return f"{duration} à {target}{name}"


class WorkoutChart(QWidget):
    """Profil complet de la séance, avancement et puissance réalisée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session: WorkoutSession | None = None
        self.setMinimumHeight(220)
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
        margin_l, margin_r, margin_t, margin_b = 44, 12, 12, 26
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
            w0 = seg.target_w(0) or 0
            w1 = seg.target_w(seg.duration_s) or 0
            path = QPainterPath(QPointF(x0, area.bottom()))
            path.lineTo(x0, y(max(w0, peak * 0.03)))
            path.lineTo(x1, y(max(w1, peak * 0.03)))
            path.lineTo(x1, area.bottom())
            path.closeSubpath()
            color = zone_color(seg.target_w(seg.duration_s / 2), ftp)
            if i < s.index:
                color.setAlpha(90)
            elif i > s.index:
                color.setAlpha(190)
            p.fillPath(path, color)
            if i == s.index:
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

        # Fréquence cardiaque, sur sa propre échelle (graduée à droite).
        heart = [(x_.t, x_.heart_rate_bpm) for x_ in s.samples if x_.heart_rate_bpm]
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

        # Curseur de position.
        p.setPen(QPen(QColor(TEXT), 2))
        p.drawLine(QPointF(x(position), area.top()), QPointF(x(position), area.bottom()))


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


class MainWindow(QMainWindow):
    def __init__(self, workout: Workout, ftp: float = DEFAULT_FTP,
                 source: PowerSource | None = None,
                 heart_rate: BackgroundSensor[HeartRateReading] | None = None) -> None:
        super().__init__()
        self.ftp = ftp
        self.source: PowerSource = source or SimulatedTrainer()
        self.session = WorkoutSession(workout, ftp)
        self._since_sample = 0.0
        self._last_reading = None
        self.heart_rate: BackgroundSensor[HeartRateReading] | None = None

        self._build_toolbar()
        self._build_body()
        self._build_shortcuts()

        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(TICK_MS)
        self.clock.start()
        self.load(workout)
        self.set_power_source(self.source)
        self.set_heart_rate_sensor(heart_rate)

    # --- construction ----------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = QToolBar("Séance")
        bar.setMovable(False)
        self.addToolBar(bar)
        open_action = QAction("Ouvrir…", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._open_file)
        bar.addAction(open_action)
        bricks_action = QAction("Briques…", self)
        bricks_action.triggered.connect(self._enter_bricks)
        bar.addAction(bricks_action)
        trainer_action = QAction("Home trainer…", self)
        trainer_action.triggered.connect(self._choose_trainer)
        bar.addAction(trainer_action)
        heart_action = QAction("Cardio…", self)
        heart_action.triggered.connect(self._choose_heart_rate)
        bar.addAction(heart_action)
        bar.addSeparator()
        bar.addWidget(QLabel(" FTP "))
        self.ftp_box = QSpinBox()
        self.ftp_box.setRange(50, 600)
        self.ftp_box.setSuffix(" W")
        self.ftp_box.setValue(int(self.ftp))
        self.ftp_box.editingFinished.connect(self._ftp_changed)
        bar.addWidget(self.ftp_box)
        bar.addSeparator()
        self.source_label = QLabel(f"  {self.source.name}")
        self.source_label.setObjectName("metricSub")
        bar.addWidget(self.source_label)

    def _build_body(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(10)

        self.title = QLabel()
        self.title.setObjectName("title")
        layout.addWidget(self.title)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.m_power = Metric("PUISSANCE", 52, POWER)
        self.m_target = Metric("CIBLE", 52, ACCENT)
        self.m_step = Metric("RESTE SUR LA BRIQUE", 52)
        self.m_total = Metric("RESTE AU TOTAL", 30)
        self.m_cadence = Metric("CADENCE", 30)
        self.m_heart = Metric("CARDIO", 52, HEART)
        grid.addWidget(self.m_power, 0, 0)
        grid.addWidget(self.m_target, 0, 1)
        grid.addWidget(self.m_heart, 0, 2)
        grid.addWidget(self.m_step, 0, 3)
        grid.addWidget(self.m_cadence, 1, 0)
        grid.addWidget(self._build_intensity(), 1, 1, 1, 2)
        grid.addWidget(self.m_total, 1, 3)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        layout.addLayout(grid)

        self.current_label = QLabel()
        self.current_label.setObjectName("current")
        self.next_label = QLabel()
        self.next_label.setObjectName("metricSub")
        layout.addWidget(self.current_label)
        layout.addWidget(self.next_label)

        self.chart = WorkoutChart()
        layout.addWidget(self.chart, 1)

        buttons = QHBoxLayout()
        self.play_button = QPushButton("Démarrer")
        self.play_button.setObjectName("play")
        self.play_button.clicked.connect(self._toggle)
        self.next_button = QPushButton("Brique suivante")
        self.next_button.clicked.connect(self._next_step)
        self.reset_button = QPushButton("Recommencer")
        self.reset_button.clicked.connect(self._reset)
        for b in (self.play_button, self.next_button, self.reset_button):
            b.setFocusPolicy(Qt.NoFocus)
            buttons.addWidget(b)
        buttons.addStretch(1)
        hint = QLabel("Espace : démarrer / pause   ↑ ↓ : ±1 %   → : brique suivante")
        hint.setObjectName("metricSub")
        buttons.addWidget(hint)
        layout.addLayout(buttons)

    def _build_intensity(self) -> QWidget:
        box = QFrame()
        box.setObjectName("metric")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(4)
        title = QLabel("INTENSITÉ")
        title.setObjectName("metricTitle")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.minus_button = QPushButton("−1 %")
        self.plus_button = QPushButton("+1 %")
        self.intensity_label = QLabel("100 %")
        font = QFont()
        font.setPointSize(30)
        font.setBold(True)
        self.intensity_label.setFont(font)
        self.intensity_label.setAlignment(Qt.AlignCenter)
        for b, delta in ((self.minus_button, -1), (self.plus_button, +1)):
            b.setObjectName("adjust")
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoRepeat(True)  # rester appuyé fait défiler les pourcents
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(120)
            b.clicked.connect(lambda _=False, d=delta: self.adjust(d))
        row.addWidget(self.minus_button)
        row.addWidget(self.intensity_label, 1)
        row.addWidget(self.plus_button)
        layout.addLayout(row)
        return box

    def _build_shortcuts(self) -> None:
        for keys, slot in (((Qt.Key_Space,), self._toggle),
                           ((Qt.Key_Up, Qt.Key_Plus, Qt.Key_Equal), lambda: self.adjust(+1)),
                           ((Qt.Key_Down, Qt.Key_Minus), lambda: self.adjust(-1)),
                           ((Qt.Key_Right, Qt.Key_N), self._next_step)):
            for key in keys:
                QShortcut(QKeySequence(key), self, activated=slot)

    # --- actions ---------------------------------------------------------

    def load(self, workout: Workout) -> None:
        intensity = self.session.intensity_pct
        self.session = WorkoutSession(workout, self.ftp)
        self.session.intensity_pct = intensity
        self._since_sample = 0.0
        self.chart.set_session(self.session)
        self.title.setText(f"{workout.name}  ·  {hms(self.session.total_s)}")
        self.setWindowTitle(f"{workout.name} — Home trainer")
        self._refresh()

    def adjust(self, delta: int) -> None:
        self.session.adjust_intensity(delta)
        self._push_target()
        self._refresh()

    def _toggle(self) -> None:
        if self.session.state is State.FINISHED:
            self._reset()
        self.session.toggle()
        self.clock.restart()
        self._refresh()

    def _next_step(self) -> None:
        self.session.next_step()
        self._push_target()
        self._refresh()

    def _reset(self) -> None:
        self.load(self.session.workout)

    def _ftp_changed(self) -> None:
        if self.ftp_box.value() == self.ftp:
            return
        self.ftp = self.ftp_box.value()
        if isinstance(self.heart_rate, SimulatedHeartRate):
            self.heart_rate.ftp = self.ftp
        s = self.session
        index, step_elapsed, elapsed, state, samples = (s.index, s.step_elapsed_s, s.elapsed_s,
                                                         s.state, s.samples)
        self.load(s.workout)
        # Garder l'avancement : seule la conversion % FTP → W change.
        self.session.index, self.session.step_elapsed_s, self.session.elapsed_s = index, step_elapsed, elapsed
        self.session.state, self.session.samples = state, samples
        self._refresh()

    def set_power_source(self, source: PowerSource) -> None:
        """Remplace le home trainer (simulé ou réel) et le démarre."""
        if source is not self.source and isinstance(self.source, Trainer):
            self.source.stop()
        self.source = source
        self._last_reading = None
        if isinstance(source, Trainer):
            source.start()
        self._push_target()
        self._refresh()

    def _choose_trainer(self) -> None:
        dialog = TrainerDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.set_power_source(dialog.sensor())

    def set_heart_rate_sensor(self, sensor: BackgroundSensor[HeartRateReading] | None) -> None:
        """Remplace le capteur cardio (None = aucun) et le démarre."""
        if self.heart_rate is not None:
            self.heart_rate.stop()
        if isinstance(sensor, SimulatedHeartRate):
            sensor.ftp = self.ftp
            sensor.effort_w = self._simulated_effort
        self.heart_rate = sensor
        if sensor is not None:
            sensor.start()
        self._refresh()

    def _simulated_effort(self) -> float | None:
        """Ce que « ressent » le cardio simulé : la puissance pédalée en ce moment."""
        r = self._last_reading
        return r.power_w if r is not None and self.session.state is State.RUNNING else None

    def _choose_heart_rate(self) -> None:
        dialog = HeartRateDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.set_heart_rate_sensor(dialog.sensor())

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if self.heart_rate is not None:
            self.heart_rate.stop()
        if isinstance(self.source, Trainer):
            self.source.stop()
        super().closeEvent(event)

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Ouvrir une séance", str(Path.home()), FILE_FILTER)
        if not path:
            return
        warnings: list[str] = []
        try:
            workout = load_workout(path, warnings)
        except Exception as e:  # noqa: BLE001 (tout échec de lecture est montré à l'utilisateur)
            QMessageBox.warning(self, "Lecture impossible", f"{Path(path).name} : {e}")
            return
        if warnings:
            QMessageBox.information(self, "À savoir", "\n".join(warnings))
        self.load(workout)

    def _enter_bricks(self) -> None:
        text, ok = QInputDialog.getText(self, "Composer une séance",
                                        "Briques (ex. 10m@150 3x(4m@105% 2m@55%) 10m@110) :",
                                        text=DEFAULT_BRICKS)
        if not ok or not text.strip():
            return
        try:
            self.load(parse_workout(text, name="Séance en briques"))
        except BrickSyntaxError as e:
            QMessageBox.warning(self, "Briques invalides", str(e))

    # --- boucle ----------------------------------------------------------

    def _on_tick(self) -> None:
        dt = self.clock.restart() / 1000
        self.session.tick(dt)
        self._push_target()
        running = self.session.state is State.RUNNING
        # Un vrai home trainer est lu en permanence (échauffement, pause) ; le simulateur seulement en séance.
        reading = self.source.read(dt) if running or isinstance(self.source, Trainer) else None
        self._last_reading = reading
        if reading is not None and running:
            self._since_sample += dt
            if self._since_sample >= 1.0:
                self._since_sample -= 1.0
                heart = self.heart_rate.latest() if self.heart_rate is not None else None
                self.session.record(reading.power_w, reading.cadence_rpm, heart.bpm if heart else None)
        self._refresh()

    def _push_target(self) -> None:
        # Hors séance (avant le départ, en pause), le home trainer reste en résistance libre.
        running = self.session.state is State.RUNNING
        self.source.set_target(self.session.target_w if running else None)

    def _refresh(self) -> None:
        s = self.session
        r = self._last_reading
        self.m_power.set(f"{r.power_w:.0f} W" if r else "—", f"{r.power_w / self.ftp * 100:.0f} % FTP" if r else "")
        target = s.target_w
        base = s.base_target_w
        self.m_target.set("libre" if target is None else f"{target:.0f} W",
                          "" if base is None or s.intensity_pct == 100
                          else f"séance : {base:.0f} W  ({s.intensity_pct} %)")
        remaining = s.step_remaining_s
        self.m_step.set("tour" if remaining is None else hms(remaining),
                        f"brique {min(s.index + 1, len(s.segments))} / {len(s.segments)}")
        self.m_total.set(hms(s.total_remaining_s), f"écoulé : {hms(s.elapsed_s)}")
        self.m_cadence.set(f"{r.cadence_rpm:.0f}" if r and r.cadence_rpm is not None else "—", "tr/min")
        self._refresh_heart_rate()
        self._refresh_source()
        self.intensity_label.setText(f"{s.intensity_pct} %")
        self.intensity_label.setStyleSheet(f"color: {TEXT if s.intensity_pct == 100 else ACCENT};")
        if s.state is State.FINISHED:
            self.current_label.setText("Séance terminée")
            self.next_label.setText("")
        else:
            self.current_label.setText(f"Maintenant : {describe_segment(s.segment, self.ftp, s.intensity_pct)}")
            self.next_label.setText(f"Ensuite : {describe_segment(s.next_segment, self.ftp, s.intensity_pct)}"
                                    if s.next_segment else "Dernière brique")
        self.play_button.setText({State.RUNNING: "Pause", State.PAUSED: "Reprendre",
                                  State.FINISHED: "Recommencer"}.get(s.state, "Démarrer"))
        self.chart.update()

    def _refresh_source(self) -> None:
        source = self.source
        text = source.name
        if isinstance(source, Trainer) and (source.state is not SensorState.CONNECTED or source.latest() is None):
            text += f" : {source.status if source.state is not SensorState.CONNECTED else 'signal perdu'}"
        self.source_label.setText(f"  {text}")

    def _refresh_heart_rate(self) -> None:
        sensor = self.heart_rate
        if sensor is None:
            self.m_heart.set("—", "aucun capteur · menu Cardio…")
            return
        heart = sensor.latest()
        average = self.session.average_heart_rate()
        if heart is not None:
            sub = f"bpm · moy. {average:.0f}" if average else "bpm"
            if heart.battery_pct is not None and heart.battery_pct <= 20:
                sub += f" · batterie {heart.battery_pct} %"
            if heart.contact is False:
                sub += " · mauvais contact"
            self.m_heart.set(f"{heart.bpm}", sub)
        else:
            state = sensor.status if sensor.state is not SensorState.CONNECTED else "signal perdu"
            self.m_heart.set("—", f"{sensor.name} : {state}")


class SensorDialog(QDialog):
    """Choix d'un appareil : simulé, Bluetooth (avec recherche) ou ANT+ (numéro, 0 = premier trouvé)."""

    TITLE = ""
    KINDS: list[tuple[str, str | None]] = []
    SIMULATED_TEXT = ""
    SCAN_TEXT = ""
    ANY_DEVICE = ""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        self.kind = QComboBox()
        self.pages = QStackedWidget()
        for label, kind in self.KINDS:
            self.kind.addItem(label, kind)
            self.pages.addWidget(self._page(kind))
        form.addRow("Appareil", self.kind)
        form.addRow("", self.pages)
        self.message = QLabel("")
        self.message.setObjectName("metricSub")
        self.message.setWordWrap(True)
        form.addRow("", self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(self.pages.setCurrentIndex)
        self.kind.setCurrentIndex([k for _, k in self.KINDS].index("sim"))

        self._scan_result: list | Exception | None = None
        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(self._scan_done)

    def _page(self, kind: str | None) -> QWidget:
        if kind == "sim":
            return QLabel(self.SIMULATED_TEXT)
        if kind == "ble":
            page = QWidget()
            row = QHBoxLayout(page)
            row.setContentsMargins(0, 0, 0, 0)
            self.ble_devices = QComboBox()
            self.ble_devices.addItem(self.ANY_DEVICE, None)
            self.scan_button = QPushButton("Rechercher")
            self.scan_button.clicked.connect(self._scan)
            row.addWidget(self.ble_devices, 1)
            row.addWidget(self.scan_button)
            return page
        if kind == "ant":
            self.ant_number = QSpinBox()
            self.ant_number.setRange(0, 65535)
            self.ant_number.setSpecialValueText(self.ANY_DEVICE)
            return self.ant_number
        return QWidget()

    def scan_devices(self) -> list:
        raise NotImplementedError

    def _scan(self) -> None:
        def work() -> None:
            try:
                self._scan_result = self.scan_devices()
            except Exception as e:  # noqa: BLE001 (affiché à l'utilisateur)
                self._scan_result = e

        self._scan_result = None
        self.scan_button.setEnabled(False)
        self.message.setText(self.SCAN_TEXT)
        threading.Thread(target=work, daemon=True).start()
        self._scan_timer.start(200)

    def _scan_done(self) -> None:
        result = self._scan_result
        if result is None:
            return
        self._scan_timer.stop()
        self.scan_button.setEnabled(True)
        if isinstance(result, Exception):
            self.message.setText(f"Recherche impossible : {result}")
            return
        while self.ble_devices.count() > 1:
            self.ble_devices.removeItem(1)
        for device in result:
            self.ble_devices.addItem(str(device), device.address)
        if result:
            self.ble_devices.setCurrentIndex(1)
        self.message.setText(f"{len(result)} appareil(s) trouvé(s)." if result else "Aucun appareil trouvé.")


class HeartRateDialog(SensorDialog):
    """Choix du capteur cardio : aucun, simulé, Bluetooth ou ANT+."""

    TITLE = "Capteur cardiaque"
    KINDS = [("Aucun", None), ("Simulé", "sim"), ("Bluetooth", "ble"), ("ANT+ (clé USB)", "ant")]
    SIMULATED_TEXT = "Fréquence calculée à partir de la puissance pédalée."
    SCAN_TEXT = "Recherche des ceintures Bluetooth (5 s)… Mouillez la sangle pour la réveiller."
    ANY_DEVICE = "Première ceinture trouvée"

    def scan_devices(self) -> list:
        from ..sensors.ble import scan_heart_rate_monitors
        return scan_heart_rate_monitors(timeout=5)

    def sensor(self) -> BackgroundSensor[HeartRateReading] | None:
        kind = self.kind.currentData()
        if kind is None:
            return None
        return open_heart_rate_sensor(kind, address=self.ble_devices.currentData(),
                                      device_number=self.ant_number.value())


class TrainerDialog(SensorDialog):
    """Choix du home trainer : simulé, Wahoo en Bluetooth ou en ANT+."""

    TITLE = "Home trainer"
    KINDS = [("Simulé", "sim"), ("Wahoo Bluetooth", "ble"), ("Wahoo ANT+ (clé USB)", "ant")]
    SIMULATED_TEXT = "Puissance imitée : rejoint la consigne en quelques secondes."
    SCAN_TEXT = ("Recherche des home trainers Bluetooth (5 s)… Pédalez pour réveiller le Wahoo, "
                 "et fermez les autres applis qui pourraient s'y connecter (Wahoo, Zwift…).")
    ANY_DEVICE = "Premier home trainer trouvé"

    def scan_devices(self) -> list:
        from ..sensors.trainer_ble import scan_trainers
        return scan_trainers(timeout=5)

    def sensor(self) -> PowerSource:
        return open_power_source(self.kind.currentData(), address=self.ble_devices.currentData(),
                                 device_number=self.ant_number.value())


STYLE = f"""
QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; }}
QToolBar {{ background: {PANEL}; border: none; padding: 4px; spacing: 6px; }}
QToolBar QToolButton {{ padding: 4px 10px; }}
QFrame#metric {{ background: {PANEL}; border-radius: 10px; }}
QFrame#metric QLabel {{ background: transparent; }}
QLabel#metricTitle {{ color: {MUTED}; font-size: 11px; font-weight: bold; letter-spacing: 1px; }}
QLabel#metricSub {{ color: {MUTED}; font-size: 12px; background: transparent; }}
QLabel#title {{ font-size: 18px; font-weight: bold; }}
QLabel#current {{ font-size: 16px; }}
QPushButton {{ background: #2d323c; border: none; border-radius: 6px; padding: 8px 16px; font-size: 14px; }}
QPushButton:hover {{ background: #3a404c; }}
QPushButton#play {{ background: {ACCENT}; color: #1a1a1a; font-weight: bold; min-width: 110px; }}
QPushButton#adjust {{ font-size: 18px; font-weight: bold; min-width: 70px; min-height: 44px; }}
QSpinBox {{ background: #2d323c; border: none; padding: 3px 6px; }}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="home-trainer-gui", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", help="séance .erg, .mrc, .zwo ou .fit")
    parser.add_argument("--ftp", type=float, default=DEFAULT_FTP)
    parser.add_argument("--bricks", help="séance en notation briques")
    parser.add_argument("--trainer", choices=["sim", "ble", "ant"], default="sim",
                        help="home trainer : simulé (défaut), Wahoo Bluetooth ou Wahoo ANT+")
    parser.add_argument("--trainer-address", help="adresse Bluetooth du home trainer (sinon le premier trouvé)")
    parser.add_argument("--trainer-ant-id", type=int, default=0,
                        help="numéro ANT+ du home trainer (sinon le premier trouvé)")
    parser.add_argument("--hr", choices=["sim", "ble", "ant", "aucun"], default="sim",
                        help="capteur cardio : simulé (défaut), Bluetooth, ANT+ ou aucun")
    parser.add_argument("--hr-address", help="adresse Bluetooth de la ceinture (sinon la première trouvée)")
    parser.add_argument("--hr-ant-id", type=int, default=0,
                        help="numéro ANT+ de la ceinture (sinon la première trouvée)")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Home trainer")
    app.setStyleSheet(STYLE)
    try:
        if args.file:
            workout = load_workout(args.file)
        else:
            workout = parse_workout(args.bricks or DEFAULT_BRICKS,
                                    name="Séance en briques" if args.bricks else "Sweet spot (démo)")
    except Exception as e:  # noqa: BLE001
        print(f"erreur : {e}", file=sys.stderr)
        return 1
    heart_rate = (None if args.hr == "aucun"
                  else open_heart_rate_sensor(args.hr, address=args.hr_address, device_number=args.hr_ant_id))
    source = open_power_source(args.trainer, address=args.trainer_address, device_number=args.trainer_ant_id)
    window = MainWindow(workout, args.ftp, source=source, heart_rate=heart_rate)
    window.resize(1100, 760)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
