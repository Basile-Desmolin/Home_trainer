"""Fenêtre « Enregistrer la sortie » à la fin d'une sortie : son bilan, puis format, nom et dossier.

« Enregistrer » écrit le fichier ; « Ne pas enregistrer » abandonne la sortie
(après confirmation) ; « Annuler » ramène à la sortie, rien n'est perdu.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..formats.activity_export import RIDE_FORMATS
from ..ride_stats import RideSummary
from .ride_summary import SummaryPanel

DISCARD = "discard"  # réponse « Ne pas enregistrer »
FORBIDDEN = '<>:"/\\|?*'  # caractères refusés dans un nom de fichier sous Windows


@dataclass(frozen=True)
class RideExport:
    folder: Path
    name: str  # sans extension
    fmt: str = "fit"

    @property
    def path(self) -> Path:
        return Path(self.folder) / f"{self.name}.{self.fmt}"


def clean_name(text: str) -> str:
    """Le nom tapé, sans extension connue ni caractère interdit sous Windows."""
    name = text.strip()
    for fmt in RIDE_FORMATS:
        if name.lower().endswith("." + fmt):
            name = name[: -len(fmt) - 1]
    name = "".join("-" if c in FORBIDDEN or ord(c) < 32 else c for c in name)
    return name.strip(" .")


class RideExportDialog(QDialog):
    def __init__(self, default: RideExport, summary: str | RideSummary = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enregistrer la sortie")
        self.setMinimumWidth(620)
        self.choice: RideExport | str | None = None
        layout = QVBoxLayout(self)
        self.panel: SummaryPanel | None = None
        if isinstance(summary, RideSummary):  # bilan de la sortie au-dessus du choix du fichier
            self.panel = SummaryPanel(summary)
            layout.addWidget(self.panel)
            layout.addSpacing(8)
        elif summary:
            label = QLabel(summary)
            label.setWordWrap(True)
            layout.addWidget(label)

        form = QFormLayout()
        self.format_box = QComboBox()
        for fmt, label in RIDE_FORMATS.items():
            self.format_box.addItem(label, fmt)
        self.format_box.setCurrentIndex(max(self.format_box.findData(default.fmt), 0))
        self.format_box.currentIndexChanged.connect(self._update_hint)
        form.addRow("Format", self.format_box)
        self.name_edit = QLineEdit(default.name)
        form.addRow("Nom", self.name_edit)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(str(default.folder))
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        form.addRow("Dossier", folder_row)
        layout.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("metricSub")
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("Enregistrer", QDialogButtonBox.AcceptRole)
        self.discard_button = buttons.addButton("Ne pas enregistrer", QDialogButtonBox.DestructiveRole)
        buttons.addButton("Annuler", QDialogButtonBox.RejectRole)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        self.discard_button.clicked.connect(self._discard)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_hint()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def current(self) -> RideExport:
        return RideExport(Path(self.folder_edit.text().strip()), clean_name(self.name_edit.text()),
                          self.format_box.currentData())

    def _update_hint(self) -> None:
        if self.format_box.currentData() == "fit":
            self.hint.setText("Le .fit part aussi vers Strava / Nolio si un compte est connecté.")
        else:
            self.hint.setText("Strava / Nolio reçoivent toujours un .fit : une copie .fit est gardée "
                              "dans le dossier des sorties du profil et envoyée de là.")

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier de sauvegarde", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _save(self) -> None:
        choice = self.current()
        if not choice.name:
            QMessageBox.warning(self, "Nom manquant", "Donnez un nom au fichier.")
            return
        if not str(choice.folder).strip() or str(choice.folder) == ".":
            QMessageBox.warning(self, "Dossier manquant", "Choisissez le dossier de sauvegarde.")
            return
        if choice.path.exists() and QMessageBox.question(
                self, "Fichier existant", f"{choice.path.name} existe déjà dans ce dossier. Le remplacer ?"
        ) != QMessageBox.Yes:
            return
        self.choice = choice
        self.accept()

    def _discard(self) -> None:
        if QMessageBox.question(self, "Ne pas enregistrer",
                                "La sortie ne sera ni enregistrée ni envoyée. Continuer ?") == QMessageBox.Yes:
            self.choice = DISCARD
            self.accept()


def ask_ride_export(parent: QWidget | None, default: RideExport, summary: str | RideSummary = "") -> RideExport | str | None:
    """Fenêtre modale : un `RideExport`, `DISCARD`, ou None si l'utilisateur revient à la sortie."""
    dialog = RideExportDialog(default, summary, parent)
    dialog.exec()
    return dialog.choice

