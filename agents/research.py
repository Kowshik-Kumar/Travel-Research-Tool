"""
agents/research.py – Pure function research() that produces a ResearchResponse.

The function never raises; bad input returns {"status": "error", "error": ...}.
All external calls go through the existing tool wrappers which handle fallback
and logging internally.

Scoring weights (default):
    price:        0.45  (cheaper = higher score)
    travel_time:  0.35  (closer = higher score)
    weather:      0.20  (better weather_score = higher score)
"""

from __future__ import annotations

import json
from typing import Any, Optional

import metrics
from core.schemas import (
    RankedPlace,
    ResearchResponse,
    TripRequest,
    WeatherDay,
)
from tools.person2_adapter import get_destination_data
from tools.routing import travel_minutes_from_center
from tools.transport_mock import get_transport_options
from tools.weather import get_weather_forecast

# ---------------------------------------------------------------------------
# Default weights
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS = {"price": 0.45, "travel_time": 0.35, "weather": 0.20}
_DEFAULT_TOP_N = 15
_MIN_RATING = 2.5
_POPULARITY_WEIGHT = 0.4  # share of the final score given to place notability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_weights(weights: dict) -> Optional[str]:
    required = {"price", "travel_time", "weather"}
    if not required.issubset(weights.keys()):
        return f"weights must contain keys: {required}"
    total = sum(weights[k] for k in required)
    if abs(total - 1.0) > 0.001:
        return f"weights must sum to 1.0 (got {total:.4f})"
    return None


def _normalise_to_01(values: list[Optional[float]], invert: bool = False) -> list[float]:
    """
    Min-max normalise a list, treating None as the worst value.
    If invert=True, lower raw value = higher score (e.g. cost, travel time).
    """
    valid = [v for v in values if v is not None]
    if not valid:
        return [0.0] * len(values)
    mn, mx = min(valid), max(valid)
    worst = mx if not invert else mn
    results = []
    for v in values:
        raw = v if v is not None else worst
        if mx == mn:
            results.append(0.5)
        else:
            norm = (raw - mn) / (mx - mn)
            results.append(1.0 - norm if invert else norm)
    return results


def _best_weather_score(place_idx: int, weather_days: list[WeatherDay]) -> float:
    """Return the best weather score across available days."""
    if not weather_days:
        return 0.5
    return max(d.score for d in weather_days)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def research(request: dict[str, Any], weights: Optional[dict] = None, top_n: int = _DEFAULT_TOP_N) -> dict[str, Any]:
    """
    Run destination research and return a ResearchResponse dict.

    :param request: Dict matching TripRequest schema.
    :param weights: Optional override for scoring weights. Must sum to 1.
    :param top_n:   Number of ranked places to return (default 15).
    :returns:       ResearchResponse as a plain dict (compact JSON-ready).
    """
    metrics.inc_request()

    # ---- Input validation ------------------------------------------------
    try:
        trip = TripRequest(**request)
    except Exception as exc:
        metrics.inc_validator_failure()
        return ResearchResponse(status="error", error=str(exc)).model_dump()

    if trip.destination_city is None:
        metrics.inc_validator_failure()
        return ResearchResponse(
            status="error", error="destination_city is required"
        ).model_dump()

    # ---- Weight validation ------------------------------------------------
    w = _DEFAULT_WEIGHTS.copy()
    if weights:
        err = _validate_weights(weights)
        if err:
            metrics.inc_validator_failure()
            return ResearchResponse(status="error", error=err).model_dump()
        w = weights

    # ---- Fetch destination data ------------------------------------------
    try:
        dest = get_destination_data(trip.destination_city, trip.country)
    except Exception as exc:
        return ResearchResponse(status="error", error=f"Destination lookup failed: {exc}").model_dump()

    # ---- Filter candidates -----------------------------------------------
    candidates = []
    seen_names: set[str] = set()
    for p in dest.places:
        # Remove no-coordinates entries (kept for ranking-last logic below)
        # Remove rating < 2.5 when rating is set
        if p.rating is not None and p.rating < _MIN_RATING:
            continue
        lower = p.name.lower()
        if lower in seen_names:
            continue
        seen_names.add(lower)
        candidates.append(p)

    # ---- Travel times from city center ------------------------------------
    place_dicts = [p.model_dump() for p in candidates]
    travel_times = travel_minutes_from_center(dest.lat, dest.lon, place_dicts)

    # ---- Weather ----------------------------------------------------------
    try:
        weather_days = get_weather_forecast(dest.lat, dest.lon)
    except Exception:
        weather_days = []

    # ---- Scoring ---------------------------------------------------------
    costs = [p.est_cost_usd for p in candidates]
    times_raw = travel_times  # list of Optional[float]

    cost_scores = _normalise_to_01(costs, invert=True)
    travel_scores = _normalise_to_01(times_raw, invert=True)
    weather_scores = [_best_weather_score(i, weather_days) for i in range(len(candidates))]

    scored: list[tuple[float, int]] = []  # (score, original_index)
    for i, place in enumerate(candidates):
        has_geo = place.lat is not None and place.lon is not None
        if not has_geo:
            # No geocode → ranked last
            scored.append((-1.0, i))
        else:
            base = (
                w["price"] * cost_scores[i]
                + w["travel_time"] * travel_scores[i]
                + w["weather"] * weather_scores[i]
            )
            # Notability matters: blend in the place rating (0-5; unknown = neutral 0.5)
            pop = (candidates[i].rating / 5.0) if candidates[i].rating is not None else 0.5
            s = (1 - _POPULARITY_WEIGHT) * base + _POPULARITY_WEIGHT * pop
            scored.append((s, i))

    # Sort: -1.0 entries (no geocode) go last
    scored.sort(key=lambda x: x[0], reverse=True)

    ranked_places: list[RankedPlace] = []
    for rank, (score, idx) in enumerate(scored[:top_n], start=1):
        p = candidates[idx]
        is_mock_place = p.source == "mock" or dest.is_mock
        ranked_places.append(
            RankedPlace(
                rank=rank,
                name=p.name,
                category=p.category,
                lat=p.lat,
                lon=p.lon,
                est_cost_usd=p.est_cost_usd,
                travel_minutes_from_center=travel_times[idx],
                weather_score=weather_scores[idx],
                score=max(0.0, score),
                is_mock=is_mock_place,
            )
        )

    # ---- Transport -------------------------------------------------------
    transport = get_transport_options(
        origin_city="Unknown",
        destination_city=trip.destination_city,
    )

    result = ResearchResponse(
        status="ok",
        city=dest.city,
        ranked=ranked_places,
        transport_options=transport,
        weather_summary=weather_days,
    )

    metrics.inc_success()
    if ranked_places:
        metrics.inc_covered()

    return result.model_dump()
