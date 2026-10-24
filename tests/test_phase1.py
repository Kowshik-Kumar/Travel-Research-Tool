"""
tests/test_phase1.py – Phase 1: storage, schemas, metrics, validator tests.
All tests are offline (no network, no API keys).
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Storage tests
# ---------------------------------------------------------------------------

def test_http_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB", str(tmp_path / "test.sqlite"))
    # Re-import to pick up new env var
    import importlib
    import core.storage as storage
    importlib.reload(storage)

    storage.http_cache_set("key1", {"hello": "world"}, status_code=200, ttl_seconds=3600)
    result = storage.http_cache_get("key1")
    assert result is not None
    assert result["body"] == {"hello": "world"}
    assert result["status_code"] == 200


def test_http_cache_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB", str(tmp_path / "test2.sqlite"))
    import importlib
    import core.storage as storage
    importlib.reload(storage)

    storage.http_cache_set("expiring", {"x": 1}, ttl_seconds=0)
    result = storage.http_cache_get("expiring")
    assert result is None


def test_geocode_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB", str(tmp_path / "test3.sqlite"))
    import importlib
    import core.storage as storage
    importlib.reload(storage)

    data = {"city": "Tokyo", "country": "Japan", "lat": 35.6762, "lon": 139.6503, "timezone": "Asia/Tokyo"}
    storage.geocode_cache_set("Tokyo,Japan", data)
    result = storage.geocode_cache_get("Tokyo,Japan")
    assert result is not None
    assert result["city"] == "Tokyo"
    assert result["lat"] == 35.6762


def test_fx_rate_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB", str(tmp_path / "test4.sqlite"))
    import importlib
    import core.storage as storage
    importlib.reload(storage)

    storage.fx_rate_set("USD", "EUR", "2026-01-01", 0.91)
    result = storage.fx_rate_get("USD", "EUR", "2026-01-01")
    assert result == pytest.approx(0.91)
    assert storage.fx_rate_get("USD", "EUR", "2026-01-02") is None


def test_call_log(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB", str(tmp_path / "test5.sqlite"))
    import importlib
    import core.storage as storage
    importlib.reload(storage)

    storage.log_call("GET", "api.example.com", "/v1/data", status=200, latency_ms=123.4)
    storage.log_call("POST", "api.example.com", "/v1/write", status=500, is_error=True)
    logs = storage.fetch_call_logs(limit=10)
    assert len(logs) == 2
    assert any(l["is_error"] for l in logs)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_trip_request_defaults():
    from core.schemas import TripRequest
    t = TripRequest(destination_city="Paris")
    assert t.currency == "USD"
    assert t.interests == []
    assert t.needs_clarification is False


def test_trip_request_currency_validation():
    from core.schemas import TripRequest
    import pydantic
    with pytest.raises((pydantic.ValidationError, ValueError)):
        TripRequest(destination_city="Paris", currency="INVALID")


def test_plan_schema():
    from core.schemas import Activity, DayPlan, Plan
    a = Activity(name="Museum", start_time="09:00", duration_min=90, est_cost_usd=15.0)
    d = DayPlan(date="2027-01-01", activities=[a], day_cost_usd=15.0)
    p = Plan(days=[d], total_cost_usd=15.0, budget_usd=500.0, currency="USD", fx_rate_used=1.0)
    assert p.total_cost_usd == 15.0
    assert len(p.days) == 1


# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------

def test_metrics_counters():
    import metrics
    before = metrics.snapshot()["request_count"]
    metrics.inc_request()
    metrics.inc_success()
    after = metrics.snapshot()
    assert after["request_count"] == before + 1


def test_metrics_latency_p95():
    import metrics
    # Reset by recording known values
    for ms in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
        metrics.record_latency(ms)
    snap = metrics.snapshot()
    assert snap["p95_latency_ms"] is not None
    assert snap["p95_latency_ms"] > 0


# ---------------------------------------------------------------------------
# Validator tests
# ---------------------------------------------------------------------------

def _make_plan(days=3, cost=100.0, budget=500.0, activities_per_day=2):
    from core.schemas import Activity, DayPlan, Plan
    start = date.today() + timedelta(days=1)
    plan_days = []
    seen = 0
    for i in range(days):
        acts = [
            Activity(
                name=f"Activity {seen + j + 1}",
                start_time=f"{9 + j:02d}:00",
                duration_min=90,
                est_cost_usd=cost / (days * activities_per_day),
                travel_minutes_from_previous=20.0 if j > 0 else None,
            )
            for j in range(activities_per_day)
        ]
        seen += activities_per_day
        plan_days.append(DayPlan(
            date=(start + timedelta(days=i)).isoformat(),
            activities=acts,
            day_cost_usd=cost / days,
        ))
    return Plan(days=plan_days, total_cost_usd=cost, budget_usd=budget, currency="USD", fx_rate_used=1.0)


def test_validator_valid_plan():
    from core.schemas import TripRequest
    from core.validator import validate
    trip = TripRequest(destination_city="Rome", days=3, budget=500.0)
    plan = _make_plan(days=3, cost=100.0, budget=500.0)
    violations = validate(plan, trip)
    assert violations == []


def test_validator_budget_exceeded():
    from core.schemas import TripRequest
    from core.validator import validate
    trip = TripRequest(destination_city="Rome", days=3, budget=50.0)
    plan = _make_plan(days=3, cost=100.0, budget=50.0)
    violations = validate(plan, trip)
    assert any("Budget exceeded" in v for v in violations)


def test_validator_travel_time_exceeded():
    from core.schemas import Activity, DayPlan, Plan, TripRequest
    from core.validator import validate
    start = date.today() + timedelta(days=1)
    acts = [
        Activity(name="A", start_time="09:00", duration_min=90, est_cost_usd=10.0),
        Activity(name="B", start_time="11:00", duration_min=90, est_cost_usd=10.0,
                 travel_minutes_from_previous=120.0),  # 120 > 90 → violation
    ]
    day = DayPlan(date=start.isoformat(), activities=acts, day_cost_usd=20.0)
    plan = Plan(days=[day], total_cost_usd=20.0, budget_usd=500.0, currency="USD", fx_rate_used=1.0)
    trip = TripRequest(destination_city="Test", days=1, budget=500.0)
    violations = validate(plan, trip)
    assert any("travel time" in v.lower() or "Travel" in v for v in violations)


def test_validator_too_many_activities():
    from core.schemas import TripRequest
    from core.validator import validate
    trip = TripRequest(destination_city="Rome", days=1, budget=500.0)
    plan = _make_plan(days=1, cost=100.0, budget=500.0, activities_per_day=6)
    violations = validate(plan, trip)
    assert any("activities" in v for v in violations)


def test_validator_duplicate_names():
    from core.schemas import Activity, DayPlan, Plan, TripRequest
    from core.validator import validate
    start = date.today() + timedelta(days=1)
    acts = [
        Activity(name="Museum", start_time="09:00", duration_min=90, est_cost_usd=10.0),
        Activity(name="Museum", start_time="11:00", duration_min=90, est_cost_usd=10.0),
    ]
    day = DayPlan(date=start.isoformat(), activities=acts, day_cost_usd=20.0)
    plan = Plan(days=[day], total_cost_usd=20.0, budget_usd=500.0, currency="USD", fx_rate_used=1.0)
    trip = TripRequest(destination_city="Test", days=1, budget=500.0)
    violations = validate(plan, trip)
    assert any("Duplicate" in v or "duplicate" in v for v in violations)


def test_validator_day_count_mismatch():
    from core.schemas import TripRequest
    from core.validator import validate
    trip = TripRequest(destination_city="Rome", days=5, budget=500.0)
    plan = _make_plan(days=3, cost=100.0, budget=500.0)
    violations = validate(plan, trip)
    assert any("Day count" in v or "mismatch" in v.lower() for v in violations)


def test_validator_past_dates():
    from core.schemas import Activity, DayPlan, Plan, TripRequest
    from core.validator import validate
    acts = [Activity(name="Past Activity", start_time="09:00", duration_min=90, est_cost_usd=10.0)]
    day = DayPlan(date="2020-01-01", activities=acts, day_cost_usd=10.0)
    plan = Plan(days=[day], total_cost_usd=10.0, budget_usd=500.0, currency="USD", fx_rate_used=1.0)
    trip = TripRequest(destination_city="Test", days=1, budget=500.0)
    violations = validate(plan, trip)
    assert any("past" in v.lower() for v in violations)
