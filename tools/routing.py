"""
tools/routing.py – Travel-time matrix via OpenRouteService (ORS).

API: POST https://api.openrouteservice.org/v2/matrix/{profile}
Profiles used: "driving-car" (default) or "foot-walking".

Returns a 2D list of travel times in minutes. If a pair is unreachable
the cell contains None (not an error). Falls back to None-filled matrix
when the API key is missing or the call fails.
"""

from __future__ import annotations

import math
import os
from typing import List, Optional, Tuple

import metrics
from core.logging_utils import http_post

ORS_KEY: str = os.getenv("ORS_API_KEY", "")
_ORS_URL = "https://api.openrouteservice.org/v2/matrix/{profile}"


def _duration_matrix_mock(
    n: int,
) -> List[List[Optional[float]]]:
    """Return a mock n×n matrix with all None values (no ORS key)."""
    return [[None] * n for _ in range(n)]


def _haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _estimate_minutes(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Estimated door-to-door minutes: walking <=1.5 km, otherwise mixed transit."""
    km = _haversine_km(a, b) * 1.3  # street-network detour factor
    speed_kmh = 4.8 if km <= 1.5 else 18.0
    return round(km / speed_kmh * 60 + (0 if km <= 1.5 else 5), 1)


def _duration_matrix_estimate(coords: List[Tuple[float, float]]) -> List[List[Optional[float]]]:
    return [[0.0 if i == j else _estimate_minutes(coords[i], coords[j])
             for j in range(len(coords))] for i in range(len(coords))]


def get_travel_time_matrix(
    coordinates: List[Tuple[float, float]],
    profile: str = "foot-walking",
) -> List[List[Optional[float]]]:
    """
    Return the travel-time matrix (in minutes) for the given coordinates.

    :param coordinates: List of (lat, lon) tuples.
    :param profile:     ORS profile string ("driving-car" or "foot-walking").
    :returns:           N×N matrix; cell [i][j] = travel minutes from i to j,
                        or None if unreachable.
    """
    n = len(coordinates)
    if n == 0:
        return []

    if not ORS_KEY:
        import warnings
        warnings.warn(
            "ORS_API_KEY not set – routing uses straight-line estimates.",
            stacklevel=2,
        )
        metrics.inc_fallback()
        return _duration_matrix_estimate(coordinates)

    # ORS expects [lon, lat] order
    ors_locations = [[lon, lat] for lat, lon in coordinates]
    payload = {
        "locations": ors_locations,
        "metrics": ["duration"],
        "units": "m",
    }
    url = _ORS_URL.format(profile=profile)
    try:
        metrics.inc_api_call()
        resp = http_post(
            url,
            json=payload,
            headers={
                "Authorization": ORS_KEY,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            metrics.inc_api_error()
            metrics.inc_fallback()
            return _duration_matrix_estimate(coordinates)

        data = resp.json()
        raw_matrix = data.get("durations", [])
        # Convert seconds → minutes; None for unreachable (ORS uses 0 or very
        # large values for unreachable; treat > 1 day (86400 s) as None)
        matrix: List[List[Optional[float]]] = []
        for row in raw_matrix:
            conv_row: List[Optional[float]] = []
            for val in row:
                if val is None or val > 86400:
                    conv_row.append(None)
                else:
                    conv_row.append(round(val / 60, 1))
            matrix.append(conv_row)
        return matrix

    except Exception as exc:
        metrics.inc_api_error()
        metrics.inc_fallback()
        import warnings
        warnings.warn(f"ORS matrix call failed: {exc}", stacklevel=2)
        return _duration_matrix_estimate(coordinates)


def travel_minutes_from_center(
    center_lat: float,
    center_lon: float,
    places: List[dict],
) -> List[Optional[float]]:
    """
    Convenience wrapper: return travel times (minutes) from the city centre
    to each place.

    Places without lat/lon get None.

    :param center_lat: City centre latitude.
    :param center_lon: City centre longitude.
    :param places:     List of place dicts (must have 'lat' and 'lon' keys).
    :returns:          List of Optional[float] aligned with *places*.
    """
    if not places:
        return []

    coords: List[Tuple[float, float]] = [(center_lat, center_lon)]
    valid_indices: List[int] = []

    for i, p in enumerate(places):
        if p.get("lat") is not None and p.get("lon") is not None:
            coords.append((p["lat"], p["lon"]))
            valid_indices.append(i)

    if len(coords) == 1:
        # No place has coordinates
        return [None] * len(places)

    matrix = get_travel_time_matrix(coords)
    result: List[Optional[float]] = [None] * len(places)
    for col_idx, place_idx in enumerate(valid_indices):
        # Row 0 is the center; col_idx + 1 because we prepended center
        row = matrix[0] if matrix else []
        cell = row[col_idx + 1] if len(row) > col_idx + 1 else None
        result[place_idx] = cell

    return result
