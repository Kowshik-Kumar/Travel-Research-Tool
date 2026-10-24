"""
tools/weather.py – OpenWeatherMap 5-day / 3-hour forecast aggregated per day.

Formula for weather_score (documented):
    score = temp_score * 0.5 + dry_score * 0.5
    where:
        temp_score = clamp((avg_temp_c - 0) / 30, 0, 1)
            → 0°C scores 0; 30°C scores 1; above 30°C capped at 1
        dry_score  = 1 - rain_prob
            → 0% rain probability → 1.0; 100% → 0.0

The resulting score is in [0, 1] where 1 = warm and dry, 0 = freezing or
very rainy.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from typing import List

import metrics
from core.logging_utils import http_get
from core.config import offline
from core.schemas import WeatherDay

OWM_KEY: str = os.getenv("OPENWEATHER_API_KEY", "")
_OWM_URL = "https://api.openweathermap.org/data/2.5/forecast"
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"  # keyless, live

# ---------------------------------------------------------------------------
# Mock weather data
# ---------------------------------------------------------------------------

def _mock_weather(days: int = 5) -> List[WeatherDay]:
    import datetime
    today = datetime.date.today()
    return [
        WeatherDay(
            date=(today + datetime.timedelta(days=i)).isoformat(),
            avg_temp_c=22.0,
            rain_prob=0.1,
            score=0.72,
        )
        for i in range(days)
    ]


# ---------------------------------------------------------------------------
# Score formula
# ---------------------------------------------------------------------------

def _weather_score(avg_temp_c: float, rain_prob: float) -> float:
    """
    Compute a weather quality score in [0, 1].

    temp_score = clamp(avg_temp_c / 30, 0, 1)  (0°C → 0, 30°C → 1)
    dry_score  = 1 - rain_prob
    score      = temp_score * 0.5 + dry_score * 0.5
    """
    temp_score = max(0.0, min(1.0, avg_temp_c / 30.0))
    dry_score = 1.0 - max(0.0, min(1.0, rain_prob))
    return round(temp_score * 0.5 + dry_score * 0.5, 4)


def _open_meteo(lat: float, lon: float) -> List[WeatherDay]:
    """Live 7-day forecast from Open-Meteo (no API key). Returns [] on any failure."""
    try:
        metrics.inc_api_call()
        resp = http_get(
            _OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 7,
            },
        )
        if resp.status_code != 200:
            metrics.inc_api_error()
            return []
        d = resp.json().get("daily", {})
        out: List[WeatherDay] = []
        for i, day in enumerate(d.get("time", [])):
            tmax = d["temperature_2m_max"][i]
            tmin = d["temperature_2m_min"][i]
            pop = d.get("precipitation_probability_max", [None] * (i + 1))[i]
            if tmax is None or tmin is None:
                continue
            avg = (tmax + tmin) / 2
            rain = max(0.0, min(1.0, (pop or 0) / 100.0))
            out.append(WeatherDay(date=day, avg_temp_c=round(avg, 1), rain_prob=round(rain, 2),
                                  score=_weather_score(avg, rain)))
        return out
    except Exception:
        metrics.inc_api_error()
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_weather_forecast(lat: float, lon: float) -> List[WeatherDay]:
    """
    Return a list of WeatherDay objects for the next 5 days.

    Falls back to mock data when OPENWEATHER_API_KEY is missing or the call
    fails.
    """
    if not OWM_KEY:
        if not offline():
            live = _open_meteo(lat, lon)
            if live:
                return live
        import warnings
        warnings.warn(
            "OPENWEATHER_API_KEY not set and Open-Meteo unavailable – weather is using mock data.",
            stacklevel=2,
        )
        metrics.inc_fallback()
        return _mock_weather()

    try:
        metrics.inc_api_call()
        params = {
            "lat": lat,
            "lon": lon,
            "appid": OWM_KEY,
            "units": "metric",
            "cnt": 40,  # 5 days × 8 slots per day
        }
        resp = http_get(_OWM_URL, params=params)
        if resp.status_code != 200:
            metrics.inc_api_error()
            metrics.inc_fallback()
            return _mock_weather()

        data = resp.json()
        # Group 3-hour slots by date
        by_date: dict[str, dict] = defaultdict(lambda: {"temps": [], "pops": []})
        for entry in data.get("list", []):
            dt_txt = entry["dt_txt"][:10]  # "YYYY-MM-DD HH:MM:SS" → "YYYY-MM-DD"
            by_date[dt_txt]["temps"].append(entry["main"]["temp"])
            by_date[dt_txt]["pops"].append(entry.get("pop", 0.0))

        days: List[WeatherDay] = []
        for date_str in sorted(by_date.keys()):
            temps = by_date[date_str]["temps"]
            pops = by_date[date_str]["pops"]
            avg_temp = sum(temps) / len(temps)
            max_pop = max(pops)
            score = _weather_score(avg_temp, max_pop)
            days.append(
                WeatherDay(
                    date=date_str,
                    avg_temp_c=round(avg_temp, 1),
                    rain_prob=round(max_pop, 2),
                    score=score,
                )
            )
        return days or _mock_weather()

    except Exception as exc:
        metrics.inc_api_error()
        metrics.inc_fallback()
        import warnings
        warnings.warn(f"Weather forecast failed: {exc}", stacklevel=2)
        return _mock_weather()
