"""Modèle de séance d'entraînement, indépendant du format de fichier.

Une séance est une liste ordonnée de briques (`Step`) et de répétitions
(`Repeat`, qui contiennent elles-mêmes des briques ou des répétitions).
Chaque brique a une durée et, en option, une cible de puissance exprimée en
watts ou en pourcentage de FTP. Une brique peut être une rampe : la cible
passe alors linéairement de `power` à `power_end`.
Certains fichiers donnent la cible en fréquence cardiaque (`heart_rate`, en
bpm) : la brique n'a alors pas de puissance tant qu'on ne l'a pas convertie
(voir `heart_zones`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Union


class PowerUnit(str, Enum):
    WATTS = "W"
    FTP_PERCENT = "%FTP"


class Intensity(str, Enum):
    ACTIVE = "active"
    REST = "rest"
    WARMUP = "warmup"
    COOLDOWN = "cooldown"
    RECOVERY = "recovery"
    INTERVAL = "interval"
    OTHER = "other"


@dataclass(frozen=True)
class PowerTarget:
    """Plage de puissance cible. `low == high` pour une valeur fixe (mode ERG)."""

    low: float
    high: float
    unit: PowerUnit = PowerUnit.WATTS

    def __post_init__(self) -> None:
        if self.low < 0 or self.high < 0:
            raise ValueError("la puissance cible doit être positive")
        if self.low > self.high:
            raise ValueError("borne basse supérieure à la borne haute")

    @classmethod
    def watts(cls, low: float, high: float | None = None) -> "PowerTarget":
        return cls(low, low if high is None else high, PowerUnit.WATTS)

    @classmethod
    def ftp_percent(cls, low: float, high: float | None = None) -> "PowerTarget":
        return cls(low, low if high is None else high, PowerUnit.FTP_PERCENT)

    def in_unit(self, unit: PowerUnit, ftp: float | None) -> "PowerTarget":
        """Même cible exprimée dans une autre unité (FTP requise si elle change)."""
        if unit is self.unit:
            return self
        if not ftp:
            raise ValueError("une FTP est nécessaire pour passer des watts au % FTP")
        factor = 100 / ftp if unit is PowerUnit.FTP_PERCENT else ftp / 100
        return PowerTarget(self.low * factor, self.high * factor, unit)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def to_watts(self, ftp: float | None) -> tuple[float, float]:
        """Plage en watts ; une FTP est requise pour une cible en % FTP."""
        if self.unit is PowerUnit.WATTS:
            return self.low, self.high
        if not ftp:
            raise ValueError("une FTP est nécessaire pour convertir une cible en % FTP")
        return self.low * ftp / 100, self.high * ftp / 100


@dataclass(frozen=True)
class HeartRateTarget:
    """Fréquence cardiaque cible en bpm (`low == high` pour une valeur fixe)."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low <= 0 or self.high <= 0:
            raise ValueError("la fréquence cardiaque cible doit être positive")
        if self.low > self.high:
            raise ValueError("borne basse supérieure à la borne haute")

    @classmethod
    def bpm(cls, low: float, high: float | None = None) -> "HeartRateTarget":
        return cls(low, low if high is None else high)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


@dataclass
class Step:
    """Une brique : `duration_s` secondes (None = jusqu'à appui sur « tour »)."""

    duration_s: float | None
    power: PowerTarget | None = None
    name: str = ""
    intensity: Intensity = Intensity.ACTIVE
    notes: str = ""
    power_end: PowerTarget | None = None  # fin de rampe, même unité que `power`
    heart_rate: HeartRateTarget | None = None  # cible en FC donnée par le fichier
    heart_rate_end: HeartRateTarget | None = None  # fin de rampe en FC

    def __post_init__(self) -> None:
        if self.duration_s is not None and self.duration_s <= 0:
            raise ValueError("la durée d'une brique doit être positive")
        if self.power_end is not None:
            if self.power is None or self.power_end.unit is not self.power.unit:
                raise ValueError("une rampe a une cible de départ dans la même unité")
            if self.duration_s is None:
                raise ValueError("une rampe doit avoir une durée")
            if self.power_end == self.power:
                self.power_end = None
        if self.heart_rate_end is not None:
            if self.heart_rate is None or self.duration_s is None:
                raise ValueError("une rampe de FC a une cible de départ et une durée")
            if self.heart_rate_end == self.heart_rate:
                self.heart_rate_end = None

    @property
    def is_ramp(self) -> bool:
        return self.power_end is not None


@dataclass
class Repeat:
    """Répète `count` fois la suite de briques `steps`."""

    count: int
    steps: list["Item"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("le nombre de répétitions doit être au moins 1")


Item = Union[Step, Repeat]


@dataclass(frozen=True)
class Segment:
    """Une brique placée dans le temps, après dépliage des répétitions.

    `low_w`/`high_w` sont la cible au départ, `end_low_w`/`end_high_w` à la
    fin (identiques sauf pour une rampe).
    """

    start_s: float
    duration_s: float | None
    step: Step
    low_w: float | None
    high_w: float | None
    end_low_w: float | None = None
    end_high_w: float | None = None

    def target_w(self, elapsed_s: float) -> float | None:
        """Consigne ERG (milieu de plage) après `elapsed_s` secondes dans la brique."""
        if self.low_w is None:
            return None
        start = (self.low_w + self.high_w) / 2
        if self.end_low_w is None or not self.duration_s:
            return start
        end = (self.end_low_w + self.end_high_w) / 2
        frac = min(max(elapsed_s / self.duration_s, 0.0), 1.0)
        return start + (end - start) * frac

    def target_bpm(self, elapsed_s: float) -> float | None:
        """FC cible (milieu de plage) après `elapsed_s` secondes dans la brique."""
        hr, hr_end = self.step.heart_rate, self.step.heart_rate_end
        if hr is None:
            return None
        if hr_end is None or not self.duration_s:
            return hr.mid
        frac = min(max(elapsed_s / self.duration_s, 0.0), 1.0)
        return hr.mid + (hr_end.mid - hr.mid) * frac


@dataclass
class Workout:
    name: str = "Séance"
    steps: list[Item] = field(default_factory=list)
    description: str = ""
    ftp: float | None = None  # FTP de référence indiquée par le fichier, si présente

    def flatten(self) -> Iterator[Step]:
        """Briques dans l'ordre d'exécution, répétitions dépliées."""
        yield from _flatten(self.steps)

    def timeline(self, ftp: float | None = None) -> list[Segment]:
        """Briques à exécuter avec leur instant de départ et leur cible en watts.

        Une brique sans durée (ouverte) est conservée mais n'avance pas
        l'horloge ; c'est au pilote du home trainer de décider quand passer
        à la suivante.
        """
        segments: list[Segment] = []
        t = 0.0
        for step in self.flatten():
            low = high = None
            if step.power is not None:
                low, high = step.power.to_watts(ftp)
            end_low, end_high = (step.power_end.to_watts(ftp) if step.power_end
                                 else (low, high))
            segments.append(Segment(t, step.duration_s, step, low, high, end_low, end_high))
            if step.duration_s is not None:
                t += step.duration_s
        return segments

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s or 0 for s in self.flatten())

    @property
    def uses_ftp_percent(self) -> bool:
        return any(s.power is not None and s.power.unit is PowerUnit.FTP_PERCENT
                   for s in self.flatten())

    @property
    def uses_watts(self) -> bool:
        return any(s.power is not None and s.power.unit is PowerUnit.WATTS
                   for s in self.flatten())

    @property
    def uses_heart_rate(self) -> bool:
        return any(s.heart_rate is not None for s in self.flatten())

    @property
    def has_power_targets(self) -> bool:
        return any(s.power is not None for s in self.flatten())

    @property
    def has_open_steps(self) -> bool:
        return any(s.duration_s is None for s in self.flatten())


def _flatten(items: list[Item]) -> Iterator[Step]:
    for item in items:
        if isinstance(item, Repeat):
            for _ in range(item.count):
                yield from _flatten(item.steps)
        else:
            yield item
