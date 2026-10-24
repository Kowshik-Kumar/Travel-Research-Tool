"""
tools/currency.py – Daily-cached FX rate from Frankfurter (no API key needed).

Frankfurter docs: https://frankfurter.app/
One row per (base, target, date) is stored in the fx_rates SQLite table.
Returns the rate as a float (units of target per 1 unit of base).
Falls back to 1.0 (with a warning) on network failure.
"""

from __future__ import annotations

import datetime
import warnings

import metrics
from core.config import offline
from core.logging_utils import http_get
from core.storage import fx_rate_get, fx_rate_set

_BASE_URL = "https://api.frankfurter.app"


def get_fx_rate(base: str = "USD", target: str = "USD") -> float:
    """
    Return the exchange rate: how many *target* units equal 1 *base* unit.

    Results are cached daily in SQLite. Falls back to 1.0 on failure.
    """
    base = base.upper()
    target = target.upper()

    if base == target:
        return 1.0

    if offline():
        return 1.0

    today = datetime.date.today().isoformat()
    cached = fx_rate_get(base, target, today)
    if cached is not None:
        return cached

    try:
        metrics.inc_api_call()
        url = f"{_BASE_URL}/latest"
        params = {"from": base, "to": target}
        resp = http_get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            rate = data["rates"].get(target)
            if rate:
                fx_rate_set(base, target, today, float(rate))
                return float(rate)
        metrics.inc_api_error()
    except Exception as exc:
        metrics.inc_api_error()
        warnings.warn(f"FX rate lookup failed ({base}->{target}): {exc}", stacklevel=2)

    metrics.inc_fallback()
    warnings.warn(
        f"Falling back to FX rate 1.0 for {base}->{target} (mock/unreliable)",
        stacklevel=2,
    )
    return 1.0


def usd_to_currency(amount_usd: float, target_currency: str) -> float:
    """Convert *amount_usd* to *target_currency* using today's rate."""
    rate = get_fx_rate("USD", target_currency)
    return round(amount_usd * rate, 2)


def currency_to_usd(amount: float, source_currency: str) -> float:
    """Convert *amount* in *source_currency* to USD."""
    rate = get_fx_rate(source_currency, "USD")
    return round(amount * rate, 2)
