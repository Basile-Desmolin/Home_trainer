"""Sortie de secours : les mesures de la sortie en cours, gardées sur le disque pendant qu'on roule.

Toutes les 10 s, les nouvelles mesures sont ajoutées (et forcées sur le disque) à
`%APPDATA%\\HomeTrainer\\secours\\<profil>-<mode>-<départ>.jsonl` : une ligne d'en-tête (titre,
description, mode, profil), puis une ligne par `Sample`. Un nouvel en-tête est ajouté si le titre ou
la description changent ; c'est le dernier qui compte. Le fichier n'est supprimé qu'une fois la sortie
enregistrée ou jetée par l'utilisateur : un plantage, une coupure de courant ou un `.fit` impossible à
écrire laissent la sortie proposée au lancement suivant (`find_rescued`).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from .session import Sample

log = logging.getLogger(__name__)

RESCUE_EVERY_S = 10.0  # au plus 10 s de mesures perdues en cas de plantage
SUFFIX = ".jsonl"
NO_PROFILE = "sans-profil"
MODE_NAMES = {"seance": "séance", "libre": "mode libre", "parcours": "parcours"}
_SAMPLE_FIELDS = {f.name for f in fields(Sample)}

DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre")


def rescue_dir() -> Path:
    from ..config import config_dir
    return config_dir() / "secours"


class RescueFile:
    """Secours d'une sortie : suit sa liste de mesures (la même tant que la sortie dure, même quand
    la séance est recréée pour une nouvelle FTP) et en ajoute les nouvelles au fichier."""

    def __init__(self, directory: Path | str, profile: str | None, mode: str, samples: list[Sample]) -> None:
        self.dir = Path(directory)
        self.profile = profile or NO_PROFILE
        self.mode = mode
        self.samples = samples
        self.path: Path | None = None
        self.written = 0
        self._header: tuple[str, str] | None = None

    def _new_path(self) -> Path:
        start = next((x.at for x in self.samples if x.at is not None), None) or time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(start))
        path = self.dir / f"{self.profile}-{self.mode}-{stamp}{SUFFIX}"
        n = 2
        while path.exists():
            path = path.with_name(f"{self.profile}-{self.mode}-{stamp}~{n}{SUFFIX}")
            n += 1
        return path

    def write(self, title: str, description: str) -> None:
        """Ajoute les mesures nouvelles depuis la dernière fois (et l'en-tête s'il a changé), puis force
        l'écriture sur le disque. Lève `OSError` si c'est impossible (rien n'est alors compté comme écrit)."""
        if not self.samples:
            return
        rewrite = len(self.samples) < self.written  # liste raccourcie : tout réécrire
        header = (title, description)
        new = self.samples[0 if rewrite else self.written:]
        if not new and header == self._header and not rewrite:
            return
        lines = []
        if rewrite or header != self._header or self.path is None:
            lines.append(json.dumps({"header": {"title": title, "description": description,
                                                "mode": self.mode, "profile": self.profile}},
                                    ensure_ascii=False))
        lines += [json.dumps(asdict(x), ensure_ascii=False) for x in new]
        if self.path is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.path = self._new_path()
        with open(self.path, "w" if rewrite else "a", encoding="utf-8", newline="\n") as f:
            f.write("".join(line + "\n" for line in lines))
            f.flush()
            os.fsync(f.fileno())
        self.written = len(self.samples)
        self._header = header

    def remove(self) -> None:
        """Sortie enregistrée ou jetée : le secours n'a plus lieu d'être."""
        if self.path is not None:
            remove_file(self.path)
        self.path = None
        self.written = 0
        self._header = None


def remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as e:  # il sera reproposé : mieux vaut un doublon qu'une perte
        log.warning("secours non supprimé %s : %s", path, e)


@dataclass
class RescuedRide:
    """Sortie retrouvée dans un fichier de secours."""

    path: Path
    title: str
    description: str
    mode: str
    profile: str
    samples: list[Sample]

    @property
    def start(self) -> float | None:
        return next((x.at for x in self.samples if x.at is not None), None)

    @property
    def duration_s(self) -> int:
        return len(self.samples)  # une mesure par seconde de chrono

    @property
    def average_power(self) -> float:
        return sum(x.power_w for x in self.samples) / len(self.samples)


def read_rescue(path: Path) -> RescuedRide | None:
    """Relit un secours ; une ligne coupée (arrêt pendant l'écriture) est ignorée. None si le fichier
    est illisible ou sans mesure (il reste alors sur le disque)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        log.warning("secours illisible %s : %s", path, e)
        return None
    header: dict | None = None
    samples: list[Sample] = []
    for line in text.splitlines():
        try:
            data = json.loads(line)
            if "header" in data:
                header = data["header"]
            else:
                samples.append(Sample(**{k: v for k, v in data.items() if k in _SAMPLE_FIELDS}))
        except (ValueError, TypeError, AttributeError):
            log.warning("ligne ignorée dans le secours %s", path)
    if header is None or not samples:
        return None
    return RescuedRide(path, str(header.get("title") or "Sortie"), str(header.get("description") or ""),
                       str(header.get("mode") or ""), str(header.get("profile") or NO_PROFILE), samples)


def find_rescued(directory: Path | str, profile: str | None) -> list[RescuedRide]:
    """Sorties de secours de ce profil, de la plus ancienne à la plus récente."""
    profile = profile or NO_PROFILE
    rides = []
    for path in sorted(Path(directory).glob(f"{profile}-*{SUFFIX}")):
        ride = read_rescue(path)
        if ride is not None and ride.profile == profile:
            rides.append(ride)
    return sorted(rides, key=lambda r: r.start or 0)


# --- fenêtres ----------------------------------------------------------------

SAVE, LATER, DISCARD = "enregistrer", "plus tard", "jeter"


def duration_text(seconds: int) -> str:
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min"
    return f"{m} min {s:02d} s" if m else f"{s} s"


def ride_line(ride: RescuedRide) -> str:
    when = ""
    if ride.start is not None:
        t = time.localtime(ride.start)
        when = f"{DAYS[t.tm_wday]} {t.tm_mday} {MONTHS[t.tm_mon - 1]} {t.tm_year}, départ {t.tm_hour} h {t.tm_min:02d}, "
    mode = MODE_NAMES.get(ride.mode, ride.mode)
    return (f"{ride.title} — {mode}\n"
            f"{when}{duration_text(ride.duration_s)}, {ride.average_power:.0f} W en moyenne")


def _box(parent, icon, title: str, text: str) -> QMessageBox:
    box = QMessageBox(icon, title, text, parent=parent)
    # Assez large pour que le chemin ou la ligne de la sortie ne soient pas coupés n'importe où.
    # (le texte seul : l'icône est aussi un QLabel)
    box.setStyleSheet("QLabel#qt_msgbox_label { min-width: 460px; }")
    return box


def confirm_discard(parent, title: str, duration_s: int) -> bool:
    """« Jeter » demande confirmation ; « Annuler » est le choix par défaut."""
    box = _box(parent, QMessageBox.Warning, "Jeter la sortie",
               f"Jeter « {title} » ({duration_text(duration_s)}) ?\n\nElle sera perdue définitivement.")
    yes = box.addButton("Jeter", QMessageBox.DestructiveRole)
    cancel = box.addButton("Annuler", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is yes


def ask_rescued(parent, ride: RescuedRide) -> str:
    """Sortie retrouvée au lancement : SAVE, LATER ou DISCARD (confirmé)."""
    while True:
        box = _box(parent, QMessageBox.Question, "Sortie retrouvée",
                   "Une sortie n'a pas été enregistrée (arrêt imprévu de l'application, ou fichier "
                   f"impossible à écrire) :\n\n{ride_line(ride)}\n\n« Enregistrer » l'écrit en .fit dans "
                   "le dossier des sorties et l'envoie vers Strava / Nolio.")
        save = box.addButton("Enregistrer", QMessageBox.AcceptRole)
        later = box.addButton("Plus tard", QMessageBox.RejectRole)
        discard = box.addButton("Jeter…", QMessageBox.DestructiveRole)
        box.setDefaultButton(save)
        box.setEscapeButton(later)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return SAVE
        if clicked is discard:
            if confirm_discard(parent, ride.title, ride.duration_s):
                return DISCARD
            continue
        return LATER


def ask_unsaved(parent, title: str, duration_s: int, error: str) -> bool:
    """Ni le .fit ni le secours n'ont pu être écrits : True pour réessayer, False pour jeter
    (confirmé). La fenêtre ne se ferme pas autrement."""
    while True:
        box = _box(parent, QMessageBox.Critical, "Sortie en danger",
                   f"La sortie « {title} » ({duration_text(duration_s)}) n'a pas pu être enregistrée, ni gardée "
                   f"en secours :\n{error}\n\nLibérez de la place ou fermez OneDrive, puis réessayez. "
                   "« Jeter » la perd définitivement.")
        retry = box.addButton("Réessayer", QMessageBox.AcceptRole)
        box.addButton("Jeter…", QMessageBox.DestructiveRole)
        box.setDefaultButton(retry)
        box.setEscapeButton(retry)
        box.exec()
        if box.clickedButton() is retry:
            return True
        if confirm_discard(parent, title, duration_s):
            return False
