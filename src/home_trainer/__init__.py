"""Logiciel de pilotage de home trainer (Bluetooth FTMS / ANT+ FE-C : Wahoo, Elite, Tacx…)."""

from .workout import Intensity, PowerTarget, PowerUnit, Repeat, Segment, Step, Workout

__version__ = "0.1.0"

__all__ = ["Intensity", "PowerTarget", "PowerUnit", "Repeat", "Segment", "Step", "Workout"]
