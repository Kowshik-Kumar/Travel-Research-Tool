"""
tools/places.py – Fetch and merge places from Geoapify, Foursquare and (keyless fallback) OpenStreetMap.

Both APIs are optional: if their keys are missing the tool falls back to
labelled mock data and records a fallback event in metrics.

Output shape (per place):
  {name, category, lat, lon, price_level, rating, est_cost_usd, source}
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Optional

import metrics
from core.config import DEFAULT_USER_AGENT, offline
from core.logging_utils import http_get, http_post
from core.storage import places_cache_get, places_cache_set

# ---------------------------------------------------------------------------
# API keys and config
# ---------------------------------------------------------------------------
FS_KEY: str = os.getenv("FOURSQUARE_API_KEY", "")

_GEOAPIFY_URL = "https://api.geoapify.com/v2/places"
_FS_BASE = "https://api.foursquare.com/v3/places/search"

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Deduplication threshold (metres)
_DEDUP_DIST_M = 150

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------
_MOCK_PLACES: list[dict[str, Any]] = [
    {
        "name": "Historic Temple (MOCK)",
        "category": "religion",
        "lat": None, "lon": None,
        "price_level": 1, "rating": 4.2,
        "est_cost_usd": 5.0, "source": "mock",
    },
    {
        "name": "Central Market (MOCK)",
        "category": "marketplace",
        "lat": None, "lon": None,
        "price_level": 2, "rating": 4.0,
        "est_cost_usd": 10.0, "source": "mock",
    },
    {
        "name": "City Museum (MOCK)",
        "category": "museums",
        "lat": None, "lon": None,
        "price_level": 2, "rating": 4.5,
        "est_cost_usd": 15.0, "source": "mock",
    },
    {
        "name": "Royal Garden (MOCK)",
        "category": "gardens",
        "lat": None, "lon": None,
        "price_level": 1, "rating": 4.3,
        "est_cost_usd": 0.0, "source": "mock",
    },
    {
        "name": "Panoramic Viewpoint (MOCK)",
        "category": "natural",
        "lat": None, "lon": None,
        "price_level": 1, "rating": 4.6,
        "est_cost_usd": 0.0, "source": "mock",
    },
]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two lat/lon points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _normalise(raw: dict, source: str) -> dict:
    """Map raw API fields to the shared place shape."""
    return {
        "name": raw.get("name", "Unknown"),
        "category": raw.get("kinds", raw.get("category", "other")).split(",")[0] if raw.get("kinds") else raw.get("category", "other"),
        "lat": raw.get("point", {}).get("lat") if "point" in raw else raw.get("lat"),
        "lon": raw.get("point", {}).get("lon") if "point" in raw else raw.get("lon"),
        "price_level": raw.get("price_level"),
        "rating": raw.get("rate") or raw.get("rating"),
        "est_cost_usd": _estimate_cost(raw.get("price_level")),
        "source": source,
    }


def _normalise_foursquare(raw: dict) -> dict:
    """Map Foursquare v3 result to the shared place shape."""
    geo = raw.get("geocodes", {}).get("main", {})
    cats = raw.get("categories", [])
    category = cats[0].get("name", "other") if cats else "other"
    price = raw.get("price")  # 1-4 in Foursquare
    rating = raw.get("rating")  # 0-10 in Foursquare → convert to 0-5
    return {
        "name": raw.get("name", "Unknown"),
        "category": category,
        "lat": geo.get("latitude"),
        "lon": geo.get("longitude"),
        "price_level": price,
        "rating": round(rating / 2, 1) if rating is not None else None,
        "est_cost_usd": _estimate_cost(price),
        "source": "foursquare",
    }


def _estimate_cost(price_level: Optional[int]) -> float:
    mapping = {1: 5.0, 2: 15.0, 3: 35.0, 4: 80.0}
    return mapping.get(price_level, 10.0)  # type: ignore[arg-type]


def _dedup(places: list[dict]) -> list[dict]:
    """Remove near-duplicate places (same name or within _DEDUP_DIST_M metres)."""
    unique: list[dict] = []
    for p in places:
        is_dup = False
        for u in unique:
            if p["name"].lower() == u["name"].lower():
                is_dup = True
                break
            if p["lat"] and p["lon"] and u["lat"] and u["lon"]:
                dist = _haversine(p["lat"], p["lon"], u["lat"], u["lon"])
                if dist < _DEDUP_DIST_M:
                    is_dup = True
                    break
        if not is_dup:
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Geoapify Places API  (https://apidocs.geoapify.com/docs/places/)
# ---------------------------------------------------------------------------

_GEOAPIFY_CATEGORIES = (
    "tourism.attraction,tourism.sights,entertainment.museum,entertainment.culture,"
    "leisure.park,heritage,natural"
)
_GEO_COST = {"museum": 12.0, "culture": 10.0, "park": 0.0, "heritage": 0.0, "natural": 0.0,
             "attraction": 5.0, "sights": 0.0}


_JUNK_RE = re.compile(r"^[A-Za-z]{1,4}[_\- ]?\d+[A-Za-z]?$|^\W*$")


def _is_junk_name(name: str) -> bool:
    """Drop unnamed-looking OSM ids like 'PA_873' or one/two-letter names."""
    n = name.strip()
    return len(n) < 3 or bool(_JUNK_RE.match(n))


def _normalise_geoapify(feat: dict) -> Optional[dict]:
    pr = feat.get("properties", {})
    name = pr.get("name")
    lat, lon = pr.get("lat"), pr.get("lon")
    if not name or lat is None or lon is None:
        return None
    cats = pr.get("categories", [])
    leaf = (cats[0] if cats else "attraction").split(".")
    key = next((k for k in ("museum", "culture", "park", "heritage", "natural", "sights", "attraction")
                if k in leaf or any(k in c for c in cats)), "attraction")
    category = {"museum": "museum", "culture": "museum", "park": "park", "heritage": "historic",
                "natural": "park", "sights": "attraction", "attraction": "attraction"}[key]
    raw = pr.get("datasource", {}).get("raw", {}) or {}
    cost = 0.0 if raw.get("fee") == "no" else _GEO_COST[key]
    if _is_junk_name(name):
        return None
    prominence = (sum(k in raw for k in ("wikipedia", "wikidata", "website", "opening_hours"))
                  + (1 if pr.get("website") else 0) + (2 if pr.get("wiki_and_media") else 0)
                  + (1 if any("tourism" in c for c in cats) else 0))
    # minor street furniture / pocket spaces are poor itinerary stops: push them down
    strong = any(t in c for c in cats for t in ("museum", "castle", "unesco", "heritage", "tourism.attraction"))
    minor = (not strong and prominence < 3 and
             any(t in c for c in cats for t in ("fountain", "artwork", "memorial", "square", "playground",
                                               "sport", "pitch", "bench", "cemetery")))
    rating = max(0.0, min(5.0, 2.6 + 0.4 * prominence - (1.2 if minor else 0.0)))
    return {
        "name": name, "category": category, "lat": float(lat), "lon": float(lon),
        "price_level": None,
        "rating": round(rating, 1),  # Geoapify has no ratings: prominence proxy
        "est_cost_usd": cost, "source": "geoapify",
    }


def _fetch_geoapify(lat: float, lon: float, radius_m: int = 5000) -> list[dict]:
    key = os.getenv("GEOAPIFY_API_KEY", "")
    if not key or offline():
        return []
    try:
        metrics.inc_api_call()
        resp = None
        for limit in (300, 100):          # bigger pool first; fall back if the plan rejects it
            resp = http_get(_GEOAPIFY_URL, params={
                "categories": _GEOAPIFY_CATEGORIES,
                "filter": f"circle:{lon},{lat},{radius_m}",
                "bias": f"proximity:{lon},{lat}",
                "limit": limit,
                "apiKey": key,
            })
            if resp.status_code == 200:
                break
        if resp is None or resp.status_code != 200:
            metrics.inc_api_error()
            return []
        out = [n for n in (_normalise_geoapify(f) for f in resp.json().get("features", [])) if n]
        # most notable first (rating is a prominence proxy), then keep a useful shortlist
        out.sort(key=lambda x: -(x.get("rating") or 0))
        return out[:40]
    except Exception:
        metrics.inc_api_error()
        return []


# ---------------------------------------------------------------------------
# Foursquare
# ---------------------------------------------------------------------------

def _fetch_foursquare(lat: float, lon: float, radius_m: int = 5000) -> list[dict]:
    if not FS_KEY:
        return []
    try:
        metrics.inc_api_call()
        params = {
            "ll": f"{lat},{lon}",
            "radius": radius_m,
            "limit": 50,
        }
        resp = http_get(
            _FS_BASE,
            params=params,
            headers={"Authorization": FS_KEY},
        )
        if resp.status_code != 200:
            metrics.inc_api_error()
            return []
        data = resp.json()
        return [_normalise_foursquare(r) for r in data.get("results", [])]
    except Exception:
        metrics.inc_api_error()
        return []


# ---------------------------------------------------------------------------
# OpenStreetMap / Overpass (keyless, live)
# ---------------------------------------------------------------------------

_OSM_COST = {  # rough entry-fee estimates in USD when OSM says nothing
    "museum": 12.0, "gallery": 10.0, "zoo": 20.0, "theme_park": 40.0,
    "castle": 8.0, "attraction": 0.0, "viewpoint": 0.0, "park": 0.0,
    "monument": 0.0, "memorial": 0.0, "ruins": 5.0, "archaeological_site": 8.0,
}


def _osm_query(lat: float, lon: float, radius_m: int) -> str:
    a = f"(around:{radius_m},{lat},{lon})"
    return (
        "[out:json][timeout:25];("
        f'nwr["tourism"~"^(attraction|museum|gallery|viewpoint|zoo|theme_park)$"]["name"]{a};'
        f'nwr["historic"~"^(monument|castle|memorial|ruins|archaeological_site)$"]["name"]{a};'
        f'nwr["leisure"="park"]["name"]["wikidata"]{a};'
        ");out center 120;"
    )


def _normalise_osm(el: dict) -> Optional[dict]:
    tags = el.get("tags", {})
    name = tags.get("name:en") or tags.get("name")
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    if not name or lat is None or lon is None or _is_junk_name(name):
        return None
    kind = (tags.get("tourism") or tags.get("historic") or
            ("park" if tags.get("leisure") == "park" else "attraction"))
    cat = {"theme_park": "attraction", "archaeological_site": "historic",
           "monument": "historic", "memorial": "historic", "ruins": "historic",
           "castle": "historic"}.get(kind, kind)
    cost = _OSM_COST.get(kind, 5.0)
    if tags.get("fee") == "no":
        cost = 0.0
    prominence = sum(k in tags for k in ("wikipedia", "wikidata", "website", "opening_hours"))
    return {
        "name": name, "category": cat, "lat": float(lat), "lon": float(lon),
        "price_level": None,
        # OSM has no ratings: use prominence (wikipedia/wikidata/etc.) as a 3.0-5.0 proxy
        "rating": round(min(5.0, 3.0 + 0.5 * prominence), 1),
        "est_cost_usd": cost, "source": "osm", "_prom": prominence,
    }


def _fetch_osm(lat: float, lon: float, radius_m: int = 6000) -> list[dict]:
    if offline():
        return []
    try:
        metrics.inc_api_call()
        resp = http_post(
            _OVERPASS_URL, data={"data": _osm_query(lat, lon, radius_m)},
            headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=40,
        )
        if resp.status_code != 200:
            metrics.inc_api_error()
            return []
        items = [n for n in (_normalise_osm(e) for e in resp.json().get("elements", [])) if n]
        items.sort(key=lambda x: -x["_prom"])
        for i in items:
            i.pop("_prom", None)
        return items[:40]
    except Exception:
        metrics.inc_api_error()
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_places(city: str, lat: float, lon: float, radius_m: int = 5000) -> list[dict]:
    """
    Fetch, merge, and deduplicate places for *city* centred on *(lat, lon)*.
    Results are cached in SQLite per city.
    """
    city_key = "v5:" + city.lower().strip()   # bump prefix to invalidate stale/poor cached results
    cached = places_cache_get(city_key)
    if cached is not None:
        return cached

    geo_places = _fetch_geoapify(lat, lon, radius_m)
    fs_places = _fetch_foursquare(lat, lon, radius_m)
    combined = geo_places + fs_places
    if not combined:
        combined = _fetch_osm(lat, lon, radius_m)

    if not combined and not offline():
        # Sparse area (e.g. a region/state centroid): retry with a wider radius
        wide = radius_m * 4
        combined = _fetch_geoapify(lat, lon, wide) or _fetch_osm(lat, lon, wide)

    if not combined:
        # All sources failed or returned nothing – use mock, but NEVER cache it
        metrics.inc_fallback()
        return list(_MOCK_PLACES)

    combined = [p for p in combined if not _is_junk_name(p.get("name", ""))]
    combined.sort(key=lambda x: -(x.get("rating") or 0))   # most notable first, then dedup keeps the best
    combined = _dedup(combined)[:40]
    places_cache_set(city_key, combined)
    return combined
