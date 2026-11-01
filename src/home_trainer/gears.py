"""Braquets virtuels : 24 rapports simulés sur un vélo à pignon unique (Zwift Cog).

    gears = VirtualGears()        # braquet 12 : celui du vélo, rien ne change
    gears.shift(+1)               # un cran plus dur (bouton + du Zwift Click)
    gears.factor                  # ≈ 1,085 : envoyé au home trainer avec la pente (`Slope.gear`)

Hors ERG, le home trainer simule une route (pente, poids, vitesse). Passer
un braquet `factor` fois plus grand revient, à même cadence, à rouler
`factor` fois plus vite : le home trainer en tient compte en simulant une
masse et une traînée plus grandes (voir `sensors.trainer`). Les crans sont
réguliers (8,5 % l'un de l'autre, comme sur une cassette serrée) : du
braquet 1, 2,5 fois plus souple que celui du vélo, au braquet 24, 2,7 fois
plus grand.
"""

from __future__ import annotations

GEAR_COUNT = 24
NEUTRAL_GEAR = 12  # le braquet réel du vélo (plateau / Cog)
GEAR_STEP = 1.085


def gear_factor(gear: int) -> float:
    """Rapport du braquet virtuel `gear` (1 à 24) au braquet réel du vélo."""
    return GEAR_STEP ** (gear - NEUTRAL_GEAR)


class VirtualGears:
    """Braquet virtuel en cours, de 1 (le plus souple) à `GEAR_COUNT` (le plus dur)."""

    def __init__(self, gear: int = NEUTRAL_GEAR) -> None:
        self.gear = NEUTRAL_GEAR
        self.set(gear)

    def set(self, gear: int) -> int:
        self.gear = max(1, min(GEAR_COUNT, int(gear)))
        return self.gear

    def shift(self, direction: int) -> int:
        """Change de braquet : +1 plus dur, −1 plus souple (bloqué aux extrémités)."""
        return self.set(self.gear + direction)

    @property
    def factor(self) -> float:
        return gear_factor(self.gear)

    @property
    def label(self) -> str:
        return f"{self.gear} / {GEAR_COUNT}"
