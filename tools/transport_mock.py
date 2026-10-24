"""
tools/transport_mock.py – Mock transport options.

Returns train, bus, and flight options. All items have is_mock=True and
label "MOCK DATA - not live". No real API is called.
"""

from __future__ import annotations

from typing import List

from core.schemas import TransportOption

_MOCK_LABEL = "MOCK DATA - not live"


def get_transport_options(
    origin_city: str,
    destination_city: str,
) -> List[TransportOption]:
    """
    Return mock transport options between *origin_city* and *destination_city*.
    Always returns three options (train, bus, flight).
    """
    return [
        TransportOption(
            mode="train",
            provider="Mock Rail Co.",
            est_duration_min=120,
            est_price_usd=45.0,
            is_mock=True,
            label=_MOCK_LABEL,
        ),
        TransportOption(
            mode="bus",
            provider="Mock Bus Lines",
            est_duration_min=180,
            est_price_usd=20.0,
            is_mock=True,
            label=_MOCK_LABEL,
        ),
        TransportOption(
            mode="flight",
            provider="Mock Airways",
            est_duration_min=60,
            est_price_usd=150.0,
            is_mock=True,
            label=_MOCK_LABEL,
        ),
    ]
