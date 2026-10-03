"""Ligne de commande : afficher, créer ou convertir des séances.

Formats : .zwo (Zwift), .erg, .mrc, .fit, choisis d'après l'extension.

    home-trainer show seance.zwo [--ftp 250]
    home-trainer new seance.erg --ftp 250 --name "Sweet spot" "10m@50%>70% 3x(10m@90% 3m@55%)"
    home-trainer convert seance.zwo seance.erg --ftp 250
"""

from __future__ import annotations

import argparse
import sys

from .bricks import BrickSyntaxError, format_bricks, parse_workout
from .formats import FormatError, load_workout, save_workout
from .workout import PowerUnit, Segment, Workout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="home-trainer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="afficher le contenu d'une séance")
    show.add_argument("file")
    show.add_argument("--ftp", type=float, help="FTP en watts, pour convertir les % FTP")

    new = sub.add_parser("new", help="créer une séance à partir de briques")
    new.add_argument("file", help="fichier à écrire (.zwo, .erg, .mrc ou .fit)")
    new.add_argument("bricks", help='ex. "10m@150 5x(1m@300 1m@150) 10m@120"')
    new.add_argument("--name", default="Séance")
    new.add_argument("--ftp", type=float,
                     help="FTP en watts, nécessaire pour passer des watts au % FTP ou l'inverse")

    conv = sub.add_parser("convert", help="convertir une séance d'un format à un autre")
    conv.add_argument("source")
    conv.add_argument("dest")
    conv.add_argument("--ftp", type=float, help="FTP en watts, si la conversion l'exige")

    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            warnings: list[str] = []
            workout = load_workout(args.file, warnings)
            print(describe(workout, args.ftp or workout.ftp))
            _print_warnings(warnings)
        elif args.command == "new":
            workout = parse_workout(args.bricks, name=args.name)
            save_workout(workout, args.file, args.ftp)
            print(describe(workout, args.ftp))
            print(f"\nécrit : {args.file}")
        else:
            warnings = []
            workout = load_workout(args.source, warnings)
            _print_warnings(warnings)
            save_workout(workout, args.dest, args.ftp)
            print(describe(workout, args.ftp or workout.ftp))
            print(f"\nécrit : {args.dest}")
    except (OSError, FormatError, BrickSyntaxError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 1
    return 0


def _print_warnings(warnings: list[str]) -> None:
    for w in warnings:
        print(f"attention : {w}", file=sys.stderr)


def describe(workout: Workout, ftp: float | None = None) -> str:
    lines = [f"{workout.name}  ({_hms(workout.total_duration_s)}"
             f"{' + étapes ouvertes' if workout.has_open_steps else ''})",
             f"briques : {format_bricks(workout.steps)}", ""]
    needs_ftp = workout.uses_ftp_percent
    segments = _timeline_without_ftp(workout) if needs_ftp and not ftp else workout.timeline(ftp)
    for seg in segments:
        duration = "ouverte" if seg.duration_s is None else _hms(seg.duration_s)
        target = _target(seg, ftp)
        label = f"  {seg.step.name}" if seg.step.name else ""
        lines.append(f"{_hms(seg.start_s):>8}  {duration:>8}  {target:<24}{label}")
    return "\n".join(lines)


def _timeline_without_ftp(workout: Workout):
    t = 0.0
    for step in workout.flatten():
        yield Segment(t, step.duration_s, step, None, None)
        t += step.duration_s or 0


def _target(seg: Segment, ftp: float | None) -> str:
    p, end = seg.step.power, seg.step.power_end
    if p is None:
        return "libre"
    if p.unit is PowerUnit.WATTS or ftp is None:
        unit = "W" if p.unit is PowerUnit.WATTS else "% FTP"
        text = _range(p.low, p.high)
        if end is not None:
            text += " → " + _range(end.low, end.high)
        return f"{text} {unit}"
    text = _range(seg.low_w, seg.high_w)
    pct = _range(p.low, p.high)
    if end is not None:
        text += " → " + _range(seg.end_low_w, seg.end_high_w)
        pct += " → " + _range(end.low, end.high)
    return f"{text} W ({pct} %)"


def _range(low: float, high: float) -> str:
    return f"{low:.0f}" if round(low) == round(high) else f"{low:.0f}-{high:.0f}"


def _hms(seconds: float) -> str:
    h, rest = divmod(round(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
