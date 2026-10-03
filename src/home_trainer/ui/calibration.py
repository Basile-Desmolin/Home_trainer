"""Assistant de calibration (spindown) du home trainer.

Le home trainer mesure le temps que met son volant à s'arrêter roue libre
depuis une vitesse donnée, et en déduit ses frottements : les watts mesurés
et imposés en ERG sont plus justes ensuite. L'assistant guide le cycliste
pas à pas : demande au home trainer, accélération jusqu'à la vitesse cible,
roue libre, résultat. Le déroulé réel est mené par le pilote
(`Trainer.start_calibration`, FTMS, protocole Wahoo ou ANT+ FE-C) ;
l'assistant ne fait qu'en afficher l'état.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from ..sensors.trainer import CalibrationPhase, CalibrationStatus
from .chart import ACCENT, MUTED, TEXT

INTRO = ("La calibration mesure les frottements du home trainer pour que les watts soient justes. "
         "Faites-la après une dizaine de minutes d'échauffement, quand le home trainer est chaud, "
         "et après l'avoir déplacé ou s'il fait très chaud ou très froid.")

STEPS = [
    ((CalibrationPhase.REQUESTED, CalibrationPhase.STARTING), "Demande au home trainer"),
    ((CalibrationPhase.SPEED_UP,), "Accélérez jusqu'à la vitesse indiquée"),
    ((CalibrationPhase.COAST,), "Arrêtez de pédaler, laissez la roue s'arrêter seule"),
    ((CalibrationPhase.DONE, CalibrationPhase.FAILED), "Résultat"),
]


class Calibratable(Protocol):
    def start_calibration(self) -> None: ...

    def cancel_calibration(self) -> None: ...

    def calibration_status(self) -> CalibrationStatus: ...


def can_calibrate(source) -> bool:
    return all(hasattr(source, name) for name in ("start_calibration", "cancel_calibration", "calibration_status"))


def describe(status: CalibrationStatus) -> str:
    """Résultat ou consigne du moment, en une phrase."""
    if status.phase is CalibrationPhase.DONE:
        text = "Calibration réussie."
        if status.spindown_ms:
            text += f" Roue libre en {status.spindown_ms / 1000:.1f} s".replace(".", ",")
            if status.temperature_c is not None:
                text += f", home trainer à {status.temperature_c:.0f} °C"
            text += "."
        return text + " Le home trainer garde ce réglage, vous pouvez reprendre votre séance."
    if status.phase is CalibrationPhase.FAILED:
        return f"Calibration non faite : {status.message}."
    if status.phase is CalibrationPhase.IDLE:
        return "Cliquez « Démarrer » quand vous êtes prêt."
    message = status.message or status.phase.value
    return message[:1].upper() + message[1:] + "."


def speed_text(status: CalibrationStatus) -> str:
    speed = "—" if status.speed_kmh is None else f"{status.speed_kmh:.1f} km/h".replace(".", ",")
    if status.target_kmh and status.target_high_kmh:
        return f"{speed}   (entre {status.target_kmh:.0f} et {status.target_high_kmh:.0f} km/h)"
    if status.target_kmh:
        return f"{speed}   (cible {status.target_kmh:.0f} km/h)"
    return speed


class CalibrationDialog(QDialog):
    """Assistant guidé ; `source` est le home trainer en service (réel ou simulé)."""

    def __init__(self, source: Calibratable, name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = source
        self.setWindowTitle("Calibration du home trainer")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        intro = QLabel(INTRO + (f"\n\nHome trainer : {name}" if name else ""))
        intro.setWordWrap(True)
        intro.setObjectName("metricSub")
        layout.addWidget(intro)
        self.step_labels = []
        for _phases, text in STEPS:
            label = QLabel()
            self.step_labels.append((label, text))
            layout.addWidget(label)
        self.instruction = QLabel()
        self.instruction.setWordWrap(True)
        self.instruction.setObjectName("current")
        layout.addWidget(self.instruction)
        self.speed = QLabel()
        layout.addWidget(self.speed)
        self.gauge = QProgressBar()
        self.gauge.setTextVisible(False)
        self.gauge.setRange(0, 1000)
        self.gauge.setStyleSheet(f"QProgressBar {{ background: #2d323c; border: none; border-radius: 4px; height: 10px; }}"
                                 f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}")
        layout.addWidget(self.gauge)
        buttons = QDialogButtonBox()
        self.start_button = QPushButton("Démarrer")
        self.start_button.setObjectName("play")
        self.start_button.clicked.connect(self.start)
        buttons.addButton(self.start_button, QDialogButtonBox.ActionRole)
        self.close_button = buttons.addButton("Fermer", QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(200)
        self.refresh()

    def start(self) -> None:
        self.source.start_calibration()
        self.refresh()

    def reject(self) -> None:
        if self.source.calibration_status().active:
            self.source.cancel_calibration()
        self.timer.stop()
        super().reject()

    def refresh(self) -> None:
        status = self.source.calibration_status()
        for (phases, _), (label, text) in zip(STEPS, self.step_labels):
            current = status.phase in phases
            color = ACCENT if current else (TEXT if status.phase is not CalibrationPhase.IDLE else MUTED)
            label.setText(f"{'▶' if current else '  '}  {text}")
            label.setStyleSheet(f"color: {color}; font-weight: {'bold' if current else 'normal'};")
        self.instruction.setText(describe(status))
        self.speed.setText(f"Vitesse : {speed_text(status)}")
        top = (status.target_high_kmh or status.target_kmh or 0) * 1.15
        speed = status.speed_kmh or 0
        self.gauge.setValue(round(min(1.0, speed / top) * 1000) if top else 0)
        self.start_button.setEnabled(not status.active)
        self.start_button.setVisible(not status.active)
        self.start_button.setText("Recommencer" if status.finished else "Démarrer")
        self.close_button.setText("Annuler" if status.active else "Fermer")
