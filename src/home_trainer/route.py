"""Parcours GPX : distance, altitude et pente le long d'une trace.

`load_route(chemin)` lit un fichier .gpx (traces `trk` ou itinéraires `rte`).
`Route.grade_at(d)` donne la pente lissée au mètre `d` du parcours : la
différence d'altitude sur une fenêtre de 100 m centrée sur `d`, ce qui gomme
le bruit des altitudes GPS sans effacer une vraie rampe.
"""

from __future__ import annotations

import bisect
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

EARTH_RADIUS_M = 6_371_000
GRADE_WINDOW_M = 100.0  # longueur sur laquelle la pente est mesurée
MAX_ABS_GRADE_PCT = 25.0  # au-delà, c'est une erreur d'altitude plus qu'une route
CLIMB_STEP_M = 20.0


class RouteError(ValueError):
    """Fichier GPX illisible ou sans trace exploitable."""


@dataclass(frozen=True)
class RoutePoint:
    lat: float
    lon: float
    ele_m: float
    distance_m: float  # depuis le départ, le long de la trace


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


class Route:
    def __init__(self, name: str, points: list[RoutePoint]) -> None:
        if len(points) < 2 or points[-1].distance_m <= 0:
            raise RouteError("la trace doit contenir au moins deux points distincts")
        self.name = name
        self.points = points
        self._d = [p.distance_m for p in points]
        self._climb = self._cumulative_climb()

    @classmethod
    def from_coordinates(cls, name: str, coords: list[tuple[float, float, float | None]]) -> Route:
        """Construit le parcours depuis (lat, lon, altitude) ; altitudes manquantes interpolées."""
        known = [e for _, _, e in coords if e is not None]
        if not known:
            raise RouteError("la trace n'a pas d'altitude : impossible d'en déduire la pente")
        distances: list[float] = []
        kept: list[tuple[float, float, float | None]] = []
        for lat, lon, ele in coords:
            step = haversine_m(kept[-1][0], kept[-1][1], lat, lon) if kept else 0.0
            if kept and step == 0:
                continue  # point en double : rien à mesurer
            distances.append((distances[-1] if distances else 0.0) + step)
            kept.append((lat, lon, ele))
        # Altitudes : celles du fichier, interpolées selon la distance là où elles manquent.
        known_d = [(d, ele) for d, (_, _, ele) in zip(distances, kept) if ele is not None]
        return cls(name, [RoutePoint(lat, lon, _interpolate(known_d, d), d)
                          for d, (lat, lon, _) in zip(distances, kept)])

    # --- lecture ---------------------------------------------------------

    @property
    def total_m(self) -> float:
        return self._d[-1]

    def elevation_at(self, distance_m: float) -> float:
        d = max(0.0, min(self.total_m, distance_m))
        i = bisect.bisect_right(self._d, d)
        if i >= len(self.points):
            return self.points[-1].ele_m
        a, b = self.points[i - 1], self.points[i]
        span = b.distance_m - a.distance_m
        return a.ele_m + (b.ele_m - a.ele_m) * (d - a.distance_m) / span

    def position_at(self, distance_m: float) -> tuple[float, float]:
        """(lat, lon) au mètre `distance_m`."""
        d = max(0.0, min(self.total_m, distance_m))
        i = min(bisect.bisect_right(self._d, d), len(self.points) - 1)
        a, b = self.points[i - 1], self.points[i]
        k = (d - a.distance_m) / (b.distance_m - a.distance_m)
        return a.lat + (b.lat - a.lat) * k, a.lon + (b.lon - a.lon) * k

    def grade_at(self, distance_m: float, window_m: float = GRADE_WINDOW_M) -> float:
        """Pente lissée (%) au mètre `distance_m`, mesurée sur `window_m` centrés (raccourcis aux extrémités)."""
        window = min(window_m, self.total_m)
        lo = max(0.0, min(distance_m - window / 2, self.total_m - window))
        hi = lo + window
        grade = (self.elevation_at(hi) - self.elevation_at(lo)) / window * 100
        return max(-MAX_ABS_GRADE_PCT, min(MAX_ABS_GRADE_PCT, grade))

    @property
    def climb_m(self) -> float:
        return self._climb[-1]

    def climb_between(self, start_m: float, end_m: float) -> float:
        """Dénivelé positif entre deux distances (altitudes lissées pour ne pas compter le bruit GPS)."""
        return max(self._climb_at(end_m) - self._climb_at(start_m), 0.0)

    def _climb_at(self, d: float) -> float:
        """Dénivelé positif cumulé depuis le départ (grille de CLIMB_STEP_M, interpolée)."""
        x = max(0.0, min(self.total_m, d)) / CLIMB_STEP_M
        i = min(int(x), len(self._climb) - 2)
        return self._climb[i] + (self._climb[i + 1] - self._climb[i]) * (x - i)

    def _cumulative_climb(self) -> list[float]:
        n = math.ceil(self.total_m / CLIMB_STEP_M)
        half = GRADE_WINDOW_M / 4
        smoothed = [sum(self.elevation_at(i * CLIMB_STEP_M + k * half) for k in (-1, 0, 1)) / 3
                    for i in range(n + 1)]
        climb = [0.0]
        for a, b in zip(smoothed, smoothed[1:]):
            climb.append(climb[-1] + max(b - a, 0.0))
        return climb

    @property
    def min_ele_m(self) -> float:
        return min(p.ele_m for p in self.points)

    @property
    def max_ele_m(self) -> float:
        return max(p.ele_m for p in self.points)


def _interpolate(known: list[tuple[float, float]], d: float) -> float:
    if d <= known[0][0]:
        return known[0][1]
    if d >= known[-1][0]:
        return known[-1][1]
    i = bisect.bisect_right([k[0] for k in known], d)
    (d0, e0), (d1, e1) = known[i - 1], known[i]
    return e0 if d1 == d0 else e0 + (e1 - e0) * (d - d0) / (d1 - d0)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_gpx(data: bytes, default_name: str = "Parcours") -> Route:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise RouteError(f"GPX illisible : {e}") from None
    if _local(root.tag) != "gpx":
        raise RouteError("ce n'est pas un fichier GPX")
    name = None
    coords: list[tuple[float, float, float | None]] = []
    # Les traces (trk/trkseg/trkpt) d'abord ; à défaut, un itinéraire (rte/rtept).
    for container, point in (("trk", "trkpt"), ("rte", "rtept")):
        for element in root.iter():
            if _local(element.tag) != container:
                continue
            for child in element:
                if _local(child.tag) == "name" and child.text and name is None:
                    name = child.text.strip()
            for pt in element.iter():
                if _local(pt.tag) != point:
                    continue
                try:
                    lat, lon = float(pt.attrib["lat"]), float(pt.attrib["lon"])
                except (KeyError, ValueError):
                    continue
                ele = next((c.text for c in pt if _local(c.tag) == "ele" and c.text), None)
                try:
                    coords.append((lat, lon, float(ele) if ele is not None else None))
                except ValueError:
                    coords.append((lat, lon, None))
        if coords:
            break
    if not name:
        meta_name = next((e.text for e in root.iter() if _local(e.tag) == "name" and e.text), None)
        name = meta_name.strip() if meta_name else default_name
    if len(coords) < 2:
        raise RouteError("aucune trace dans ce fichier GPX")
    return Route.from_coordinates(name, coords)


def load_route(path: str | Path) -> Route:
    path = Path(path)
    return parse_gpx(path.read_bytes(), default_name=path.stem)
