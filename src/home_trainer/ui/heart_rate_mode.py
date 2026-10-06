"""Fenêtre « Séance en fréquence cardiaque » : comment rouler une séance dont les cibles sont en FC.

* « Sans ERG » : le home trainer reste en résistance libre, la FC cible
  s'affiche et l'on règle l'effort avec les vitesses ;
* « En puissance » : les cibles de FC sont converties en % FTP d'après la
  FC max (voir `heart_zones`), et la séance se roule en ERG comme les autres.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QFormLayout, QLabel, QRadioButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ..heart_zones import DEFAULT_HR_MAX, MAX_HR_MAX, MIN_HR_MAX, ftp_percent_for


@dataclass(frozen=True)
class HeartRateChoice:
    to_power: bool  # True : cibles converties en puissance (ERG) ; False : sans ERG, aux vitesses
    hr_max: int


class HeartRateModeDialog(QDialog):
    def __init__(self, workout_name: str, hr_max: int | None, to_power: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Séance en fréquence cardiaque")
        self.setMinimumWidth(480)
        self.choice: HeartRateChoice | None = None
        layout = QVBoxLayout(self)
        intro = QLabel(f"Les cibles de « {workout_name} » sont données en fréquence cardiaque. "
                       "Comment voulez-vous la rouler ?")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.free_button = QRadioButton("Sans ERG : je tiens la FC cible avec les vitesses")
        self.power_button = QRadioButton("En puissance : convertir les zones de FC en watts (ERG)")
        group = QButtonGroup(self)
        for b in (self.free_button, self.power_button):
            group.addButton(b)
            layout.addWidget(b)
        (self.power_button if to_power else self.free_button).setChecked(True)

        form = QFormLayout()
        self.hr_max_box = QSpinBox()
        self.hr_max_box.setRange(MIN_HR_MAX, MAX_HR_MAX)
        self.hr_max_box.setSuffix(" bpm")
        self.hr_max_box.setValue(hr_max or DEFAULT_HR_MAX)
        form.addRow("FC max", self.hr_max_box)
        layout.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("metricSub")
        layout.addWidget(self.hint)

        group.buttonToggled.connect(self._update)
        self.hr_max_box.valueChanged.connect(self._update)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Rouler")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update()

    def _update(self, *_args) -> None:
        to_power = self.power_button.isChecked()
        self.hr_max_box.setEnabled(to_power)
        if to_power:
            hr_max = self.hr_max_box.value()
            examples = ", ".join(f"{bpm} bpm ≈ {ftp_percent_for(bpm, hr_max):.0f} % FTP"
                                 for bpm in (round(hr_max * 0.65), round(hr_max * 0.8), round(hr_max * 0.9)))
            self.hint.setText(f"FC au seuil estimée à 90 % de la FC max. Par exemple : {examples}. "
                              "C'est une estimation : ajustez au besoin avec l'intensité +/−.")
        else:
            self.hint.setText("Le home trainer reste en résistance libre ; la FC cible s'affiche à côté "
                              "de votre FC (capteur cardiaque conseillé).")

    def _accept(self) -> None:
        self.choice = HeartRateChoice(self.power_button.isChecked(), self.hr_max_box.value())
        self.accept()


def ask_heart_rate_mode(parent: QWidget | None, workout_name: str, hr_max: int | None,
                        to_power: bool = False) -> HeartRateChoice | None:
    """Fenêtre modale : le choix fait, ou None si l'utilisateur renonce à ouvrir la séance."""
    dialog = HeartRateModeDialog(workout_name, hr_max, to_power, parent)
    dialog.exec()
    return dialog.choice
