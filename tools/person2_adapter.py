"""
tools/person2_adapter.py – Single integration point for Person 2's
get_destination_data function.

Until real integration is complete, this module falls back to labelled mock
data while providing a _normalise() function that maps arbitrary field names
to the DestinationData shape.

TODO: Replace the mock fallback in _call_person2() with the real import once
Person 2's package is available and integrated.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

import metrics
from core.schemas import DestinationData, Place

_MOCK_LABEL = "MOCK DATA - not live"


# ---------------------------------------------------------------------------
# Normalise helper – maps arbitrary dict to DestinationData-compatible shape
# ---------------------------------------------------------------------------

def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Map field names from an external response to the DestinationData schema.

    Handles common aliases (e.g. 'city_name' → 'city', 'latitude' → 'lat').
    Unknown fields are discarded.
    """
    field_map = {
        "city_name": "city",
        "name": "city",
        "latitude": "lat",
        "longitude": "lon",
        "tz": "timezone",
        "time_zone": "timezone",
        "currency": "currency_code",
        "local_currency": "currency_code",
        "fx_rate": "fx_rate_to_usd",
        "exchange_rate": "fx_rate_to_usd",
        "attractions": "places",
        "pois": "places",
    }
    normalised: dict[str, Any] = {}
    for k, v in raw.items():
        target_key = field_map.get(k, k)
        normalised[target_key] = v
    return normalised


# ---------------------------------------------------------------------------
# Mock fallback
# ---------------------------------------------------------------------------

def _mock_destination(city: str, country: Optional[str]) -> DestinationData:
    """Return a DestinationData fully populated with clearly labelled mock data."""
    metrics.inc_fallback()
    return DestinationData(
        city=city,
        country=country or "Unknown (MOCK)",
        lat=0.0,
        lon=0.0,
        timezone="UTC",
        currency_code="USD",
        fx_rate_to_usd=1.0,
        places=[
            Place(
                name=f"Mock Attraction {i} – {_MOCK_LABEL}",
                category="attraction",
                lat=None,
                lon=None,
                price_level=2,
                rating=4.0,
                est_cost_usd=10.0,
                source="mock",
            )
            for i in range(1, 6)
        ],
        fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        is_mock=True,
    )


# ---------------------------------------------------------------------------
# Internal call – replace with real integration when available
# ---------------------------------------------------------------------------

def _call_person2(city: str, country: Optional[str]) -> Optional[DestinationData]:
    """
    TODO: import and call Person 2's actual function here, e.g.:
        from person2_package import get_destination_data as p2_get
        raw = p2_get(city, country)
        normalised = _normalise(raw)
        return DestinationData(**normalised)
    Until then, return None to trigger the mock fallback.
    """
    return None  # Triggers mock fallback


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_destination_data(city: str, country: Optional[str] = None) -> DestinationData:
    """
    Retrieve destination data, preferring Person 2's implementation.

    Falls back to the local tools/destination.py if Person 2's integration
    is not yet available, and to labelled mock data if that also fails.
    """
    # Try Person 2's real implementation first
    result = _call_person2(city, country)
    if result is not None:
        return result

    # Fall back to local tools/destination.py
    try:
        from tools.destination import get_destination_data as local_get
        data = local_get(city, country)
        return data
    except Exception:
        pass

    # Last resort: fully mocked data
    import warnings
    warnings.warn(
        f"person2_adapter: all data sources failed for {city!r} – using mock data.",
        stacklevel=2,
    )
    return _mock_destination(city, country)
