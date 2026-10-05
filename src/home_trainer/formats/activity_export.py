"""Une sortie dans le format choisi : .fit (par défaut), .tcx ou .csv.

    write_ride(points, "sortie.tcx")   # le format suit l'extension

Le .fit est celui qu'attendent Strava et Nolio ; le .tcx (Garmin Training
Center) est lu par la plupart des sites et logiciels ; le .csv, une ligne par
seconde, s'ouvre dans un tableur.
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path

from .fit_activity import ActivityPoint, _distances, _durations, summarize, write_activity

# Extension → libellé, dans l'ordre de la liste de choix (le premier est celui par défaut).
RIDE_FORMATS = {
    "fit": "FIT : Strava, Nolio, Garmin… (.fit)",
    "tcx": "TCX : Garmin Training Center (.tcx)",
    "csv": "CSV : tableur, une ligne par seconde (.csv)",
}


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def encode_tcx(points: list[ActivityPoint]) -> str:
    points = sorted(points, key=lambda p: p.at)
    distances = _distances(points, _durations(points))
    laps: list[list[int]] = []
    for i, p in enumerate(points):
        if not laps or p.lap != points[laps[-1][-1]].lap:
            laps.append([])
        laps[-1].append(i)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"'
           ' xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">',
           '  <Activities>', '    <Activity Sport="Biking">', f"      <Id>{_iso(points[0].at)}</Id>"]
    for lap in laps:
        lap_points = [points[i] for i in lap]
        s = summarize(lap_points)
        out.append(f'      <Lap StartTime="{_iso(s.start)}">')
        out.append(f"        <TotalTimeSeconds>{s.timer_s:.1f}</TotalTimeSeconds>")
        lap_distance = distances[lap[-1]] - (distances[lap[0] - 1] if lap[0] else 0.0)
        out.append(f"        <DistanceMeters>{lap_distance:.1f}</DistanceMeters>")
        out.append(f"        <Calories>{round(s.work_j / 1000)}</Calories>")
        if s.avg_heart_rate:
            out.append(f"        <AverageHeartRateBpm><Value>{round(s.avg_heart_rate)}</Value></AverageHeartRateBpm>")
            out.append(f"        <MaximumHeartRateBpm><Value>{round(s.max_heart_rate)}</Value></MaximumHeartRateBpm>")
        out.append("        <Intensity>Active</Intensity>")
        if s.avg_cadence:
            out.append(f"        <Cadence>{round(s.avg_cadence)}</Cadence>")
        out.append("        <TriggerMethod>Manual</TriggerMethod>")
        out.append("        <Track>")
        for i in lap:
            p = points[i]
            out.append("          <Trackpoint>")
            out.append(f"            <Time>{_iso(p.at)}</Time>")
            out.append(f"            <DistanceMeters>{distances[i]:.1f}</DistanceMeters>")
            if p.heart_rate_bpm:
                out.append(f"            <HeartRateBpm><Value>{round(p.heart_rate_bpm)}</Value></HeartRateBpm>")
            if p.cadence_rpm is not None:
                out.append(f"            <Cadence>{round(p.cadence_rpm)}</Cadence>")
            tpx = f"<ns3:Watts>{round(max(p.power_w, 0))}</ns3:Watts>"
            if p.speed_kmh is not None:
                tpx = f"<ns3:Speed>{p.speed_kmh / 3.6:.3f}</ns3:Speed>" + tpx
            out.append(f"            <Extensions><ns3:TPX>{tpx}</ns3:TPX></Extensions>")
            out.append("          </Trackpoint>")
        out.append("        </Track>")
        out.append("      </Lap>")
    out += ["      <Creator><Name>Home trainer</Name></Creator>",
            "    </Activity>", "  </Activities>", "</TrainingCenterDatabase>", ""]
    return "\n".join(out)


CSV_COLUMNS = ["heure", "temps_s", "puissance_w", "cadence_tr_min", "cardio_bpm", "vitesse_km_h",
               "distance_m", "pente_pct", "tour"]


def write_csv(points: list[ActivityPoint], dest: str | os.PathLike) -> Path:
    points = sorted(points, key=lambda p: p.at)
    durations = _durations(points)
    distances = _distances(points, durations)
    path = Path(dest)
    # Point-virgule et BOM : Excel en français l'ouvre directement en colonnes.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CSV_COLUMNS)
        elapsed = 0.0
        for p, dt, distance in zip(points, durations, distances):
            elapsed += dt

            def num(v: float | None, digits: int = 0) -> str:
                return "" if v is None else f"{v:.{digits}f}"

            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.at)), num(elapsed),
                        num(p.power_w), num(p.cadence_rpm), num(p.heart_rate_bpm), num(p.speed_kmh, 2),
                        num(distance, 1), num(p.grade_pct, 1), p.lap + 1])
    return path


def write_ride(points: list[ActivityPoint], dest: str | os.PathLike) -> Path:
    """Écrit la sortie dans le format de l'extension de `dest` (.fit, .tcx ou .csv)."""
    if not points:
        raise ValueError("sortie vide : aucune mesure")
    path = Path(dest)
    fmt = path.suffix.lower().lstrip(".")
    if fmt == "fit":
        return write_activity(points, path)
    if fmt == "tcx":
        path.write_text(encode_tcx(points), encoding="utf-8")
        return path
    if fmt == "csv":
        return write_csv(points, path)
    raise ValueError(f"format de sortie inconnu : .{fmt} (formats : {', '.join('.' + k for k in RIDE_FORMATS)})")
