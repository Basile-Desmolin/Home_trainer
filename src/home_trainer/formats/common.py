"""Outils partagés par les formats de fichiers."""

from __future__ import annotations

from ..workout import Item, Repeat, Step

MAX_PATTERN = 8  # longueur maximale d'un motif cherché par `compress_repeats`


class FormatError(ValueError):
    """Fichier illisible, ou séance impossible à écrire dans ce format."""


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def merge_steady(items: list[Item]) -> list[Item]:
    """Fusionne les briques consécutives identiques hors durée (sans rampe)."""
    out: list[Item] = []
    for step in items:
        prev = out[-1] if out else None
        if (isinstance(prev, Step) and isinstance(step, Step) and not prev.is_ramp and not step.is_ramp
                and prev.duration_s is not None and step.duration_s is not None
                and (prev.power, prev.name, prev.intensity, prev.notes)
                == (step.power, step.name, step.intensity, step.notes)):
            out[-1] = Step(prev.duration_s + step.duration_s, prev.power, prev.name,
                           prev.intensity, prev.notes)
        else:
            out.append(step)
    return out


def compress_repeats(steps: list[Step]) -> list[Item]:
    """Regroupe les motifs répétés à la suite en `Repeat` (un seul niveau),
    puis fusionne les paliers identiques restés côte à côte.

    Les formats ERG/MRC et une partie des .zwo ne connaissent pas les
    répétitions ; on les reconstitue pour que la séance reste lisible.
    """
    out: list[Item] = []
    i = 0
    while i < len(steps):
        best_len, best_count = 1, 1
        for length in range(1, MAX_PATTERN + 1):
            block = steps[i:i + length]
            if len(block) < length:
                break
            if all(b == block[0] for b in block):
                continue  # un palier découpé en morceaux n'est pas une répétition
            count = 1
            while steps[i + count * length:i + (count + 1) * length] == block:
                count += 1
            if count >= 2 and length * count > best_len * best_count:
                best_len, best_count = length, count
        if best_count >= 2:
            out.append(Repeat(best_count, list(steps[i:i + best_len])))
            i += best_len * best_count
        else:
            out.append(steps[i])
            i += 1
    return merge_steady(out)


def require_durations(steps: list[Step], fmt: str) -> None:
    if any(s.duration_s is None for s in steps):
        raise FormatError(f"le format {fmt} n'accepte pas les briques sans durée (« open »)")
