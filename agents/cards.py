"""
agents/cards.py - ASI:One Interactive Card payload builders + actionable links.

Cards are plain dicts matching the ASI1 card schemas (form / review / carousel).
They are shipped inside a ChatMessage as MetadataContent (see orchestrator).
Docs: https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse as _up
from typing import Any, Dict, List, Optional

CURRENCIES = ["USD", "EUR", "GBP", "INR", "JPY", "AUD", "CAD", "THB", "MXN", "BRL"]
INTERESTS = [
    ("", "Anything - surprise me"),
    ("museum", "Museums"),
    ("historic", "History & landmarks"),
    ("park", "Parks & nature"),
    ("viewpoint", "Viewpoints"),
    ("attraction", "Attractions"),
]


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def trip_form_card() -> Dict[str, Any]:
    return {
        "title": "Plan my trip",
        "fields": [
            {"kind": "text", "name": "destination_city", "label": "Destination city", "required": True},
            {"kind": "number", "name": "days", "label": "Number of days (1-7)", "required": True},
            {"kind": "number", "name": "budget", "label": "Total budget (activities)", "required": False},
            {"kind": "select", "name": "currency", "label": "Budget currency", "required": True,
             "options": [{"value": c, "label": c} for c in CURRENCIES]},
            {"kind": "select", "name": "interests", "label": "Main interest", "required": False,
             "options": [{"value": v, "label": l} for v, l in INTERESTS]},
        ],
        "submit_cta": {"label": "Plan my trip", "selection": {"action": "submit_trip"}},
    }


def review_card(title: str, rows: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "title": title,
        "summary_rows": rows,
        "approve_cta": {"label": "Confirm & get itinerary links", "primary": True,
                        "selection": {"action": "approve_plan"}},
        "reject_cta": {"label": "Change details", "selection": {"action": "reject_plan"}},
    }


def choice_carousel(title: str, options: List[Dict[str, str]]) -> Dict[str, Any]:
    """Disambiguation carousel, e.g. Paris FR vs Paris TX. options: {id,title,subtitle,city,country}."""
    return {
        "title": title,
        "items": [{
            "id": o["id"], "title": o["title"], "subtitle": o.get("subtitle", ""),
            "primary_cta": {"label": "Choose", "selection": {
                "action": "pick_destination", "destination_city": o["city"], "country": o["country"]}},
        } for o in options],
    }


# ---------------------------------------------------------------------------
# Action links (these are what make the outcome executable)
# ---------------------------------------------------------------------------

def maps_place_link(name: str, city: str, lat: Optional[float], lon: Optional[float]) -> str:
    q = f"{lat},{lon}" if lat is not None and lon is not None else f"{name} {city}"
    return "https://www.google.com/maps/search/?" + _up.urlencode({"api": 1, "query": q})


def maps_route_link(stops: List[Dict[str, Any]], mode: str = "walking") -> Optional[str]:
    pts = [f"{s['lat']},{s['lon']}" for s in stops if s.get("lat") is not None and s.get("lon") is not None]
    if len(pts) < 2:
        return None
    params = {"api": 1, "origin": pts[0], "destination": pts[-1], "travelmode": mode}
    if len(pts) > 2:
        params["waypoints"] = "|".join(pts[1:-1][:8])
    return "https://www.google.com/maps/dir/?" + _up.urlencode(params)


def gcal_link(title: str, date_iso: str, start_hhmm: str, duration_min: int,
              details: str = "", location: str = "") -> str:
    start = _dt.datetime.strptime(f"{date_iso} {start_hhmm}", "%Y-%m-%d %H:%M")
    end = start + _dt.timedelta(minutes=duration_min)
    fmt = "%Y%m%dT%H%M%S"
    params = {"action": "TEMPLATE", "text": title, "dates": f"{start.strftime(fmt)}/{end.strftime(fmt)}",
              "details": details, "location": location}
    return "https://calendar.google.com/calendar/render?" + _up.urlencode(params)
