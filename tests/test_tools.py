"""
tests/test_tools.py – Phase 2 tool tests with HTTP mocked.
Tests geocode, places, currency, routing, and weather tools.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from unittest.mock import patch, MagicMock

import pytest
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Geocode tests
# ---------------------------------------------------------------------------

class TestGeocode:
    def test_mock_fallback_when_no_agent(self, monkeypatch):
        """When NOMINATIM_USER_AGENT is empty, falls back to mock data."""
        monkeypatch.setenv("NOMINATIM_USER_AGENT", "")
        import importlib
        import tools.geocode as geo_mod
        importlib.reload(geo_mod)

        result = geo_mod.geocode("Kyoto", "Japan")
        assert result["city"] is not None
        assert result["lat"] is not None

    def test_kyoto_mock(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOMINATIM_USER_AGENT", "")
        monkeypatch.setenv("CACHE_DB", str(tmp_path / "geo_test.sqlite"))
        import importlib
        import core.storage as storage
        import tools.geocode as geo_mod
        importlib.reload(storage)
        importlib.reload(geo_mod)

        result = geo_mod.geocode("Kyoto", "Japan")
        assert abs(result["lat"] - 35.0117) < 0.1

    def test_lisbon_mock(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOMINATIM_USER_AGENT", "")
        monkeypatch.setenv("CACHE_DB", str(tmp_path / "geo_test2.sqlite"))
        import importlib
        import core.storage as storage
        import tools.geocode as geo_mod
        importlib.reload(storage)
        importlib.reload(geo_mod)

        result = geo_mod.geocode("Lisbon", "Portugal")
        assert abs(result["lat"] - 38.7167) < 0.1

    def test_cusco_mock(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOMINATIM_USER_AGENT", "")
        monkeypatch.setenv("CACHE_DB", str(tmp_path / "geo_test3.sqlite"))
        import importlib
        import core.storage as storage
        import tools.geocode as geo_mod
        importlib.reload(storage)
        importlib.reload(geo_mod)

        result = geo_mod.geocode("Cusco", "Peru")
        assert abs(result["lat"] - (-13.5317)) < 0.1

    def test_cache_used_on_second_call(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOMINATIM_USER_AGENT", "")
        monkeypatch.setenv("CACHE_DB", str(tmp_path / "geo_cache.sqlite"))
        import importlib
        import core.storage as storage
        import tools.geocode as geo_mod
        importlib.reload(storage)
        importlib.reload(geo_mod)

        r1 = geo_mod.geocode("Paris", "France")
        r2 = geo_mod.geocode("Paris", "France")
        assert r1 == r2


# ---------------------------------------------------------------------------
# Currency tests
# ---------------------------------------------------------------------------

class TestCurrency:
    def test_same_currency_returns_1(self):
        from tools.currency import get_fx_rate
        assert get_fx_rate("USD", "USD") == 1.0

    def test_fallback_when_api_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CACHE_DB", str(tmp_path / "fx_test.sqlite"))
        import importlib
        import core.storage as storage
        importlib.reload(storage)

        import tools.currency as currency_mod
        # Patch http_get to raise
        with patch("tools.currency.http_get", side_effect=Exception("network down")):
            rate = currency_mod.get_fx_rate("USD", "EUR")
        assert rate == 1.0  # fallback

    def test_frankfurter_response_parsed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRIP_OFFLINE", "0")
        monkeypatch.setenv("CACHE_DB", str(tmp_path / "fx_test2.sqlite"))
        import importlib
        import core.storage as storage
        importlib.reload(storage)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "amount": 1.0, "base": "USD", "date": date.today().isoformat(),
            "rates": {"EUR": 0.92}
        }
        import tools.currency as currency_mod
        with patch("tools.currency.http_get", return_value=mock_response):
            rate = currency_mod.get_fx_rate("USD", "EUR")
        assert abs(rate - 0.92) < 0.001


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------

class TestRouting:
    def test_null_matrix_when_no_key(self, monkeypatch):
        monkeypatch.setenv("ORS_API_KEY", "")
        import importlib
        import tools.routing as routing_mod
        importlib.reload(routing_mod)

        coords = [(35.01, 135.77), (35.02, 135.78), (35.03, 135.79)]
        matrix = routing_mod.get_travel_time_matrix(coords)
        assert len(matrix) == 3
        # No ORS key -> straight-line estimates (never None for valid coordinates)
        assert all(v is not None for row in matrix for v in row)
        assert matrix[0][0] == 0.0 and matrix[0][1] > 0

    def test_travel_minutes_from_center_with_null(self, monkeypatch):
        monkeypatch.setenv("ORS_API_KEY", "")
        import importlib
        import tools.routing as routing_mod
        importlib.reload(routing_mod)

        places = [{"lat": 35.01, "lon": 135.77}, {"lat": None, "lon": None}]
        times = routing_mod.travel_minutes_from_center(35.00, 135.76, places)
        assert len(times) == 2
        # Second place has no coords → None
        assert times[1] is None

    def test_ors_response_parsed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORS_API_KEY", "test-key")
        import importlib
        import tools.routing as routing_mod
        importlib.reload(routing_mod)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "durations": [[0, 600], [600, 0]]  # 600 seconds = 10 minutes
        }
        with patch("tools.routing.http_post", return_value=mock_resp):
            matrix = routing_mod.get_travel_time_matrix([(35.01, 135.77), (35.02, 135.78)])
        assert matrix[0][1] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Weather tests
# ---------------------------------------------------------------------------

class TestWeather:
    def test_mock_fallback_when_no_key(self, monkeypatch):
        monkeypatch.setenv("OPENWEATHER_API_KEY", "")
        import importlib
        import tools.weather as weather_mod
        importlib.reload(weather_mod)

        days = weather_mod.get_weather_forecast(35.01, 135.77)
        assert len(days) > 0
        for d in days:
            assert 0 <= d.score <= 1

    def test_weather_score_warm_dry(self, monkeypatch):
        monkeypatch.setenv("OPENWEATHER_API_KEY", "")
        import importlib
        import tools.weather as weather_mod
        importlib.reload(weather_mod)

        score = weather_mod._weather_score(30.0, 0.0)
        assert abs(score - 1.0) < 0.01  # warm and dry → 1.0

    def test_weather_score_cold_rainy(self, monkeypatch):
        monkeypatch.setenv("OPENWEATHER_API_KEY", "")
        import importlib
        import tools.weather as weather_mod
        importlib.reload(weather_mod)

        score = weather_mod._weather_score(0.0, 1.0)
        assert abs(score - 0.0) < 0.01  # freezing + rainy → 0.0

    def test_owm_response_parsed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENWEATHER_API_KEY", "test-key")
        import importlib
        import tools.weather as weather_mod
        importlib.reload(weather_mod)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "list": [
                {"dt_txt": "2027-01-01 00:00:00", "main": {"temp": 20.0}, "pop": 0.1},
                {"dt_txt": "2027-01-01 03:00:00", "main": {"temp": 22.0}, "pop": 0.05},
                {"dt_txt": "2027-01-02 00:00:00", "main": {"temp": 18.0}, "pop": 0.2},
            ]
        }
        with patch("tools.weather.http_get", return_value=mock_resp):
            days = weather_mod.get_weather_forecast(35.01, 135.77)
        assert len(days) == 2
        assert days[0].date == "2027-01-01"
        assert days[0].avg_temp_c == pytest.approx(21.0)


# ---------------------------------------------------------------------------
# Places tests
# ---------------------------------------------------------------------------

class TestPlaces:
    def test_mock_fallback_when_no_keys(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GEOAPIFY_API_KEY", "")
        monkeypatch.setenv("FOURSQUARE_API_KEY", "")
        monkeypatch.setenv("CACHE_DB", str(tmp_path / "places_test.sqlite"))
        import importlib
        import core.storage as storage
        import tools.places as places_mod
        importlib.reload(storage)
        importlib.reload(places_mod)

        result = places_mod.get_places("Kyoto", 35.01, 135.77)
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(p.get("source") == "mock" for p in result)
