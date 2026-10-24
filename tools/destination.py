"""
tools/destination.py – Combines geocode, places, and currency into one
compact DestinationData object.

get_destination_data(city, country) is the single entry-point that other
agents use for destination research. It is safe to call repeatedly (results
are cached inside each individual tool).
"""

from __future__ import annotations

import datetime
from typing import Optional

from core.schemas import DestinationData, Place
from tools.currency import get_fx_rate
from tools.geocode import geocode
from tools.places import get_places

_MAX_PLACES = 40


def get_destination_data(city: str, country: Optional[str] = None) -> DestinationData:
    """
    Combine geocode + places + FX rate into a DestinationData object.

    :param city:    City name (e.g. "Kyoto")
    :param country: Optional country hint (e.g. "Japan")
    :returns:       Populated DestinationData; always returns something.
    """
    geo = geocode(city, country)

    raw_places = get_places(
        city=city,
        lat=geo["lat"],
        lon=geo["lon"],
    )

    # Normalise and cap at 40 places
    places: list[Place] = []
    is_mock = False
    for p in raw_places[:_MAX_PLACES]:
        if p.get("source") == "mock":
            is_mock = True
        places.append(
            Place(
                name=p["name"],
                category=p.get("category", "other"),
                lat=p.get("lat"),
                lon=p.get("lon"),
                price_level=p.get("price_level"),
                rating=p.get("rating"),
                est_cost_usd=p.get("est_cost_usd", 0.0),
                source=p.get("source", "unknown"),
            )
        )

    # Guess destination currency code from country (basic mapping)
    currency_code = _country_to_currency(geo.get("country", ""))
    fx_rate = get_fx_rate(currency_code, "USD")

    return DestinationData(
        city=geo["city"],
        country=geo["country"],
        lat=geo["lat"],
        lon=geo["lon"],
        timezone=geo.get("timezone", "UTC"),
        currency_code=currency_code,
        fx_rate_to_usd=fx_rate,
        places=places,
        fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        is_mock=is_mock,
    )


# ---------------------------------------------------------------------------
# Country → currency ISO 4217 (best-effort mapping)
# ---------------------------------------------------------------------------

_COUNTRY_CURRENCY: dict[str, str] = {
    "japan": "JPY",
    "japan (日本)": "JPY",
    "portugal": "EUR",
    "france": "EUR",
    "germany": "EUR",
    "spain": "EUR",
    "italy": "EUR",
    "peru": "PEN",
    "united states": "USD",
    "usa": "USD",
    "united states of america": "USD",
    "united kingdom": "GBP",
    "australia": "AUD",
    "canada": "CAD",
    "india": "INR",
    "thailand": "THB",
    "brazil": "BRL",
    "mexico": "MXN",
    "china": "CNY",
    "south korea": "KRW",
}


def _country_to_currency(country: str) -> str:
    return _COUNTRY_CURRENCY.get(country.lower(), "USD")
