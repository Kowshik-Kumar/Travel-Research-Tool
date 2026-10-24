"""
tests/test_research.py – 6+ tests for agents/research.py.
All external HTTP calls are mocked via httpx MockTransport or monkeypatching.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_dest_data(city="Kyoto", country="Japan", is_mock=False, places=None):
    """Build a minimal DestinationData for mocking."""
    from core.schemas import DestinationData, Place
    import datetime
    default_places = [
        Place(name="Temple A", category="religion", lat=35.01, lon=135.77,
              price_level=1, rating=4.5, est_cost_usd=5.0, source="opentripmap"),
        Place(name="Museum B", category="museums", lat=35.02, lon=135.78,
              price_level=2, rating=4.2, est_cost_usd=15.0, source="foursquare"),
        Place(name="Park C", category="parks", lat=35.03, lon=135.79,
              price_level=1, rating=4.0, est_cost_usd=0.0, source="opentripmap"),
        Place(name="Market D", category="marketplace", lat=35.04, lon=135.80,
              price_level=2, rating=3.8, est_cost_usd=10.0, source="foursquare"),
    ]
    return DestinationData(
        city=city,
        country=country,
        lat=35.0117,
        lon=135.7683,
        timezone="Asia/Tokyo",
        currency_code="JPY",
        fx_rate_to_usd=0.0067,
        places=places if places is not None else default_places,
        fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        is_mock=is_mock,
    )


def _make_weather():
    from core.schemas import WeatherDay
    return [
        WeatherDay(date="2027-01-01", avg_temp_c=18.0, rain_prob=0.1, score=0.75),
        WeatherDay(date="2027-01-02", avg_temp_c=20.0, rain_prob=0.05, score=0.81),
    ]


def _null_routing(center_lat, center_lon, places):
    """Mock that returns None for all travel times."""
    return [None] * len(places)


def _fixed_routing(center_lat, center_lon, places):
    """Mock that returns increasing travel times."""
    return [float(i * 5) for i in range(len(places))]


# ---------------------------------------------------------------------------
# Test 1: Filtering – rating below 2.5 excluded
# ---------------------------------------------------------------------------

def test_filtering_low_rating():
    from core.schemas import DestinationData, Place
    import datetime
    places_with_bad = [
        Place(name="Good Place", category="museum", lat=35.01, lon=135.77,
              price_level=2, rating=4.0, est_cost_usd=10.0, source="opentripmap"),
        Place(name="Bad Place", category="museum", lat=35.02, lon=135.78,
              price_level=1, rating=2.0, est_cost_usd=5.0, source="opentripmap"),  # below 2.5
    ]
    dest = _make_dest_data(places=places_with_bad)

    with (
        patch("agents.research.get_destination_data", return_value=dest),
        patch("agents.research.travel_minutes_from_center", side_effect=_null_routing),
        patch("agents.research.get_weather_forecast", return_value=_make_weather()),
    ):
        from agents.research import research
        result = research({"destination_city": "Kyoto", "country": "Japan", "days": 3})

    assert result["status"] == "ok"
    names = [r["name"] for r in result["ranked"]]
    assert "Bad Place" not in names
    assert "Good Place" in names


# ---------------------------------------------------------------------------
# Test 2: Ranking order – cheaper and closer should rank higher
# ---------------------------------------------------------------------------

def test_ranking_order():
    from core.schemas import Place
    # Place A: cheap + close; Place B: expensive + far
    places = [
        Place(name="Expensive Far", category="museum", lat=35.10, lon=135.90,
              price_level=4, rating=4.0, est_cost_usd=80.0, source="opentripmap"),
        Place(name="Cheap Close", category="museum", lat=35.01, lon=135.77,
              price_level=1, rating=4.0, est_cost_usd=5.0, source="opentripmap"),
    ]
    dest = _make_dest_data(places=places)

    def routing(cl, clon, pl):
        return [60.0, 5.0]  # [ExpensiveFar, CheapClose] times

    with (
        patch("agents.research.get_destination_data", return_value=dest),
        patch("agents.research.travel_minutes_from_center", side_effect=routing),
        patch("agents.research.get_weather_forecast", return_value=_make_weather()),
    ):
        from agents.research import research
        result = research({"destination_city": "Kyoto", "country": "Japan", "days": 3})

    assert result["status"] == "ok"
    ranked = result["ranked"]
    assert len(ranked) >= 2
    names = [r["name"] for r in ranked]
    assert names.index("Cheap Close") < names.index("Expensive Far")


# ---------------------------------------------------------------------------
# Test 3: Weight override
# ---------------------------------------------------------------------------

def test_weight_override():
    from core.schemas import Place
    places = [
        Place(name="Place X", category="museum", lat=35.01, lon=135.77,
              price_level=1, rating=4.0, est_cost_usd=5.0, source="opentripmap"),
        Place(name="Place Y", category="museum", lat=35.05, lon=135.80,
              price_level=4, rating=4.0, est_cost_usd=80.0, source="opentripmap"),
    ]
    dest = _make_dest_data(places=places)

    with (
        patch("agents.research.get_destination_data", return_value=dest),
        patch("agents.research.travel_minutes_from_center", side_effect=_null_routing),
        patch("agents.research.get_weather_forecast", return_value=_make_weather()),
    ):
        from agents.research import research
        # Weight override: price=1.0 (only price matters)
        result = research(
            {"destination_city": "Kyoto", "country": "Japan", "days": 3},
            weights={"price": 1.0, "travel_time": 0.0, "weather": 0.0},
        )

    assert result["status"] == "ok"
    ranked = result["ranked"]
    # With travel_time=None (null routing), Place X should rank first (cheaper)
    assert ranked[0]["name"] == "Place X"


# ---------------------------------------------------------------------------
# Test 4: Missing geocode goes last
# ---------------------------------------------------------------------------

def test_missing_geocode_goes_last():
    from core.schemas import Place
    places = [
        Place(name="No Coords", category="museum", lat=None, lon=None,
              price_level=1, rating=4.5, est_cost_usd=0.0, source="opentripmap"),
        Place(name="Has Coords", category="museum", lat=35.01, lon=135.77,
              price_level=2, rating=4.0, est_cost_usd=15.0, source="opentripmap"),
    ]
    dest = _make_dest_data(places=places)

    def routing(cl, clon, pl):
        # Only "Has Coords" has coordinates; return [None, 10.0]
        return [None if p.get("lat") is None else 10.0 for p in pl]

    with (
        patch("agents.research.get_destination_data", return_value=dest),
        patch("agents.research.travel_minutes_from_center", side_effect=routing),
        patch("agents.research.get_weather_forecast", return_value=_make_weather()),
    ):
        from agents.research import research
        result = research({"destination_city": "Kyoto", "country": "Japan", "days": 3})

    assert result["status"] == "ok"
    names = [r["name"] for r in result["ranked"]]
    # "Has Coords" should rank before "No Coords"
    assert names.index("Has Coords") < names.index("No Coords")


# ---------------------------------------------------------------------------
# Test 5: Bad request returns error (not exception)
# ---------------------------------------------------------------------------

def test_bad_request_returns_error():
    from agents.research import research
    result = research({"destination_city": None, "days": 3})
    assert result["status"] == "error"
    assert result["error"] is not None


def test_bad_request_invalid_field():
    from agents.research import research
    # destination_city missing entirely
    result = research({"country": "Japan", "days": "not_a_number"})
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Test 6: Mock fallback sets is_mock=True
# ---------------------------------------------------------------------------

def test_mock_fallback_sets_is_mock():
    dest = _make_dest_data(is_mock=True)
    # All places from mock source
    for p in dest.places:
        p.source = "mock"

    with (
        patch("agents.research.get_destination_data", return_value=dest),
        patch("agents.research.travel_minutes_from_center", side_effect=_null_routing),
        patch("agents.research.get_weather_forecast", return_value=_make_weather()),
    ):
        from agents.research import research
        result = research({"destination_city": "Kyoto", "country": "Japan", "days": 3})

    assert result["status"] == "ok"
    assert all(r["is_mock"] for r in result["ranked"])


# ---------------------------------------------------------------------------
# Test 7: Duplicates removed
# ---------------------------------------------------------------------------

def test_duplicates_removed():
    from core.schemas import Place
    places = [
        Place(name="Temple A", category="religion", lat=35.01, lon=135.77,
              rating=4.5, est_cost_usd=5.0, source="opentripmap"),
        Place(name="Temple A", category="religion", lat=35.01, lon=135.77,
              rating=4.5, est_cost_usd=5.0, source="foursquare"),  # duplicate name
    ]
    dest = _make_dest_data(places=places)

    with (
        patch("agents.research.get_destination_data", return_value=dest),
        patch("agents.research.travel_minutes_from_center", side_effect=_null_routing),
        patch("agents.research.get_weather_forecast", return_value=_make_weather()),
    ):
        from agents.research import research
        result = research({"destination_city": "Kyoto", "country": "Japan", "days": 3})

    names = [r["name"] for r in result["ranked"]]
    assert names.count("Temple A") == 1
