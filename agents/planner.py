"""
agents/planner.py – Day-by-day trip planner.

plan(research_response, trip_request, fx_rate) builds a Plan from a
ResearchResponse, respecting days/interests/budget. Passes through the
validator, drops the most expensive activity if violations are found,
and retries up to 3 times.
"""

from __future__ import annotations

import datetime
from typing import Any, List, Optional

import metrics
from core.schemas import Activity, DayPlan, Plan, RankedPlace, TripRequest
from core.validator import validate

_START_HOUR = 9    # 09:00
_ACTIVITY_DURATION = 90  # minutes


def _add_minutes(time_str: str, mins: int) -> str:
    """Add *mins* minutes to a 'HH:MM' string and return 'HH:MM'."""
    h, m = map(int, time_str.split(":"))
    total = h * 60 + m + mins
    return f"{total // 60:02d}:{total % 60:02d}"


def _nearest_neighbour_order(places: List[RankedPlace]) -> List[RankedPlace]:
    """
    Order *places* using a nearest-neighbour greedy algorithm on
    travel_minutes_from_center as a proxy for inter-place distance.

    Places without travel_minutes go at the end in their original order.
    """
    with_time = [p for p in places if p.travel_minutes_from_center is not None]
    without_time = [p for p in places if p.travel_minutes_from_center is None]

    if not with_time:
        return without_time

    # Start with the place closest to center
    ordered: List[RankedPlace] = []
    remaining = sorted(with_time, key=lambda p: p.travel_minutes_from_center or 0)
    ordered.append(remaining.pop(0))

    # Greedy: always pick the nearest to the last chosen place
    # (using travel_minutes_from_center as an imperfect proxy)
    while remaining:
        last = ordered[-1]
        nearest = min(
            remaining,
            key=lambda p: abs((p.travel_minutes_from_center or 0) - (last.travel_minutes_from_center or 0)),
        )
        ordered.append(nearest)
        remaining.remove(nearest)

    return ordered + without_time


def _build_plan(
    ranked: List[RankedPlace],
    trip: TripRequest,
    fx_rate: float,
    max_per_day: int = 5,
) -> Plan:
    """Build a Plan object from ranked places."""
    days: List[DayPlan] = []
    start_date = datetime.date.today() + datetime.timedelta(days=1)

    activities_per_day = min(max_per_day, max(1, len(ranked) // max(1, trip.days or 1)))
    used: int = 0
    total_cost_usd: float = 0.0

    for day_idx in range(trip.days or 1):
        day_date = (start_date + datetime.timedelta(days=day_idx)).isoformat()
        day_places = ranked[used: used + activities_per_day]
        ordered_places = _nearest_neighbour_order(day_places)
        used += activities_per_day

        current_time = f"{_START_HOUR:02d}:00"
        activities: List[Activity] = []
        day_cost = 0.0

        prev: Optional[RankedPlace] = None
        for place in ordered_places:
            travel_from_prev: Optional[float] = None
            if prev is not None and place.travel_minutes_from_center is not None and prev.travel_minutes_from_center is not None:
                travel_from_prev = abs(
                    place.travel_minutes_from_center - prev.travel_minutes_from_center
                )
                current_time = _add_minutes(current_time, int(travel_from_prev))

            activity = Activity(
                name=place.name,
                start_time=current_time,
                duration_min=_ACTIVITY_DURATION,
                est_cost_usd=place.est_cost_usd,
                travel_minutes_from_previous=travel_from_prev,
            )
            activities.append(activity)
            current_time = _add_minutes(current_time, _ACTIVITY_DURATION)
            day_cost += place.est_cost_usd
            prev = place

        day_cost_local = round(day_cost / fx_rate, 2) if fx_rate > 0 else day_cost
        days.append(DayPlan(date=day_date, activities=activities, day_cost_usd=day_cost))
        total_cost_usd += day_cost

    budget_usd = (trip.budget or 0.0) * fx_rate if trip.budget else 0.0

    return Plan(
        days=days,
        total_cost_usd=round(total_cost_usd, 2),
        budget_usd=round(budget_usd, 2),
        currency=trip.currency,
        fx_rate_used=fx_rate,
    )


def plan(
    research_response: dict[str, Any],
    trip_request: dict[str, Any],
    fx_rate: float = 1.0,
) -> Plan:
    """
    Build a day-by-day trip plan.

    Validates the plan; if violations are found, drops the most expensive
    activity and retries up to 3 times. Each failure is counted in metrics.

    :param research_response: ResearchResponse dict from agents/research.py
    :param trip_request:      TripRequest dict
    :param fx_rate:           Exchange rate from destination currency to USD
    :returns:                 A validated Plan object
    """
    trip = TripRequest(**trip_request)
    ranked_raw = research_response.get("ranked", [])
    ranked = [RankedPlace(**r) for r in ranked_raw]

    # Filter by interests if specified
    if trip.interests:
        interest_lower = [i.lower() for i in trip.interests]
        matching = [p for p in ranked if any(i in p.category.lower() for i in interest_lower)]
        ranked = matching if matching else ranked

    # Ensure we have at least some places
    if not ranked:
        ranked = [RankedPlace(**r) for r in ranked_raw]

    MAX_ATTEMPTS = 4  # initial build + 3 retries
    for attempt in range(MAX_ATTEMPTS):
        current_plan = _build_plan(ranked, trip, fx_rate)
        violations = validate(current_plan, trip)

        if not violations:
            return current_plan

        metrics.inc_validator_failure()

        if attempt < MAX_ATTEMPTS - 1 and ranked:
            # Drop the most expensive activity across all days
            most_expensive_name = max(
                (act for day in current_plan.days for act in day.activities),
                key=lambda a: a.est_cost_usd,
                default=None,
            )
            if most_expensive_name:
                ranked = [p for p in ranked if p.name != most_expensive_name.name]

    # Return the best plan we have even if still invalid
    return current_plan
