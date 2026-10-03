"""Fenêtre « Profil » : choisir ou créer le cycliste qui roule (au lancement et depuis la barre d'outils).

Les changements (nom, FTP, poids, nouveau profil, suppression) ne sont gardés
qu'en validant ; « Annuler » laisse la liste telle qu'elle était.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout)

from ..profiles import DEFAULT_FTP, DEFAULT_WEIGHT_KG, Profile, ProfileBook

NEW_NAME = "Nouveau profil"


class ProfileDialog(QDialog):
    def __init__(self, book: ProfileBook, current: Profile | None = None, parent=None, *,
                 startup: bool = False) -> None:
        super().__init__(parent)
        self.book = book
        self.profile: Profile | None = None  # le profil choisi, une fois validé
        # Copies de travail : rien ne change dans `book` avant de valider.
        self.working = [replace(p) for p in book.profiles]
        self.setWindowTitle("Qui roule ?" if startup else "Profil")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        intro = QLabel("Chaque profil a sa FTP, son poids, ses comptes Strava / Nolio et ses sorties.")
        intro.setWordWrap(True)
        intro.setObjectName("metricSub")
        layout.addWidget(intro)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.new_button = QPushButton("Nouveau")
        self.new_button.clicked.connect(self.add_profile)
        self.delete_button = QPushButton("Supprimer")
        self.delete_button.clicked.connect(self._delete_clicked)
        row.addWidget(self.new_button)
        row.addWidget(self.delete_button)
        left.addLayout(row)
        body.addLayout(left, 1)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(self._name_edited)
        self.ftp_box = QSpinBox()
        self.ftp_box.setRange(50, 600)
        self.ftp_box.setSuffix(" W")
        self.ftp_box.valueChanged.connect(self._ftp_edited)
        self.weight_box = QDoubleSpinBox()
        self.weight_box.setRange(30, 200)
        self.weight_box.setDecimals(1)
        self.weight_box.setSingleStep(0.5)
        self.weight_box.setSuffix(" kg")
        self.weight_box.setLocale(QLocale(QLocale.French))
        self.weight_box.valueChanged.connect(self._weight_edited)
        form.addRow("Nom", self.name_edit)
        form.addRow("FTP", self.ftp_box)
        form.addRow("Poids", self.weight_box)
        body.addLayout(form, 1)
        layout.addLayout(body)

        self.remember_box = QCheckBox("Se souvenir de mon choix (ne plus demander au lancement)")
        self.remember_box.setChecked(book.remember)
        layout.addWidget(self.remember_box)

        buttons = QDialogButtonBox()
        self.ok_button = buttons.addButton("Rouler avec ce profil", QDialogButtonBox.AcceptRole)
        buttons.addButton("Quitter" if startup else "Annuler", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for p in self.working:
            self.list.addItem(QListWidgetItem(p.name))
        if not self.working:
            self.add_profile()  # premier lancement : on crée directement le premier profil
        else:
            ids = [p.id for p in self.working]
            wanted = current.id if current is not None else book.last
            self.list.setCurrentRow(ids.index(wanted) if wanted in ids else 0)

    # --- liste ---------------------------------------------------------------

    def selected(self) -> Profile | None:
        row = self.list.currentRow()
        return self.working[row] if 0 <= row < len(self.working) else None

    def add_profile(self) -> Profile:
        # Pas encore d'identifiant : il sera tiré du nom définitif en validant.
        profile = Profile("", NEW_NAME, DEFAULT_FTP, DEFAULT_WEIGHT_KG)
        self.working.append(profile)
        self.list.addItem(QListWidgetItem(profile.name))
        self.list.setCurrentRow(len(self.working) - 1)
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        return profile

    def delete_selected(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        del self.working[row]
        self.list.takeItem(row)
        if not self.working:
            self.add_profile()

    def _delete_clicked(self) -> None:
        profile = self.selected()
        if profile is None:
            return
        if profile.id:
            answer = QMessageBox.question(
                self, "Supprimer le profil",
                f"Supprimer le profil « {profile.name} » ?\n\nSes sorties et ses comptes restent sur "
                f"l'ordinateur, dans {self.book.folder(profile)}.")
            if answer != QMessageBox.Yes:
                return
        self.delete_selected()

    def _selected(self, row: int) -> None:
        profile = self.selected()
        enabled = profile is not None
        for widget in (self.name_edit, self.ftp_box, self.weight_box, self.delete_button):
            widget.setEnabled(enabled)
        if profile is None:
            return
        self.name_edit.setText(profile.name)
        self.ftp_box.setValue(profile.ftp)
        self.weight_box.setValue(profile.weight_kg)

    def _name_edited(self, text: str) -> None:
        profile = self.selected()
        if profile is not None:
            profile.name = text
            self.list.currentItem().setText(text.strip() or "(sans nom)")

    def _ftp_edited(self, value: int) -> None:
        if (profile := self.selected()) is not None:
            profile.ftp = value

    def _weight_edited(self, value: float) -> None:
        if (profile := self.selected()) is not None:
            profile.weight_kg = value

    # --- validation ----------------------------------------------------------

    def accept(self) -> None:
        profile = self.selected()
        if profile is None:
            return
        names = [p.name.strip().casefold() for p in self.working]
        for p in self.working:
            p.name = p.name.strip()
            if not p.name:
                self._refuse(p, "Donnez un nom à chaque profil.")
                return
            if names.count(p.name.casefold()) > 1:
                self._refuse(p, f"Deux profils s'appellent « {p.name} ».")
                return
        self.apply(profile)
        super().accept()

    def apply(self, profile: Profile) -> None:
        """Enregistre la liste de travail dans le carnet et retient `profile` comme choisi."""
        book = self.book
        book.profiles = [p for p in self.working if p.id]
        for p in self.working:  # les nouveaux après les anciens, pour ne pas leur prendre un identifiant
            if not p.id:
                p.id = book.new_id(p.name)
                book.profiles.append(p)
        book.profiles = list(self.working)  # dans l'ordre affiché
        book.last = profile.id
        book.remember = self.remember_box.isChecked()
        book.save()
        self.profile = profile

    def _refuse(self, profile: Profile, message: str) -> None:
        self.list.setCurrentRow(self.working.index(profile))
        QMessageBox.warning(self, "Profil", message)
        self.name_edit.setFocus()


def choose_profile(book: ProfileBook, current: Profile | None = None, parent=None, *,
                   startup: bool = False) -> Profile | None:
    dialog = ProfileDialog(book, current, parent, startup=startup)
    dialog.setWindowModality(Qt.ApplicationModal)
    return dialog.profile if dialog.exec() == QDialog.Accepted else None
