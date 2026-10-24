"""core/config.py - tiny runtime switches shared by the tool layer."""
from __future__ import annotations

import os

DEFAULT_USER_AGENT = "TripPlannerAgent/1.0 (ASI:One Fetch-A-Thon agent)"


def offline() -> bool:
    """True when TRIP_OFFLINE=1: tools must not touch the network (tests, demos)."""
    return os.getenv("TRIP_OFFLINE", "").strip() in ("1", "true", "yes")
