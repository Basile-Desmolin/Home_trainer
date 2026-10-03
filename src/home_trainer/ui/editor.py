"""Éditeur graphique de séance : briques « x min à y W ou % FTP », répétitions, rampes.

Tableau des briques (double-clic pour modifier), aperçu du profil, notation
texte synchronisée, et enregistrement en .zwo, .mrc, .erg ou .fit.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QStyledItemDelegate,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..bricks import BrickSyntaxError, format_bricks, parse_bricks
from ..formats import FormatError, save_workout
from ..workout import Item, PowerUnit, Repeat, Step, Workout
from .chart import MUTED, WorkoutChart, hms
from .editing import (UNIT_LABELS, StepFields, convert_fields, fields_from_step, format_count, parse_count_field,
                      step_from_fields)
from .session import WorkoutSession

COL_DURATION, COL_POWER, COL_END, COL_UNIT, COL_NAME = range(5)
HEADERS = ["Durée", "Puissance", "Fin de rampe", "Unité", "Nom"]
ERROR = "#e5484d"
REPEAT_ROLE = Qt.UserRole + 1
SAVE_FILTERS = {
    "Zwift (*.zwo)": ".zwo",
    "MRC, en % FTP (*.mrc)": ".mrc",
    "ERG, en watts (*.erg)": ".erg",
    "FIT, Garmin (*.fit)": ".fit",
}


class UnitDelegate(QStyledItemDelegate):
    """Choix W / % FTP dans une liste plutôt qu'au clavier."""

    def createEditor(self, parent, _option, index):  # noqa: N802 (API Qt)
        if index.data(REPEAT_ROLE):
            return None
        box = QComboBox(parent)
        box.addItems(list(UNIT_LABELS.values()))
        return box

    def setEditorData(self, editor, index):  # noqa: N802
        editor.setCurrentText(index.data())
        editor.showPopup()

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.currentText())


class WorkoutEditor(QDialog):
    """Compose ou modifie une séance. `workout()` donne le résultat après « Rouler cette séance »."""

    def __init__(self, workout: Workout | None, ftp: float, parent: QWidget | None = None,
                 directory: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Éditeur de séance")
        self.resize(980, 720)
        self.ftp = ftp
        self.directory = directory or str(Path.home())
        self.saved_path: str | None = None
        self._workout: Workout | None = None
        self._steps_by_item: dict[int, Step] = {}
        self._updating = False

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Nom"))
        self.name_edit = QLineEdit()
        top.addWidget(self.name_edit, 1)
        top.addWidget(QLabel("  FTP"))
        self.ftp_box = QSpinBox()
        self.ftp_box.setRange(50, 600)
        self.ftp_box.setSuffix(" W")
        self.ftp_box.setValue(int(ftp))
        self.ftp_box.valueChanged.connect(self._ftp_changed)
        top.addWidget(self.ftp_box)
        top.addWidget(QLabel("  Nouvelles briques en"))
        self.unit_box = QComboBox()
        for unit, label in UNIT_LABELS.items():
            self.unit_box.addItem(label, unit)
        top.addWidget(self.unit_box)
        layout.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                  | QAbstractItemView.AnyKeyPressed)
        self.tree.setItemDelegateForColumn(COL_UNIT, UnitDelegate(self.tree))
        header = self.tree.header()
        for col in range(COL_NAME):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
            self.tree.setColumnWidth(col, 110)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.tree, 3)

        buttons = QHBoxLayout()
        for text, slot, tip in (
            ("+ Brique", self._add_step, "Ajoute une brique après la sélection (copie de la brique choisie)"),
            ("+ Répétition", self._add_repeat, "Répète la brique choisie (ou ajoute un bloc 3 × 2 briques)"),
            ("Dupliquer", self._duplicate, "Copie la brique ou la répétition choisie"),
            ("Monter", lambda: self._move(-1), "Monte la brique (entre ou sort d'une répétition)"),
            ("Descendre", lambda: self._move(+1), "Descend la brique (entre ou sort d'une répétition)"),
            ("Supprimer", self._delete, "Supprime la brique ou la répétition choisie"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        QShortcut(QKeySequence.Delete, self.tree, activated=self._delete)

        notation = QHBoxLayout()
        notation.addWidget(QLabel("Notation"))
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("ex. 10m@50%>75% 3x(8m@90% 3m@55%) 10m@120")
        self.text_edit.setToolTip("Écrire les briques en texte puis Entrée : 10m@150, 20m@88%, 10m@100>200, "
                                  "4x(4m@105% 2m@55%), open@120")
        self.text_edit.returnPressed.connect(self._apply_text)
        notation.addWidget(self.text_edit, 1)
        apply_button = QPushButton("Appliquer")
        apply_button.clicked.connect(self._apply_text)
        notation.addWidget(apply_button)
        layout.addLayout(notation)

        self.chart = WorkoutChart(preview=True)
        layout.addWidget(self.chart, 2)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        self.save_button = QPushButton("Enregistrer sous…")
        self.save_button.clicked.connect(self.save_as)
        self.ride_button = QPushButton("Rouler cette séance")
        self.ride_button.setObjectName("play")
        self.ride_button.setDefault(False)
        self.ride_button.setAutoDefault(False)
        self.ride_button.clicked.connect(self._ride)
        close_button = QPushButton("Fermer")
        close_button.setAutoDefault(False)
        close_button.clicked.connect(self.reject)
        self.save_button.setAutoDefault(False)
        bottom.addWidget(self.save_button)
        bottom.addStretch(1)
        bottom.addWidget(close_button)
        bottom.addWidget(self.ride_button)
        layout.addLayout(bottom)

        if workout is None:
            workout = Workout(name="Nouvelle séance", steps=parse_bricks("10m@50%>70% 20m@88% 10m@60%>45%"))
        if workout.uses_ftp_percent and not workout.uses_watts:
            self.unit_box.setCurrentIndex(self.unit_box.findData(PowerUnit.FTP_PERCENT))
        self.name_edit.setText(workout.name)
        self.set_items(workout.steps)

    # --- tableau <-> séance ----------------------------------------------

    def set_items(self, items: list[Item]) -> None:
        self._updating = True
        self.tree.clear()
        for item in items:
            self.tree.addTopLevelItem(self._make_item(item))
        self.tree.expandAll()
        self._updating = False
        self._rebuild()

    def _make_item(self, item: Item) -> QTreeWidgetItem:
        node = QTreeWidgetItem()
        node.setFlags(node.flags() | Qt.ItemIsEditable)
        if isinstance(item, Repeat):
            node.setData(COL_DURATION, REPEAT_ROLE, True)
            node.setText(COL_DURATION, format_count(item.count))
            node.setToolTip(COL_DURATION, "Nombre de répétitions (double-clic pour changer)")
            for child in item.steps:
                node.addChild(self._make_item(child))
            node.setExpanded(True)
        else:
            self._set_fields(node, fields_from_step(item))
        return node

    def _set_fields(self, node: QTreeWidgetItem, f: StepFields) -> None:
        node.setText(COL_DURATION, f.duration)
        node.setText(COL_POWER, f.power)
        node.setText(COL_END, f.power_end)
        node.setText(COL_UNIT, UNIT_LABELS[f.unit])
        node.setText(COL_NAME, f.name)

    @staticmethod
    def _is_repeat(node: QTreeWidgetItem | None) -> bool:
        return bool(node is not None and node.data(COL_DURATION, REPEAT_ROLE))

    @staticmethod
    def _fields(node: QTreeWidgetItem) -> StepFields:
        unit = next((u for u, label in UNIT_LABELS.items() if label == node.text(COL_UNIT)), PowerUnit.WATTS)
        return StepFields(node.text(COL_DURATION), node.text(COL_POWER), node.text(COL_END), unit,
                          node.text(COL_NAME))

    def _read_node(self, node: QTreeWidgetItem, errors: list[str]) -> Item | None:
        self._mark(node, None)
        if self._is_repeat(node):
            children = [c for c in (self._read_node(node.child(i), errors) for i in range(node.childCount()))
                        if c is not None]
            try:
                count = parse_count_field(node.text(COL_DURATION))
            except BrickSyntaxError as e:
                self._mark(node, str(e))
                errors.append(str(e))
                return None
            if not children:
                return None
            total = sum(s.duration_s or 0 for s in Workout(steps=children).flatten()) * count
            node.setText(COL_NAME, f"répétition · {hms(total)} au total")
            return Repeat(count, children)
        try:
            step = step_from_fields(self._fields(node))
        except BrickSyntaxError as e:
            self._mark(node, str(e))
            errors.append(str(e))
            return None
        self._steps_by_item[id(node)] = step
        return step

    def _mark(self, node: QTreeWidgetItem, error: str | None) -> None:
        brush = QBrush(QColor(ERROR)) if error else QBrush()
        for col in range(len(HEADERS)):
            node.setBackground(col, brush)
            if col != COL_DURATION or not self._is_repeat(node):
                node.setToolTip(col, error or "")

    def _rebuild(self) -> None:
        """Relit le tableau : séance, aperçu, notation texte et messages."""
        if self._updating:
            return
        self._updating = True
        errors: list[str] = []
        self._steps_by_item = {}
        items = [i for i in (self._read_node(self.tree.topLevelItem(n), errors)
                             for n in range(self.tree.topLevelItemCount())) if i is not None]
        self._updating = False
        workout = Workout(name=self.name_edit.text().strip() or "Séance", steps=items)
        self._workout = None if errors or not items else workout
        if not self.text_edit.hasFocus():
            self.text_edit.setText(format_bricks(items))
        session = WorkoutSession(workout, self.ftp)
        self.chart.set_session(session)
        self._selection_changed()
        if errors:
            self.status.setStyleSheet(f"color: {ERROR};")
            self.status.setText(f"À corriger (cases en rouge) : {errors[0]}")
        elif not items:
            self.status.setStyleSheet(f"color: {MUTED};")
            self.status.setText("Séance vide : ajoutez une brique.")
        else:
            self.status.setStyleSheet(f"color: {MUTED};")
            steps = list(workout.flatten())
            self.status.setText(f"{len(steps)} briques · {hms(workout.total_duration_s)}"
                                + ("" if not workout.has_open_steps else " + briques « tour »")
                                + f" · travail ≈ {self._work_kj(session):.0f} kJ")
        self.save_button.setEnabled(self._workout is not None)
        self.ride_button.setEnabled(self._workout is not None)

    @staticmethod
    def _work_kj(session: WorkoutSession) -> float:
        return sum(((seg.target_w(0) or 0) + (seg.target_w(seg.duration_s) or 0)) / 2 * seg.duration_s
                   for seg in session.segments if seg.duration_s) / 1000

    def _item_changed(self, node: QTreeWidgetItem, column: int) -> None:
        if self._updating:
            return
        if column == COL_UNIT and not self._is_repeat(node):
            # Changer d'unité convertit les valeurs avec la FTP : 150 W ↔ 60 % à 250 W.
            new_unit = next((u for u, label in UNIT_LABELS.items() if label == node.text(COL_UNIT)), None)
            old = self._steps_by_item.get(id(node))
            if new_unit is not None and old is not None and old.power is not None and old.power.unit is not new_unit:
                self._updating = True
                f = fields_from_step(old)
                f.name = node.text(COL_NAME)
                self._set_fields(node, convert_fields(f, new_unit, self.ftp))
                self._updating = False
        self._rebuild()

    def _selection_changed(self) -> None:
        node = self._selected()
        steps: list[Step] = []

        def collect(n: QTreeWidgetItem) -> None:
            if id(n) in self._steps_by_item:
                steps.append(self._steps_by_item[id(n)])
            for i in range(n.childCount()):
                collect(n.child(i))

        if node is not None:
            collect(node)
        self.chart.highlight = steps
        self.chart.update()

    def _apply_text(self) -> None:
        text = self.text_edit.text()
        try:
            items = parse_bricks(text)
        except BrickSyntaxError as e:
            self.status.setStyleSheet(f"color: {ERROR};")
            self.status.setText(f"Notation invalide : {e}")
            return
        self.tree.setFocus()
        self.set_items(items)

    def _ftp_changed(self, value: int) -> None:
        self.ftp = value
        self._rebuild()

    # --- boutons ---------------------------------------------------------

    def _selected(self) -> QTreeWidgetItem | None:
        items = self.tree.selectedItems()
        return items[0] if items else None

    def _siblings(self, node: QTreeWidgetItem) -> tuple[QTreeWidgetItem | None, int]:
        parent = node.parent()
        index = parent.indexOfChild(node) if parent else self.tree.indexOfTopLevelItem(node)
        return parent, index

    def _insert(self, parent: QTreeWidgetItem | None, index: int, node: QTreeWidgetItem) -> None:
        if parent is None:
            self.tree.insertTopLevelItem(index, node)
        else:
            parent.insertChild(index, node)

    def _take(self, node: QTreeWidgetItem) -> None:
        parent, index = self._siblings(node)
        if parent is None:
            self.tree.takeTopLevelItem(index)
        else:
            parent.takeChild(index)

    def _new_step_fields(self) -> StepFields:
        node = self._selected()
        if node is not None and not self._is_repeat(node):
            return self._fields(node)
        unit = self.unit_box.currentData()
        return StepFields("5:00", "150" if unit is PowerUnit.WATTS else "60", "", unit, "")

    def _place(self, node: QTreeWidgetItem) -> None:
        """Insère après la sélection (dans la répétition si une répétition est choisie), puis sélectionne."""
        selected = self._selected()
        self._updating = True
        if selected is None:
            self.tree.addTopLevelItem(node)
        elif self._is_repeat(selected) and not self._is_repeat(node):
            selected.addChild(node)
            selected.setExpanded(True)
        else:
            parent, index = self._siblings(selected)
            self._insert(parent, index + 1, node)
        self._expand(node)
        self._updating = False
        self.tree.setCurrentItem(node)
        self._rebuild()

    def _expand(self, node: QTreeWidgetItem) -> None:
        node.setExpanded(True)
        for i in range(node.childCount()):
            self._expand(node.child(i))

    def _add_step(self) -> None:
        node = QTreeWidgetItem()
        node.setFlags(node.flags() | Qt.ItemIsEditable)
        self._set_fields(node, self._new_step_fields())
        self._place(node)
        self.tree.editItem(node, COL_DURATION)

    def _add_repeat(self) -> None:
        selected = self._selected()
        if selected is not None and not self._is_repeat(selected):
            # La brique choisie devient le contenu d'une répétition 3 ×.
            self._updating = True
            parent, index = self._siblings(selected)
            self._take(selected)
            repeat = self._make_item(Repeat(3, [Step(60)]))
            repeat.takeChild(0)
            repeat.addChild(selected)
            self._insert(parent, index, repeat)
            self._expand(repeat)
            self._updating = False
            self.tree.setCurrentItem(repeat)
            self._rebuild()
            return
        unit = self.unit_box.currentData()
        text = "3x(4m@105% 2m@55%)" if unit is PowerUnit.FTP_PERCENT else "3x(4m@260 2m@140)"
        self._place(self._make_item(parse_bricks(text)[0]))

    def _duplicate(self) -> None:
        node = self._selected()
        if node is not None:
            self._place(node.clone())

    def _delete(self) -> None:
        node = self._selected()
        if node is None:
            return
        parent, index = self._siblings(node)
        self._updating = True
        self._take(node)
        self._updating = False
        count = parent.childCount() if parent else self.tree.topLevelItemCount()
        if count:
            nxt = parent.child(min(index, count - 1)) if parent else self.tree.topLevelItem(min(index, count - 1))
            self.tree.setCurrentItem(nxt)
        elif parent is not None:
            self.tree.setCurrentItem(parent)
        self._rebuild()

    def _move(self, direction: int) -> None:
        """Monte ou descend ; une brique entre dans la répétition voisine ou en sort au bord."""
        node = self._selected()
        if node is None:
            return
        parent, index = self._siblings(node)
        count = parent.childCount() if parent else self.tree.topLevelItemCount()
        neighbour_index = index + direction
        self._updating = True
        self._take(node)
        if 0 <= neighbour_index < count:
            neighbour = parent.child(neighbour_index - (direction > 0)) if parent else \
                self.tree.topLevelItem(neighbour_index - (direction > 0))
            if self._is_repeat(neighbour) and not self._is_repeat(node):
                neighbour.insertChild(neighbour.childCount() if direction < 0 else 0, node)
                neighbour.setExpanded(True)
            else:
                self._insert(parent, neighbour_index, node)
        elif parent is not None:
            grand, parent_index = self._siblings(parent)
            self._insert(grand, parent_index + (direction > 0), node)
        else:
            self._insert(None, index, node)
        self._expand(node)
        self._updating = False
        self.tree.setCurrentItem(node)
        self._rebuild()

    # --- sorties ---------------------------------------------------------

    def workout(self) -> Workout | None:
        """Séance valide décrite par l'éditeur (None s'il reste des erreurs)."""
        self._rebuild()
        if self._workout is not None:
            self._workout.name = self.name_edit.text().strip() or "Séance"
        return self._workout

    def save_as(self) -> str | None:
        workout = self.workout()
        if workout is None:
            return None
        default = Path(self.saved_path or Path(self.directory) / _file_name(workout.name))
        path, chosen = QFileDialog.getSaveFileName(self, "Enregistrer la séance", str(default.with_suffix("")),
                                                   ";;".join(SAVE_FILTERS), _filter_for(default.suffix))
        if not path:
            return None
        if Path(path).suffix.lower() not in SAVE_FILTERS.values():
            path += SAVE_FILTERS.get(chosen, ".zwo")
        try:
            save_workout(workout, path, ftp=self.ftp)
        except (FormatError, ValueError, OSError) as e:
            QMessageBox.warning(self, "Enregistrement impossible", f"{Path(path).name} : {e}")
            return None
        self.saved_path = path
        self.directory = str(Path(path).parent)
        self.status.setStyleSheet(f"color: {MUTED};")
        self.status.setText(f"Enregistrée : {path}" + _losses(workout, Path(path).suffix.lower()))
        return path

    def _ride(self) -> None:
        if self.workout() is not None:
            self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        # Entrée valide une case ou la notation, jamais toute la fenêtre.
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            return
        super().keyPressEvent(event)


def _file_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
    return (safe or "seance") + ".zwo"


def _filter_for(ext: str) -> str:
    return next((f for f, e in SAVE_FILTERS.items() if e == ext.lower()), next(iter(SAVE_FILTERS)))


def _losses(workout: Workout, ext: str) -> str:
    """Ce que le format choisi ne garde pas, pour prévenir sans bloquer."""
    notes = []
    if ext in (".erg", ".mrc") and any(isinstance(i, Repeat) for i in workout.steps):
        notes.append("répétitions écrites à plat")
    if ext in (".erg", ".mrc") and any(s.name for s in workout.flatten()):
        notes.append("noms des briques non gardés")
    return f"  ({', '.join(notes)})" if notes else ""
