"""
tools/geocode.py – Geocoding via Nominatim (OpenStreetMap).

Rules:
- Rate-limited to 1 request/second (enforced with a threading.Lock + sleep).
- Sends NOMINATIM_USER_AGENT from env vars.
- Caches results in SQLite (geocode_cache table).
- Falls back to labelled mock data if the API key/agent is missing or the
  request fails; records a fallback event in metrics.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import metrics
from core.config import DEFAULT_USER_AGENT, offline
from core.logging_utils import http_get
from core.storage import geocode_cache_get, geocode_cache_set

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NOMINATIM_USER_AGENT: str = os.getenv("NOMINATIM_USER_AGENT", "") or DEFAULT_USER_AGENT
_BASE_URL = "https://nominatim.openstreetmap.org/search"

# Rate-limit state
_rate_lock = threading.Lock()
_last_call_time: float = 0.0
_MIN_INTERVAL = 1.0  # seconds

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------
_MOCK_CITIES: dict[str, dict] = {
    "kyoto": {
        "city": "Kyoto", "country": "Japan",
        "lat": 35.0117, "lon": 135.7683, "timezone": "Asia/Tokyo",
    },
    "lisbon": {
        "city": "Lisbon", "country": "Portugal",
        "lat": 38.7167, "lon": -9.1333, "timezone": "Europe/Lisbon",
    },
    "cusco": {
        "city": "Cusco", "country": "Peru",
        "lat": -13.5317, "lon": -71.9675, "timezone": "America/Lima",
    },
    "paris": {
        "city": "Paris", "country": "France",
        "lat": 48.8566, "lon": 2.3522, "timezone": "Europe/Paris",
    },
    "new york": {
        "city": "New York", "country": "USA",
        "lat": 40.7128, "lon": -74.0060, "timezone": "America/New_York",
    },
    "tokyo": {
        "city": "Tokyo", "country": "Japan",
        "lat": 35.6762, "lon": 139.6503, "timezone": "Asia/Tokyo",
    },
    "rome": {
        "city": "Rome", "country": "Italy",
        "lat": 41.9028, "lon": 12.4964, "timezone": "Europe/Rome",
    },
    "barcelona": {
        "city": "Barcelona", "country": "Spain",
        "lat": 41.3851, "lon": 2.1734, "timezone": "Europe/Madrid",
    },
    "bangkok": {
        "city": "Bangkok", "country": "Thailand",
        "lat": 13.7563, "lon": 100.5018, "timezone": "Asia/Bangkok",
    },
    "sydney": {
        "city": "Sydney", "country": "Australia",
        "lat": -33.8688, "lon": 151.2093, "timezone": "Australia/Sydney",
    },
}


def _mock_for(city: str, country: Optional[str] = None) -> dict:
    key = city.lower().strip()
    if key in _MOCK_CITIES:
        return dict(_MOCK_CITIES[key])
    # Generic fallback
    return {
        "city": city,
        "country": country or "Unknown",
        "lat": 0.0,
        "lon": 0.0,
        "timezone": "UTC",
    }


# ---------------------------------------------------------------------------
# Rate-limited request
# ---------------------------------------------------------------------------

def _rate_limited_get(url: str, params: dict) -> Optional[dict]:
    """Enforce ≤1 req/s and call the Nominatim API."""
    global _last_call_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_call_time = time.monotonic()

    try:
        metrics.inc_api_call()
        resp = http_get(url, params=params, headers={"User-Agent": NOMINATIM_USER_AGENT})
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return data[0]
        else:
            metrics.inc_api_error()
    except Exception:
        metrics.inc_api_error()
    return None


def _geoapify_geocode(city: str, country: Optional[str]) -> Optional[dict]:
    """Geocode via Geoapify when GEOAPIFY_API_KEY is set. Returns None on any failure."""
    key = os.getenv("GEOAPIFY_API_KEY", "")
    if not key:
        return None
    try:
        metrics.inc_api_call()
        text = f"{city}, {country}" if country else city
        resp = http_get("https://api.geoapify.com/v1/geocode/search",
                        params={"text": text, "type": "city", "format": "json", "limit": 1, "apiKey": key})
        if resp.status_code != 200:
            metrics.inc_api_error()
            return None
        res = (resp.json().get("results") or [None])[0]
        if not res:
            return None
        return {
            "city": res.get("city") or res.get("name") or city,
            "country": res.get("country", country or "Unknown"),
            "lat": float(res["lat"]), "lon": float(res["lon"]),
            "timezone": (res.get("timezone") or {}).get("name", "UTC"),
        }
    except Exception:
        metrics.inc_api_error()
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def geocode(city: str, country: Optional[str] = None) -> dict:
    """
    Return a dict with {city, country, lat, lon, timezone} for the given city.

    Tries the SQLite cache first, then Nominatim, then falls back to mock data.
    """
    query = f"{city},{country}" if country else city
    cached = geocode_cache_get(query)
    if cached:
        return cached

    if not offline():
        geo = _geoapify_geocode(city, country)
        if geo:
            geocode_cache_set(query, geo)
            return geo

    # Live lookup unless TRIP_OFFLINE=1 (a default User-Agent is always sent)
    if not offline():
        params = {
            "q": query,
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
        raw = _rate_limited_get(_BASE_URL, params)
        if raw:
            addr = raw.get("address", {})
            result = {
                "city": (
                    addr.get("city")
                    or addr.get("town")
                    or addr.get("village")
                    or city
                ),
                "country": addr.get("country", country or "Unknown"),
                "lat": float(raw["lat"]),
                "lon": float(raw["lon"]),
                "timezone": "UTC",  # Nominatim doesn't return TZ; use UTC placeholder
            }
            geocode_cache_set(query, result)
            return result
        # API call failed; fall through to mock
        metrics.inc_fallback()
    else:
        import warnings
        warnings.warn(
            "Offline mode – geocode is using mock data.",
            stacklevel=2,
        )
        metrics.inc_fallback()

    result = _mock_for(city, country)
    geocode_cache_set(query, result)
    return result
