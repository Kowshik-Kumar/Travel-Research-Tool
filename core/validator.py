"""
core/validator.py – Plan validation rules.

validate(plan, trip_request) returns a list of human-readable violation strings.
An empty list means the plan is valid.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List

from core.schemas import Plan, TripRequest


def validate(plan: Plan, trip_request: TripRequest) -> List[str]:
    """
    Validate *plan* against *trip_request*.

    Rules:
    1. total_cost_usd ≤ budget_usd (when budget is set)
    2. Travel time between consecutive activities ≤ 90 minutes
    3. At most 5 activities per day
    4. Dates are valid ISO dates, in ascending order, and not in the past
    5. No duplicate activity names across the whole trip
    6. Number of days in the plan matches trip_request.days

    Returns a list of violation strings; empty means valid.
    """
    violations: List[str] = []
    today = date.today()

    # Rule 1: budget
    if trip_request.budget is not None and plan.budget_usd > 0:
        if plan.total_cost_usd > plan.budget_usd + 0.005:  # small float tolerance
            violations.append(
                f"Budget exceeded: {plan.total_cost_usd:.2f} USD > {plan.budget_usd:.2f} USD"
            )

    # Parse and validate dates
    parsed_dates: List[date] = []
    for day in plan.days:
        try:
            parsed_dates.append(datetime.strptime(day.date, "%Y-%m-%d").date())
        except ValueError:
            violations.append(f"Invalid date format: {day.date!r} (expected YYYY-MM-DD)")

    # Rule 4a: past dates
    for d in parsed_dates:
        if d < today:
            violations.append(f"Date {d.isoformat()} is in the past")

    # Rule 4b: ascending order
    for i in range(1, len(parsed_dates)):
        if parsed_dates[i] <= parsed_dates[i - 1]:
            violations.append(
                f"Dates not in order: {parsed_dates[i - 1]} then {parsed_dates[i]}"
            )

    # Rule 6: day count
    if trip_request.days is not None and len(plan.days) != trip_request.days:
        violations.append(
            f"Day count mismatch: plan has {len(plan.days)} days, request asked for {trip_request.days}"
        )

    seen_names: set[str] = set()

    for day in plan.days:
        # Rule 3: at most 5 activities per day
        if len(day.activities) > 5:
            violations.append(
                f"Day {day.date} has {len(day.activities)} activities (max 5)"
            )

        for i, act in enumerate(day.activities):
            # Rule 5: duplicate names
            lower_name = act.name.lower()
            if lower_name in seen_names:
                violations.append(f"Duplicate activity name: {act.name!r}")
            seen_names.add(lower_name)

            # Rule 2: travel time ≤ 90 min between consecutive activities
            if i > 0 and act.travel_minutes_from_previous is not None:
                if act.travel_minutes_from_previous > 90:
                    violations.append(
                        f"Travel time {act.travel_minutes_from_previous:.0f} min "
                        f"from {day.activities[i - 1].name!r} to {act.name!r} exceeds 90 min"
                    )

    return violations
