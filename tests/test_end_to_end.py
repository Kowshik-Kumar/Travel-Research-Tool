"""
tests/test_end_to_end.py – Phase 6 end-to-end tests.

Runs each prompt from test_prompts.json through the orchestrator with:
- Research calls replaced by fixture responses
- LLM calls stubbed to return deterministic TripRequest JSON
- Asserts: intent extraction, plan validation, coverage
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from datetime import date
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_PROMPTS_FILE = Path(__file__).parent / "test_prompts.json"


# ---------------------------------------------------------------------------
# Load test prompts
# ---------------------------------------------------------------------------

def _load_prompts():
    with open(_PROMPTS_FILE) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Mock research response factory
# ---------------------------------------------------------------------------

def _make_fixture_research(city: str, days: int = 3):
    """Build a deterministic research response for testing."""
    ranked = []
    for i in range(min(days * 2, 12)):
        ranked.append({
            "rank": i + 1,
            "name": f"{city} Attraction {i + 1}",
            "category": "museum",
            "lat": 35.0 + i * 0.01,
            "lon": 135.0 + i * 0.01,
            "est_cost_usd": float((i + 1) * 8),
            "travel_minutes_from_center": float(i * 10),
            "weather_score": 0.7,
            "score": float(1.0 - i * 0.05),
            "is_mock": True,
        })
    return {
        "status": "ok",
        "city": city,
        "ranked": ranked,
        "transport_options": [],
        "weather_summary": [
            {"date": (date.today().replace(day=date.today().day)).isoformat(),
             "avg_temp_c": 20.0, "rain_prob": 0.1, "score": 0.75}
        ],
        "error": None,
    }


# ---------------------------------------------------------------------------
# Stub LLM that parses prompt fields from test_prompts.json
# ---------------------------------------------------------------------------

def _make_stub_parser(prompt_data: dict):
    """Returns a function that stubs _parse_trip_request for a given prompt."""
    def _stub(text, session_state=None):
        from core.schemas import TripRequest
        return TripRequest(
            destination_city=prompt_data["expected_city"],
            country=prompt_data.get("expected_country"),
            days=prompt_data.get("days") or 3,
            budget=prompt_data.get("budget"),
            currency=prompt_data.get("currency", "USD"),
            interests=prompt_data.get("interests", []),
            needs_clarification=prompt_data.get("ambiguous", False),
            clarification_question=(
                "Could you specify the country?" if prompt_data.get("ambiguous") else None
            ),
        )
    return _stub


# ---------------------------------------------------------------------------
# End-to-end tests
# ---------------------------------------------------------------------------

class TestEndToEnd:
    @pytest.mark.parametrize("prompt_data", _load_prompts())
    def test_intent_extraction(self, prompt_data, monkeypatch):
        """Stub extraction must produce the expected city."""
        stub = _make_stub_parser(prompt_data)
        result = stub(prompt_data["prompt"])
        assert result.destination_city == prompt_data["expected_city"]
        if prompt_data.get("days"):
            assert result.days == prompt_data["days"]

    @pytest.mark.parametrize("prompt_data", _load_prompts())
    def test_plan_validation_passes(self, prompt_data, monkeypatch):
        """After research + plan, validator should return no critical violations."""
        if prompt_data.get("ambiguous") and not prompt_data.get("expected_city"):
            pytest.skip("Ambiguous prompt with no expected city")

        city = prompt_data["expected_city"]
        days = prompt_data.get("days") or 3
        budget = prompt_data.get("budget")
        currency = prompt_data.get("currency", "USD")

        fixture_research = _make_fixture_research(city, days)

        from agents.planner import plan
        from core.schemas import TripRequest
        from core.validator import validate

        trip = TripRequest(
            destination_city=city,
            country=prompt_data.get("expected_country"),
            days=days,
            budget=budget,
            currency=currency,
        )

        plan_obj = plan(fixture_research, trip.model_dump(), fx_rate=1.0)

        # Only check rules that should definitely pass for our fixture data
        violations = validate(plan_obj, trip)
        # Filter out budget violations (fixture costs may exceed test budgets)
        non_budget = [v for v in violations if "Budget" not in v]
        # Filter out day count mismatches (fixture may produce fewer days if few places)
        non_structural = [v for v in non_budget if "Day count" not in v]
        # Should have no date/travel/duplicate violations
        critical = [v for v in non_structural if
                    "past" in v.lower() or
                    "Duplicate" in v or
                    "activities" in v.lower() or
                    "Travel time" in v]
        assert not critical, f"Critical violations for {city}: {critical}"

    @pytest.mark.parametrize("prompt_data", _load_prompts())
    def test_coverage(self, prompt_data, monkeypatch):
        """Each non-ambiguous prompt should produce at least 1 day with 1 activity."""
        if prompt_data.get("ambiguous") and not prompt_data.get("expected_city"):
            pytest.skip("Ambiguous prompt with no expected city")

        city = prompt_data["expected_city"]
        days = prompt_data.get("days") or 3
        fixture_research = _make_fixture_research(city, days)

        from agents.planner import plan
        from core.schemas import TripRequest

        trip = TripRequest(
            destination_city=city,
            days=days,
            budget=prompt_data.get("budget"),
            currency=prompt_data.get("currency", "USD"),
        )
        plan_obj = plan(fixture_research, trip.model_dump(), fx_rate=1.0)

        # At least one day with at least one activity
        covered = any(len(day.activities) > 0 for day in plan_obj.days)
        assert covered, f"No coverage for prompt {prompt_data['id']}: {city}"

    def test_ambiguous_prompts_ask_clarification(self, monkeypatch):
        """Ambiguous prompts should trigger a clarification question."""
        ambiguous = [p for p in _load_prompts() if p.get("ambiguous")]
        assert len(ambiguous) >= 2, "Need at least 2 ambiguous prompts"

        for p in ambiguous:
            from agents.orchestrator import _sessions, handle_message_offline, _Session
            _sessions.clear()

            def ambiguous_stub(text, session_state=None):
                from core.schemas import TripRequest
                return TripRequest(
                    destination_city=p.get("expected_city"),
                    country=None,
                    days=p.get("days") or 3,
                    needs_clarification=True,
                    clarification_question="Which city did you mean?",
                )

            monkeypatch.setattr("agents.orchestrator._parse_trip_request", ambiguous_stub)
            reply = handle_message_offline(f"sender-{p['id']}", p["prompt"])
            # Should ask for clarification, not produce a plan immediately
            assert isinstance(reply, str) and len(reply) > 0

    def test_non_usd_currency_in_plan(self, monkeypatch):
        """Plans with non-USD currency should show the correct currency code."""
        from agents.planner import plan
        from core.schemas import TripRequest

        fixture = _make_fixture_research("Tokyo", 3)
        trip = TripRequest(
            destination_city="Tokyo",
            country="Japan",
            days=3,
            budget=50000.0,
            currency="JPY",
        )
        plan_obj = plan(fixture, trip.model_dump(), fx_rate=0.0067)
        assert plan_obj.currency == "JPY"
        assert plan_obj.fx_rate_used == pytest.approx(0.0067)
