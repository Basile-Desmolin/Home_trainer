"""Fenêtre « Bibliothèque » : les séances d'un dossier (et de ses sous-dossiers), avec recherche,
filtre de durée, aperçu du profil ; double-clic ou « Rouler cette séance » pour la charger."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPainter, QPainterPath, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QStyledItemDelegate, QStyle, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..library import Library, LibraryEntry, fold_text
from .chart import MUTED, WorkoutChart, hms, zone_color
from .session import WorkoutSession

HEADERS = ["Nom", "Durée", "Profil", "TSS", "Dossier", "Format"]
COL_NAME, COL_DURATION, COL_PROFILE, COL_TSS, COL_FOLDER, COL_FORMAT = range(len(HEADERS))
SELECTED = "#3a404c"  # QTreeWidget::item:selected de la feuille de style

# Filtre de durée : (libellé, minimum, maximum) en minutes.
DURATIONS = [("Toutes durées", 0, None), ("Moins de 45 min", 0, 45), ("45 min à 1 h 15", 45, 75),
             ("1 h 15 à 2 h", 75, 120), ("Plus de 2 h", 120, None)]


def documents_folder() -> str | None:
    """Dossier Documents de l'utilisateur (redirigé vers OneDrive compris)."""
    return QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or None


class EntryItem(QTreeWidgetItem):
    """Ligne de la liste ; le tri suit les valeurs (durée, TSS) et non le texte affiché."""

    def __init__(self, entry: LibraryEntry, ftp: float) -> None:
        tss = entry.stress_score(ftp)
        super().__init__([entry.name, hms(entry.duration_s) + (" +" if entry.workout.has_open_steps else ""),
                          "", "—" if tss is None else f"{tss:.0f}", entry.folder or "—", entry.format])
        self.entry = entry
        self.keys = {COL_NAME: fold_text(entry.name), COL_DURATION: entry.duration_s, COL_TSS: tss or 0,
                     COL_FOLDER: fold_text(entry.folder), COL_FORMAT: entry.format}
        self.segments = entry.workout.timeline(ftp)
        self.ftp = ftp
        tip = str(entry.path)
        if entry.workout.description:
            tip += "\n\n" + entry.workout.description
        if entry.workout.has_open_steps:
            tip += "\n\n+ : briques « jusqu'au tour » en plus de la durée affichée"
        for col in range(len(HEADERS)):
            self.setToolTip(col, tip)
        for col in (COL_DURATION, COL_TSS):
            self.setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        col = self.treeWidget().sortColumn() if self.treeWidget() else COL_NAME
        if isinstance(other, EntryItem) and col in self.keys:
            return (self.keys[col], self.keys[COL_NAME]) < (other.keys[col], other.keys[COL_NAME])
        return super().__lt__(other)


class ProfileDelegate(QStyledItemDelegate):
    """Petit profil de la séance dans la colonne « Profil » (même échelle de temps pour toutes les lignes)."""

    def __init__(self, dialog: LibraryDialog) -> None:
        super().__init__(dialog.tree)
        self.dialog = dialog

    def paint(self, painter: QPainter, option, index) -> None:
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(SELECTED))  # même teinte que les autres colonnes
        item = self.dialog.tree.itemFromIndex(index)
        if not isinstance(item, EntryItem):
            return
        rect = QRectF(option.rect).adjusted(4, 4, -4, -3)
        longest = max(self.dialog.longest_s, 1.0)
        peak = max([seg.high_w or 0 for seg in item.segments]
                   + [seg.end_high_w or 0 for seg in item.segments] + [item.ftp * 1.2])
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        for seg in item.segments:
            if not seg.duration_s:
                continue
            x0 = rect.left() + seg.start_s / longest * rect.width()
            x1 = rect.left() + (seg.start_s + seg.duration_s) / longest * rect.width()
            w0 = max(seg.target_w(0) or 0, peak * 0.05)
            w1 = max(seg.target_w(seg.duration_s) or 0, peak * 0.05)
            path = QPainterPath(QPointF(x0, rect.bottom()))
            path.lineTo(x0, rect.bottom() - w0 / peak * rect.height())
            path.lineTo(x1, rect.bottom() - w1 / peak * rect.height())
            path.lineTo(x1, rect.bottom())
            path.closeSubpath()
            painter.fillPath(path, zone_color(seg.target_w(seg.duration_s / 2), item.ftp))
        painter.restore()


class LibraryDialog(QDialog):
    """Choix d'une séance dans la bibliothèque ; `chosen_path()` après acceptation."""

    def __init__(self, library: Library, ftp: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = library
        self.ftp = ftp
        self.longest_s = 0.0
        self._chosen: Path | None = None
        self.setWindowTitle("Bibliothèque des séances")
        self.resize(1000, 700)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Dossier"))
        self.folder_label = QLabel()
        self.folder_label.setObjectName("metricSub")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.folder_label, 1)
        for text, slot, tip in (("Changer…", self._change_folder, "Choisir le dossier des séances (mémorisé)"),
                                ("Ouvrir le dossier", self._show_folder,
                                 "Ouvrir le dossier dans l'explorateur, pour y copier des fichiers"),
                                ("Actualiser", self.refresh, "Relire le dossier (F5)")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            top.addWidget(b)
        layout.addLayout(top)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher : nom, dossier, description… (ex. « vo2 30 »)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        filters.addWidget(self.search, 1)
        self.duration_box = QComboBox()
        for label, low, high in DURATIONS:
            self.duration_box.addItem(label, (low, high))
        self.duration_box.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self.duration_box)
        layout.addLayout(filters)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(COL_FOLDER, Qt.AscendingOrder)
        self.tree.setItemDelegateForColumn(COL_PROFILE, ProfileDelegate(self))
        header = self.tree.header()
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_PROFILE, QHeaderView.Fixed)
        self.tree.setColumnWidth(COL_PROFILE, 220)
        for col in (COL_DURATION, COL_TSS, COL_FOLDER, COL_FORMAT):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemActivated.connect(lambda *_: self._ride())  # double-clic ou Entrée
        layout.addWidget(self.tree, 3)

        self.empty = QLabel()
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("metricSub")
        layout.addWidget(self.empty)

        self.chart = WorkoutChart(preview=True)
        layout.addWidget(self.chart, 2)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)

        bottom = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setObjectName("metricSub")
        bottom.addWidget(self.count_label, 1)
        close_button = QPushButton("Fermer")
        close_button.setAutoDefault(False)
        close_button.clicked.connect(self.reject)
        self.ride_button = QPushButton("Rouler cette séance")
        self.ride_button.setObjectName("play")
        self.ride_button.setAutoDefault(False)
        self.ride_button.clicked.connect(self._ride)
        bottom.addWidget(close_button)
        bottom.addWidget(self.ride_button)
        layout.addLayout(bottom)

        QShortcut(QKeySequence.Refresh, self, activated=self.refresh)
        QShortcut(QKeySequence.Find, self, activated=self.search.setFocus)
        self.refresh()
        self.search.setFocus()

    # --- contenu ---------------------------------------------------------

    def refresh(self) -> None:
        selected = self.selected_entry()
        keep = selected.path if selected else None
        self.library.ensure_folder()
        self.folder_label.setText(str(self.library.folder))
        result = self.library.scan()
        self.errors = result.errors
        self.longest_s = max((e.duration_s for e in result.entries), default=0.0)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for entry in result.entries:
            item = EntryItem(entry, self.ftp)
            self.tree.addTopLevelItem(item)
            if entry.path == keep:
                item.setSelected(True)
        self.tree.setSortingEnabled(True)
        self._apply_filter()

    def items(self) -> list[EntryItem]:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def visible_entries(self) -> list[LibraryEntry]:
        return [item.entry for item in self.items() if not item.isHidden()]

    def _apply_filter(self) -> None:
        query = self.search.text()
        low, high = self.duration_box.currentData() or (0, None)
        for item in self.items():
            minutes = item.entry.duration_s / 60
            fits = minutes >= low and (high is None or minutes < high)
            item.setHidden(not (fits and item.entry.matches(query)))
        visible = [item for item in self.items() if not item.isHidden()]
        if visible and not any(item.isSelected() for item in visible):
            self.tree.setCurrentItem(visible[0])
        total = len(self.items())
        if total == 0:
            self.empty.setText("Aucune séance dans ce dossier pour l'instant. Les séances enregistrées depuis "
                               "l'éditeur arrivent ici ; vous pouvez aussi y copier vos fichiers .zwo, .mrc, "
                               ".erg ou .fit (sous-dossiers compris).")
        elif not visible:
            self.empty.setText("Aucune séance ne correspond à la recherche.")
        self.empty.setVisible(not visible)
        count = f"{len(visible)} séance(s)" if len(visible) == total else f"{len(visible)} séance(s) sur {total}"
        if self.errors:
            count += f" · {len(self.errors)} fichier(s) ignoré(s)"
            self.count_label.setToolTip("\n".join(f"{p.relative_to(self.library.folder)} : {why}"
                                                  for p, why in self.errors))
        else:
            self.count_label.setToolTip("")
        self.count_label.setText(count)
        self._selection_changed()

    def selected_entry(self) -> LibraryEntry | None:
        items = [item for item in self.tree.selectedItems() if not item.isHidden()]
        return items[0].entry if items and isinstance(items[0], EntryItem) else None

    def _selection_changed(self) -> None:
        entry = self.selected_entry()
        self.ride_button.setEnabled(entry is not None)
        if entry is None:
            self.chart.session = None
            self.chart.update()
            self.details.setText("")
            return
        self.chart.set_session(WorkoutSession(entry.workout, self.ftp))
        text = f"<b>{_escape(entry.name)}</b> · {hms(entry.duration_s)}"
        if entry.workout.description:
            text += f"<br><span style='color:{MUTED}'>{_escape(entry.workout.description)}</span>"
        self.details.setText(text)

    # --- actions ---------------------------------------------------------

    def select_path(self, path: Path | str) -> bool:
        for item in self.items():
            if item.entry.path == Path(path):
                self.tree.setCurrentItem(item)
                return True
        return False

    def chosen_path(self) -> Path | None:
        return self._chosen

    def _ride(self) -> None:
        entry = self.selected_entry()
        if entry is not None:
            self._chosen = entry.path
            self.accept()

    def _change_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier des séances", str(self.library.folder))
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder: Path | str) -> None:
        self.library.set_folder(folder)
        self.library.save()
        self.refresh()

    def _show_folder(self) -> None:
        if self.library.ensure_folder():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.folder)))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
