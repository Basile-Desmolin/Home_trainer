"""Notation texte compacte pour composer une séance en briques.

Exemples :
    10m@150            10 minutes à 150 W
    30s@300W           30 secondes à 300 W
    20m@88%            20 minutes à 88 % de la FTP
    5m30s@200-220      5 min 30 s entre 200 et 220 W
    10m@100>200        rampe de 100 à 200 W sur 10 minutes
    10m@50%>75%        rampe de 50 à 75 % de la FTP
    10m                10 minutes sans cible (roue libre)
    open@120           jusqu'à appui sur « tour », à 120 W
    4x(4m@105% 2m@55%) 4 fois : 4 min à 105 %, 2 min à 55 %

Les éléments sont séparés par des espaces ou des virgules.
"""

from __future__ import annotations

import re

from .workout import Item, PowerTarget, PowerUnit, Repeat, Step, Workout

_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$")
_POWER = re.compile(r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?\s*(w|%|%ftp)?$", re.IGNORECASE)
_TOKEN = re.compile(r"\s*(?:(\d+)\s*x\s*\(|(\))|([^\s,()]+))\s*,?", re.IGNORECASE)


class BrickSyntaxError(ValueError):
    pass


def parse_bricks(text: str) -> list[Item]:
    items, pos = _parse_sequence(text, 0, nested=False)
    if pos != len(text.rstrip()) and text[pos:].strip():
        raise BrickSyntaxError(f"texte inattendu : {text[pos:].strip()!r}")
    return items


def parse_workout(text: str, name: str = "Séance") -> Workout:
    return Workout(name=name, steps=parse_bricks(text))


def format_bricks(items: list[Item]) -> str:
    """Inverse de `parse_bricks` (sans les noms ni les notes)."""
    parts = []
    for item in items:
        if isinstance(item, Repeat):
            parts.append(f"{item.count}x({format_bricks(item.steps)})")
        else:
            parts.append(_format_step(item))
    return " ".join(parts)


def parse_duration(text: str) -> float | None:
    text = text.strip().lower()
    if text == "open":
        return None
    if text.isdigit():
        return float(text) * 60  # un nombre seul = minutes
    m = _DURATION.match(text)
    if not text or not m or not any(m.groups()):
        raise BrickSyntaxError(f"durée invalide : {text!r} (ex. 10m, 30s, 1h, 5m30s)")
    h, mn, s = (float(g) if g else 0.0 for g in m.groups())
    seconds = h * 3600 + mn * 60 + s
    if seconds <= 0:
        raise BrickSyntaxError(f"durée nulle : {text!r}")
    return seconds


def parse_power(text: str) -> PowerTarget:
    m = _POWER.match(text.strip())
    if not m:
        raise BrickSyntaxError(f"puissance invalide : {text!r} (ex. 200, 200W, 90%, 200-220)")
    low = float(m.group(1))
    high = float(m.group(2)) if m.group(2) else low
    unit = PowerUnit.FTP_PERCENT if (m.group(3) or "").startswith("%") else PowerUnit.WATTS
    try:
        return PowerTarget(low, high, unit)
    except ValueError as e:
        raise BrickSyntaxError(f"puissance invalide : {text!r} ({e})") from None


def _parse_sequence(text: str, pos: int, nested: bool) -> tuple[list[Item], int]:
    items: list[Item] = []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip():
                raise BrickSyntaxError(f"texte inattendu : {text[pos:].strip()!r}")
            break
        count, close, brick = m.groups()
        if close:
            if not nested:
                raise BrickSyntaxError("parenthèse fermante en trop")
            return items, m.end()
        pos = m.end()
        if count:
            body, pos = _parse_sequence(text, pos, nested=True)
            if not body:
                raise BrickSyntaxError("répétition vide")
            if int(count) < 1:
                raise BrickSyntaxError("le nombre de répétitions doit être au moins 1")
            items.append(Repeat(int(count), body))
        else:
            items.append(_parse_step(brick))
    if nested:
        raise BrickSyntaxError("parenthèse non fermée")
    return items, pos


def _parse_step(token: str) -> Step:
    duration, _, power = token.partition("@")
    start, ramp, end = power.partition(">")
    power_end = None
    if ramp:
        if "%" in start + end:  # « 50>75% » : l'unité vaut pour les deux bornes
            start, end = start.rstrip("%") + "%", end.rstrip("%") + "%"
        power_end = parse_power(end)
    try:
        return Step(
            duration_s=parse_duration(duration),
            power=parse_power(start) if start else None,
            power_end=power_end,
        )
    except ValueError as e:
        if isinstance(e, BrickSyntaxError):
            raise
        raise BrickSyntaxError(f"brique invalide : {token!r} ({e})") from None


def _format_step(step: Step) -> str:
    out = "open" if step.duration_s is None else _format_duration(step.duration_s)
    if step.power is not None:
        out += "@" + _format_power(step.power)
        if step.power_end is not None:
            out += ">" + _format_power(step.power_end)
    return out


def _format_power(p: PowerTarget) -> str:
    value = _num(p.low) if p.low == p.high else f"{_num(p.low)}-{_num(p.high)}"
    return value + ("%" if p.unit is PowerUnit.FTP_PERCENT else "W")


def _format_duration(seconds: float) -> str:
    h, rest = divmod(round(seconds), 3600)
    m, s = divmod(rest, 60)
    return "".join(f"{v}{u}" for v, u in ((h, "h"), (m, "m"), (s, "s")) if v) or "0s"


def _num(x: float) -> str:
    x = round(float(x), 1)
    return str(int(x)) if x.is_integer() else str(x)
