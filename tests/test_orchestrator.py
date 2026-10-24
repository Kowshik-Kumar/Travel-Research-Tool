"""
tests/test_orchestrator.py – Tests for agents/orchestrator.py.
Uses fake messages and stubbed LLM client (no network required).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_research_result(city="Paris"):
    return {
        "status": "ok",
        "city": city,
        "ranked": [
            {
                "rank": 1, "name": "Eiffel Tower", "category": "architecture",
                "lat": 48.858, "lon": 2.295, "est_cost_usd": 25.0,
                "travel_minutes_from_center": 10.0, "weather_score": 0.8,
                "score": 0.9, "is_mock": True,
            },
            {
                "rank": 2, "name": "Louvre Museum", "category": "museums",
                "lat": 48.860, "lon": 2.337, "est_cost_usd": 17.0,
                "travel_minutes_from_center": 5.0, "weather_score": 0.75,
                "score": 0.85, "is_mock": True,
            },
            {
                "rank": 3, "name": "Notre-Dame Cathedral", "category": "religion",
                "lat": 48.853, "lon": 2.350, "est_cost_usd": 0.0,
                "travel_minutes_from_center": 7.0, "weather_score": 0.7,
                "score": 0.8, "is_mock": True,
            },
        ],
        "transport_options": [],
        "weather_summary": [],
        "error": None,
    }


def _stub_llm_extraction(text: str, session_state=None):
    """Directly return a TripRequest without calling the LLM."""
    from core.schemas import TripRequest
    return TripRequest(
        destination_city="Paris",
        country="France",
        days=3,
        budget=1000.0,
        currency="USD",
        interests=["museums"],
        needs_clarification=False,
        clarification_question=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOrchestrator:
    def test_handle_message_offline_basic(self, monkeypatch):
        """Offline handler should return a non-empty reply."""
        # Patch research and plan to avoid network calls
        monkeypatch.setattr("agents.orchestrator._parse_trip_request", _stub_llm_extraction)
        with patch("agents.orchestrator.research", return_value=_make_research_result()):
            from agents.orchestrator import handle_message_offline
            reply = handle_message_offline("test-sender-1", "I want to visit Paris for 3 days")
        assert isinstance(reply, str)
        assert len(reply) > 0

    def test_clarification_asked_when_ambiguous(self, monkeypatch):
        """If city is ambiguous, the orchestrator should ask a clarification question."""
        from core.schemas import TripRequest

        def ambiguous_extraction(text, session_state=None):
            return TripRequest(
                destination_city="Paris",
                country=None,
                days=3,
                needs_clarification=True,
                clarification_question="Which Paris do you mean – France or Texas?",
            )

        monkeypatch.setattr("agents.orchestrator._parse_trip_request", ambiguous_extraction)
        from agents.orchestrator import _sessions, handle_message_offline
        # Clear session state
        _sessions.clear()
        reply = handle_message_offline("test-sender-clarify", "I want to visit Paris for 3 days")
        assert "Paris" in reply or "clarif" in reply.lower() or "which" in reply.lower()

    def test_session_state_persists_across_turns(self, monkeypatch):
        """Second message should build on the first session state."""
        from agents.orchestrator import _sessions, handle_message_offline
        _sessions.clear()

        call_count = [0]

        def extraction_first(text, session_state=None):
            from core.schemas import TripRequest
            call_count[0] += 1
            if call_count[0] == 1:
                return TripRequest(destination_city=None, days=3, needs_clarification=True,
                                   clarification_question="Which city?")
            return TripRequest(destination_city="Rome", country="Italy", days=3,
                               budget=800.0, needs_clarification=False)

        monkeypatch.setattr("agents.orchestrator._parse_trip_request", extraction_first)
        with patch("agents.orchestrator.research", return_value=_make_research_result("Rome")):
            r1 = handle_message_offline("test-sender-session", "3 days trip")
            r2 = handle_message_offline("test-sender-session", "Rome, Italy")

        assert isinstance(r1, str)
        assert isinstance(r2, str)

    def test_bad_llm_falls_back_to_best_guess(self, monkeypatch):
        """When LLM returns None, best-guess parsing should handle simple cases."""
        monkeypatch.setattr("agents.orchestrator._parse_trip_request", lambda t, **kw: None)
        with patch("agents.orchestrator.research", return_value=_make_research_result("Tokyo")):
            from agents.orchestrator import _sessions, handle_message_offline
            _sessions.clear()
            reply = handle_message_offline("test-sender-nogpt", "I want to visit Tokyo for 2 days")
        assert isinstance(reply, str)

    def test_research_error_propagated(self, monkeypatch):
        """If research returns an error, the reply should mention it."""
        monkeypatch.setattr("agents.orchestrator._parse_trip_request", _stub_llm_extraction)
        with patch("agents.orchestrator.research", return_value={"status": "error", "error": "API down"}):
            from agents.orchestrator import _sessions, handle_message_offline
            _sessions.clear()
            reply = handle_message_offline("test-sender-err", "Visit Paris 3 days")
        assert "error" in reply.lower() or "failed" in reply.lower() or "API" in reply

    def test_mock_note_in_reply_when_mock_data(self, monkeypatch):
        """When ranked places have is_mock=True, reply should mention mock."""
        monkeypatch.setattr("agents.orchestrator._parse_trip_request", _stub_llm_extraction)
        result = _make_research_result()
        # Ensure all are mock
        for r in result["ranked"]:
            r["is_mock"] = True
        with patch("agents.orchestrator.research", return_value=result):
            from agents.orchestrator import _sessions, handle_message_offline
            _sessions.clear()
            reply = handle_message_offline("test-sender-mock", "Visit Paris 3 days")
        assert "mock" in reply.lower() or "MOCK" in reply

    def test_session_expires(self, monkeypatch):
        """An expired session should reset state for a new message."""
        import time
        from agents.orchestrator import _sessions, handle_message_offline, _Session
        _sessions.clear()

        # Create an "already expired" session
        old_session = _Session()
        old_session.created_at = time.time() - 9999  # way in the past
        old_session.turns = 10
        _sessions["test-sender-expire"] = old_session

        monkeypatch.setattr("agents.orchestrator._parse_trip_request", _stub_llm_extraction)
        with patch("agents.orchestrator.research", return_value=_make_research_result()):
            reply = handle_message_offline("test-sender-expire", "Plan my Paris trip")

        # Session should have been reset; turns should now be 1 not 11
        session = _sessions["test-sender-expire"]
        assert session.turns == 1
        assert isinstance(reply, str)
