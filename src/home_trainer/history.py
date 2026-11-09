"""Historique des sorties d'un profil : le bilan de chaque sortie enregistrée, gardé dans
`historique.json` du dossier des sorties du profil.

Sert à la liste des sorties, aux records (meilleures puissances), aux totaux par
semaine et à la forme (condition, fatigue).

Rien ne s'y perd : avant chaque écriture, la version précédente est copiée dans
`historique.bak.json` ; un fichier illisible est relu depuis cette copie et gardé de côté
(`historique.illisible-AAAAMMJJ-HHMMSS.json`) au lieu d'être écrasé ; une sortie illisible
est réécrite telle quelle.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from .ride_stats import CURVE_S, RideSummary, fitness

log = logging.getLogger(__name__)

FILE_NAME = "historique.json"


def _read(path: Path) -> tuple[dict | None, str | None]:
    """(contenu, erreur) : (None, None) si le fichier n'existe pas."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, str(e)
    if not isinstance(data, dict) or not isinstance(data.get("rides", []), list):
        return None, "contenu inattendu"
    return data, None


def _backup(path: Path) -> Path:
    """Copie de la version précédente : historique.bak.json."""
    return path.with_name(f"{path.stem}.bak.json")


class RideHistory:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.rides: list[RideSummary] = []  # de la plus ancienne à la plus récente
        self.unreadable: list = []  # sorties illisibles, réécrites telles quelles
        self.problem: str | None = None  # fichier illisible à la lecture : message pour l'utilisateur
        self.set_aside: Path | None = None  # où le fichier illisible a été gardé (à la première écriture)
        self._damaged = False  # fichier principal illisible : à garder de côté avant d'écrire

    @classmethod
    def load(cls, path: Path | str) -> RideHistory:
        path = Path(path)
        history = cls(path)
        data, error = _read(path)
        if error is not None:
            log.warning("historique illisible (%s) : %s", path, error)
            history._damaged = True
            data, _ = _read(_backup(path))
            if data is not None:
                history.problem = f"Historique illisible ({error}) : relu depuis la copie de secours."
            else:
                history.problem = f"Historique illisible ({error}), sans copie de secours lisible."
        for item in data.get("rides", []) if data is not None else []:
            try:
                history.rides.append(RideSummary.from_dict(item))
            except (TypeError, ValueError, KeyError, AttributeError) as e:
                log.warning("sortie illisible gardée telle quelle dans l'historique : %s", e)
                history.unreadable.append(item)
        history.rides.sort(key=lambda r: r.start)
        return history

    def save(self) -> None:
        """Enregistre ; lève OSError si l'écriture échoue (l'erreur est aussi notée au journal)."""
        if self.path is None:
            return
        data = {"rides": [r.to_dict() for r in self.rides] + self.unreadable}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._damaged and self.path.exists():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                aside = self.path.with_name(f"{self.path.stem}.illisible-{stamp}.json")
                self.path.replace(aside)
                self.set_aside = aside
            elif self.path.exists():
                shutil.copyfile(self.path, _backup(self.path))
            self._damaged = False
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("historique non enregistré (%s) : %s", self.path, e)
            raise

    # --- sorties -------------------------------------------------------------

    def mark_records(self, ride: RideSummary) -> RideSummary:
        """Note dans `ride.records` les meilleures puissances qu'elle bat (avec l'ancien record).
        Rien pour la toute première sortie : il n'y a pas encore de record à battre."""
        others = [r for r in self.rides if r.id != ride.id]
        ride.records = {}
        if not others:
            return ride
        best = self.best_powers(others)
        for seconds, watts in ride.best_w.items():
            previous = best.get(seconds)
            if previous is None or watts > previous[0]:
                ride.records[seconds] = previous[0] if previous else None
        return ride

    def add(self, ride: RideSummary) -> None:
        """Ajoute (ou remplace, même départ) la sortie et enregistre ; OSError si l'écriture échoue
        (la liste reste alors telle qu'avant)."""
        self._change(sorted([r for r in self.rides if r.id != ride.id] + [ride], key=lambda r: r.start))

    def remove(self, ride: RideSummary) -> None:
        """Retire la sortie et enregistre ; OSError si l'écriture échoue (la sortie reste)."""
        self._change([r for r in self.rides if r.id != ride.id])

    def _change(self, rides: list[RideSummary]) -> None:
        before, self.rides = self.rides, rides
        try:
            self.save()
        except OSError:
            self.rides = before
            raise

    # --- totaux --------------------------------------------------------------

    def best_powers(self, rides: list[RideSummary] | None = None,
                    since: float | None = None) -> dict[int, tuple[float, RideSummary]]:
        """Meilleure puissance par durée (et la sortie qui la détient)."""
        out: dict[int, tuple[float, RideSummary]] = {}
        for ride in self.rides if rides is None else rides:
            if since is not None and ride.start < since:
                continue
            for seconds, watts in ride.best_w.items():
                if seconds in CURVE_S and (seconds not in out or watts > out[seconds][0]):
                    out[seconds] = (watts, ride)
        return out

    def weeks(self, count: int, today: date | None = None) -> list[tuple[date, float, float, int]]:
        """Les `count` dernières semaines, de la plus ancienne à celle en cours :
        (lundi, durée en s, TSS, nombre de sorties)."""
        today = today or date.today()
        monday = today - timedelta(days=today.weekday())
        firsts = [monday - timedelta(weeks=i) for i in range(count - 1, -1, -1)]
        totals = {d: [0.0, 0.0, 0] for d in firsts}
        for ride in self.rides:
            day = datetime.fromtimestamp(ride.start).date()
            week = day - timedelta(days=day.weekday())
            if week in totals:
                totals[week][0] += ride.duration_s
                totals[week][1] += ride.tss
                totals[week][2] += 1
        return [(d, *totals[d]) for d in firsts]

    def fitness(self, today: date | None = None) -> tuple[float, float]:
        """(condition, fatigue) : CTL et ATL tirés du TSS de chaque jour."""
        daily: dict[date, float] = {}
        for ride in self.rides:
            day = datetime.fromtimestamp(ride.start).date()
            daily[day] = daily.get(day, 0.0) + ride.tss
        return fitness(daily, today or date.today())
