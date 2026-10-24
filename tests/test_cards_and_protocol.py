"""Tests for interactive cards flow, FX conversion, live-data parsers and the ACP handler."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents import cards, orchestrator
from agents.orchestrator import Reply, process_message


def _research(city="Lisbon"):
    ranked = []
    for i, (n, c, cost) in enumerate([("Castelo", "historic", 10), ("MAAT", "museum", 12),
                                      ("Miradouro", "viewpoint", 0), ("Jardim", "park", 0),
                                      ("Oceanario", "attraction", 25), ("Pantheon", "historic", 5)], 1):
        ranked.append({"rank": i, "name": n, "category": c, "lat": 38.7 + i / 100, "lon": -9.1 + i / 100,
                       "est_cost_usd": cost, "travel_minutes_from_center": 5.0 * i,
                       "weather_score": 0.8, "score": 0.9, "is_mock": False})
    return {"status": "ok", "city": city, "ranked": ranked, "transport_options": [],
            "weather_summary": [{"date": "2030-01-01", "avg_temp_c": 18.0, "rain_prob": 0.1, "score": 0.8}]}


@pytest.fixture(autouse=True)
def _clean():
    orchestrator._sessions.clear()


def test_free_text_returns_review_card():
    with patch("agents.orchestrator.research", return_value=_research()):
        r = process_message("u1", "Plan 2 days in Lisbon with a budget of 300 EUR")
    assert r.card_kind == "review"
    assert r.card_payload["approve_cta"]["selection"] == {"action": "approve_plan"}
    assert any("Day 1" in row["label"] for row in r.card_payload["summary_rows"])


def test_budget_currency_is_converted_not_treated_as_usd():
    with patch("agents.orchestrator.research", return_value=_research()), \
         patch("agents.orchestrator.get_fx_rate", return_value=2.0):
        process_message("u2", json.dumps({"action": "submit_trip", "destination_city": "Lisbon",
                                          "days": "2", "budget": "100", "currency": "EUR"}))
    plan = orchestrator._sessions["u2"].last_plan
    assert plan.fx_rate_used == 2.0
    assert plan.budget_usd == 200.0


def test_form_submit_then_approve_gives_action_links():
    with patch("agents.orchestrator.research", return_value=_research()):
        r1 = process_message("u3", json.dumps({"action": "submit_trip", "destination_city": "Lisbon",
                                               "days": 2, "budget": "", "currency": "USD", "interests": ""}))
        assert r1.card_kind == "review"
        r2 = process_message("u3", json.dumps({"action": "approve_plan"}))
    assert "google.com/maps/search" in r2.text
    assert "calendar.google.com/calendar/render" in r2.text
    assert "google.com/maps/dir" in r2.text


def test_prose_approval_from_asi_planner():
    with patch("agents.orchestrator.research", return_value=_research()):
        process_message("u4", "3 days in Lisbon")
        r = process_message("u4", "I approve the plan, please confirm")
    assert "calendar.google.com" in r.text


def test_reject_reopens_form():
    with patch("agents.orchestrator.research", return_value=_research()):
        process_message("u5", "3 days in Lisbon")
        r = process_message("u5", json.dumps({"action": "reject_plan"}))
    assert r.card_kind == "form"


def test_missing_destination_returns_form():
    r = process_message("u6", "hello")
    assert r.card_kind == "form"
    names = [f["name"] for f in r.card_payload["fields"]]
    assert {"destination_city", "days", "budget", "currency"} <= set(names)


def test_card_message_metadata_is_valid():
    msg = orchestrator.build_chat_message(Reply("hi", "form", cards.trip_form_card()))
    meta = next(c for c in msg.content if c.type == "metadata").metadata
    assert meta["card_protocol_version"] == "1" and meta["requires_card_interaction"] == "true"
    assert meta["card_kind"] == "form"
    assert all(isinstance(v, str) for v in meta.values())
    assert len(meta["card_payload"]) < 64 * 1024
    json.loads(meta["card_payload"])


def test_acp_handler_acks_with_msg_id_and_replies():
    """Drive the real uAgents chat handler with a fake context."""
    from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent
    sent = []

    class Ctx:
        session = uuid4()
        logger = MagicMock()
        async def send(self, to, m):
            sent.append(m)

    incoming = ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                           content=[TextContent(type="text", text="2 days in Lisbon")])
    with patch("agents.orchestrator.research", return_value=_research()):
        asyncio.run(orchestrator.handle_chat(Ctx(), "agent1qtest", incoming))
    assert isinstance(sent[0], ChatAcknowledgement)
    assert sent[0].acknowledged_msg_id == incoming.msg_id
    assert isinstance(sent[1], ChatMessage)
    assert any(c.type == "metadata" for c in sent[1].content)


def test_open_meteo_parser(monkeypatch):
    import tools.weather as w
    monkeypatch.setenv("TRIP_OFFLINE", "0")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"daily": {"time": ["2030-01-01", "2030-01-02"],
                                        "temperature_2m_max": [20, 10], "temperature_2m_min": [10, 0],
                                        "precipitation_probability_max": [10, 90]}}
    with patch.object(w, "http_get", return_value=resp):
        days = w._open_meteo(1, 1)
    assert [d.avg_temp_c for d in days] == [15.0, 5.0]
    assert days[0].score > days[1].score


def test_open_meteo_failure_returns_empty():
    import tools.weather as w
    with patch.object(w, "http_get", side_effect=RuntimeError("down")):
        assert w._open_meteo(1, 1) == []


def test_osm_parser_and_ranking(monkeypatch):
    import tools.places as pl
    monkeypatch.setenv("TRIP_OFFLINE", "0")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"elements": [
        {"type": "node", "lat": 1.0, "lon": 1.0, "tags": {"name": "Plain Museum", "tourism": "museum"}},
        {"type": "way", "center": {"lat": 2.0, "lon": 2.0},
         "tags": {"name": "Famous Castle", "historic": "castle", "wikidata": "Q1", "wikipedia": "en:X", "fee": "no"}},
        {"type": "node", "lat": 3, "lon": 3, "tags": {"tourism": "museum"}},  # no name -> dropped
    ]}
    with patch.object(pl, "http_post", return_value=resp):
        out = pl._fetch_osm(0, 0)
    assert [p["name"] for p in out] == ["Famous Castle", "Plain Museum"]
    assert out[0]["est_cost_usd"] == 0.0 and out[0]["category"] == "historic"
    assert out[0]["source"] == "osm"


def test_links_are_url_safe():
    link = cards.gcal_link("A & B", "2030-01-02", "09:00", 90, location="X, Y")
    assert "A+%26+B" in link and "20300102T090000%2F20300102T103000" in link
    assert cards.maps_route_link([{"lat": 1, "lon": 1}]) is None


@pytest.mark.parametrize("text,amount,cur", [
    ("Plan 2 days in Kyoto with a budget of 200 EUR", 200.0, "EUR"),
    ("3 days in Rome, budget $1,200", 1200.0, "USD"),
    ("5-day trip to Paris €800", 800.0, "EUR"),
    ("a week in Goa, GBP 500", 500.0, "GBP"),
    ("2 days in Lisbon", None, "USD"),
])
def test_budget_parser(text, amount, cur):
    assert orchestrator._parse_budget(text) == (amount, cur)


def test_geoapify_places_parser(monkeypatch):
    import tools.places as pl
    monkeypatch.setenv("TRIP_OFFLINE", "0")
    monkeypatch.setenv("GEOAPIFY_API_KEY", "k")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"features": [
        {"properties": {"name": "Louvre", "lat": 48.86, "lon": 2.33, "categories": ["entertainment", "entertainment.museum"],
                        "datasource": {"raw": {"wikidata": "Q1", "website": "x"}}}},
        {"properties": {"name": "Jardin", "lat": 48.8, "lon": 2.3, "categories": ["leisure.park"],
                        "datasource": {"raw": {"fee": "no"}}}},
        {"properties": {"lat": 1, "lon": 1, "categories": []}},  # no name -> dropped
    ]}
    with patch.object(pl, "http_get", return_value=resp) as g:
        out = pl._fetch_geoapify(48.85, 2.35)
    assert [p["name"] for p in out] == ["Louvre", "Jardin"]
    assert out[0]["category"] == "museum" and out[0]["est_cost_usd"] == 12.0
    assert out[1]["category"] == "park" and out[1]["est_cost_usd"] == 0.0
    assert g.call_args.kwargs["params"]["filter"] == "circle:2.35,48.85,5000"


def test_geoapify_geocode(monkeypatch):
    import tools.geocode as gc
    monkeypatch.setenv("GEOAPIFY_API_KEY", "k")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"results": [{"city": "Lisbon", "country": "Portugal", "lat": 38.7, "lon": -9.1,
                                           "timezone": {"name": "Europe/Lisbon"}}]}
    with patch.object(gc, "http_get", return_value=resp):
        r = gc._geoapify_geocode("Lisbon", None)
    assert r["timezone"] == "Europe/Lisbon" and r["lat"] == 38.7


def test_geoapify_failure_falls_through(monkeypatch):
    import tools.places as pl
    monkeypatch.setenv("TRIP_OFFLINE", "0")
    monkeypatch.setenv("GEOAPIFY_API_KEY", "k")
    with patch.object(pl, "http_get", side_effect=RuntimeError("down")):
        assert pl._fetch_geoapify(0, 0) == []
