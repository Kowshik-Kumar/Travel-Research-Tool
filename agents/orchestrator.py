"""
agents/orchestrator.py – ASI:One chat-protocol orchestrator.

Implements the uAgents chat protocol (ChatMessage / ChatAcknowledgement)
and manages multi-turn session state. Each session:
  1. Receives the user's natural-language trip request.
  2. Calls the ASI:One LLM to extract TripRequest fields as strict JSON.
  3. Asks at most 2 clarification questions.
  4. Calls research() + plan() and replies with a readable summary.

Sessions expire after SESSION_TTL_SECONDS (default 30 min).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import warnings
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

# uagents imports – gracefully degrade if not installed
try:
    from uagents import Context, Protocol
    from uagents_core.contrib.protocols.chat import (
        ChatAcknowledgement,
        ChatMessage,
        EndSessionContent,
        MetadataContent,
        StartSessionContent,
        TextContent,
        chat_protocol_spec,
    )
    _UAGENTS_AVAILABLE = True
except ImportError:
    _UAGENTS_AVAILABLE = False
    warnings.warn(
        "uagents / uagents_core not installed – orchestrator running in offline mode.",
        stacklevel=1,
    )
    # Stubs so the rest of the file can be imported and tested without uagents
    class Protocol:  # type: ignore[no-redef]
        def __init__(self, **kw): pass
        def on_message(self, *a, **kw):
            return lambda f: f

    class Context:  # type: ignore[no-redef]
        logger = __import__("logging").getLogger("orchestrator")
        async def send(self, *a, **kw): pass

    class ChatMessage:  # type: ignore[no-redef]
        pass
    class ChatAcknowledgement:  # type: ignore[no-redef]
        pass
    class TextContent:  # type: ignore[no-redef]
        def __init__(self, **kw): pass
    class EndSessionContent:  # type: ignore[no-redef]
        def __init__(self, **kw): pass
    class MetadataContent:  # type: ignore[no-redef]
        def __init__(self, **kw): pass
    class StartSessionContent:  # type: ignore[no-redef]
        def __init__(self, **kw): pass
    chat_protocol_spec = None

import metrics
from agents import cards
from agents.planner import plan as build_plan
from agents.research import research
from core.schemas import TripRequest
from tools.currency import get_fx_rate

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ASI1_API_KEY: str = os.getenv("ASI1_API_KEY", "")
ASI1_BASE_URL: str = os.getenv("ASI1_BASE_URL", "https://api.asi1.ai/v1")
ASI1_MODEL: str = os.getenv("ASI1_MODEL", "asi1-mini")
SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "1800"))  # 30 min

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class _Session:
    def __init__(self):
        self.trip: Optional[TripRequest] = None
        self.turns: int = 0
        self.clarification_count: int = 0
        self.created_at: float = time.time()
        self.last_plan: Any = None          # most recent Plan awaiting approval
        self.last_trip: Optional[TripRequest] = None
        self.place_index: dict = {}         # lower(name) -> {lat, lon, category}
        self.last_weather: list = []
        self.last_mock: bool = False

    def is_expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TTL_SECONDS


_sessions: dict[str, _Session] = {}


def _get_or_create_session(sender: str) -> _Session:
    if sender in _sessions:
        sess = _sessions[sender]
        if sess.is_expired():
            _sessions[sender] = _Session()
        return _sessions[sender]
    _sessions[sender] = _Session()
    return _sessions[sender]


# ---------------------------------------------------------------------------
# LLM intent extraction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a travel assistant that extracts trip details from user messages. "
    "Return ONLY a valid JSON object matching this schema (no explanation, no markdown):\n"
    '{"destination_city": str|null, "country": str|null, "days": int|null, '
    '"budget": float|null, "currency": "USD", "interests": [str], '
    '"needs_clarification": bool, "clarification_question": str|null}\n'
    "If the destination is ambiguous (e.g. 'Paris' without country), set "
    "needs_clarification=true and provide a clarification_question."
)


def _call_llm(messages: list[dict]) -> Optional[str]:
    """Call the ASI:One chat completions API. Returns the assistant content or None."""
    if not ASI1_API_KEY:
        return None
    try:
        import httpx
        payload = {
            "model": ASI1_MODEL,
            "messages": messages,
            "temperature": 0.0,
        }
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{ASI1_BASE_URL}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {ASI1_API_KEY}"},
            )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        warnings.warn(f"ASI:One API returned {resp.status_code}", stacklevel=2)
    except Exception as exc:
        warnings.warn(f"ASI:One call failed: {exc}", stacklevel=2)
    return None


def _parse_trip_request(content: str, session_state: Optional[dict] = None) -> Optional[TripRequest]:
    """
    Ask the LLM to extract a TripRequest from user text. Retries once on
    JSON parse failure.
    """
    context_str = ""
    if session_state:
        context_str = f"\nCurrent session state: {json.dumps(session_state)}"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT + context_str},
        {"role": "user", "content": content},
    ]

    for _attempt in range(2):
        raw = _call_llm(messages)
        if raw is None:
            return None
        try:
            # Strip possible markdown fences
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(clean)
            return TripRequest(**data)
        except (json.JSONDecodeError, Exception):
            # Retry with explicit error hint
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": "Your response was not valid JSON. Please return ONLY the JSON object.",
            })

    return None


# ---------------------------------------------------------------------------
# Plan summary
# ---------------------------------------------------------------------------

def _format_plan_summary(plan_obj: Any, trip: TripRequest, has_mock: bool) -> str:
    lines = [f"🗺️  **{trip.days}-day trip to {trip.destination_city}**\n"]
    for day in plan_obj.days:
        lines.append(f"**{day.date}**\n")
        for act in day.activities:
            cost_local = round(act.est_cost_usd / (plan_obj.fx_rate_used or 1.0), 2)
            travel = (
                f" (+{act.travel_minutes_from_previous:.0f} min travel)"
                if act.travel_minutes_from_previous and act.travel_minutes_from_previous >= 1 else ""
            )
            price = f"{cost_local:g} {trip.currency}" if cost_local else "free"
            lines.append(f"- {act.start_time} {act.name}{travel} – {price}")
        lines.append(f"- _Day subtotal: {round(day.day_cost_usd / (plan_obj.fx_rate_used or 1.0), 2):g} {trip.currency}_\n")

    total_local = round(plan_obj.total_cost_usd / (plan_obj.fx_rate_used or 1.0), 2)
    usd_note = f" ({plan_obj.total_cost_usd:.2f} USD)" if trip.currency != "USD" else ""
    lines.append(f"**Total: {total_local} {trip.currency}{usd_note}**")
    if trip.budget:
        lines.append(f"Budget: {trip.budget} {trip.currency}")
    if has_mock:
        lines.append("\n⚠️  Some data is mock / not from live APIs.")
    if trip.currency != "USD":
        lines.append(f"\n_FX rate used: 1 {trip.currency} = {plan_obj.fx_rate_used:.4f} USD_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Replies (text + optional interactive card)
# ---------------------------------------------------------------------------

@dataclass
class Reply:
    text: str
    card_kind: Optional[str] = None
    card_payload: Optional[dict] = None


_APPROVE_RE = re.compile(r"\b(approve_plan|approve|confirm|looks good|go ahead|book it|yes)\b", re.I)
_REJECT_RE = re.compile(r"\b(reject_plan|change details|change|edit|modify|redo|no)\b", re.I)


def _try_json(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _form_reply(msg: str) -> Reply:
    return Reply(msg, "form", cards.trip_form_card())


def _trip_from_form(sel: dict, base: Optional[TripRequest] = None) -> TripRequest:
    interest = str(sel.get("interests") or "").strip()
    days = _to_float(sel.get("days"))
    return TripRequest(
        destination_city=(sel.get("destination_city") or "").strip() or (base.destination_city if base else None),
        country=sel.get("country") or (base.country if base else None),
        days=max(1, min(7, int(days))) if days else (base.days if base and base.days else 3),
        budget=_to_float(sel.get("budget")),
        currency=str(sel.get("currency") or (base.currency if base else "USD")).upper()[:3],
        interests=[interest] if interest else [],
    )


def _plan_trip(session: "_Session", trip: TripRequest) -> Reply:
    """Run research -> plan, store state, return a review card."""
    research_result = research(trip.model_dump(), top_n=min(15, 4 * (trip.days or 3)))
    if research_result.get("status") == "error":
        return Reply(f"Research failed: {research_result.get('error')}")
    if not research_result.get("ranked"):
        return Reply(f"I couldn't find attractions for {trip.destination_city}. "
                     "Try a larger nearby city or check the spelling.")

    # Live mode + nothing but mock places => be honest instead of planning around fake stops
    from core.config import offline
    if not offline() and all(r.get("is_mock") for r in research_result["ranked"]):
        session.trip = None
        return _form_reply(
            f"I couldn't find live attractions for '{trip.destination_city}'. It may be a region or a "
            "misspelling - please enter a specific city (e.g. Honolulu, Lisbon, Kyoto):")

    # Budget is in the *user's* currency; planner needs user-currency -> USD.
    fx = get_fx_rate(trip.currency, "USD")
    plan_obj = build_plan(research_result, trip.model_dump(), fx_rate=fx)
    has_mock = any(r.get("is_mock") for r in research_result.get("ranked", []))

    session.last_plan, session.last_trip = plan_obj, trip
    session.last_weather = research_result.get("weather_summary", [])
    session.last_mock = has_mock
    session.place_index = {
        r["name"].lower(): {"lat": r.get("lat"), "lon": r.get("lon"), "category": r.get("category")}
        for r in research_result.get("ranked", [])
    }

    text = _format_plan_summary(plan_obj, trip, has_mock)
    rows = []
    for i, day in enumerate(plan_obj.days, 1):
        names = ", ".join(a.name for a in day.activities) or "Free day"
        rows.append({"label": f"Day {i} ({day.date})", "value": names[:220]})
    total_local = round(plan_obj.total_cost_usd / (plan_obj.fx_rate_used or 1.0), 2)
    rows.append({"label": "Estimated cost", "value": f"{total_local} {trip.currency}"})
    if trip.budget:
        rows.append({"label": "Your budget", "value": f"{trip.budget:g} {trip.currency}"})
    if session.last_weather:
        w = session.last_weather[0]
        rows.append({"label": "Forecast (day 1)",
                     "value": f"{w['avg_temp_c']} C, {round(w['rain_prob'] * 100)}% rain"})
    return Reply(text + "\n\nReview the plan below - confirm to get map & calendar links.",
                 "review", cards.review_card(f"{trip.days}-day plan: {trip.destination_city}", rows))


def _final_itinerary(session: "_Session") -> Reply:
    plan_obj, trip = session.last_plan, session.last_trip
    if plan_obj is None or trip is None:
        return _form_reply("I don't have a plan to confirm yet - let's create one.")
    city = trip.destination_city or ""
    lines = [f"## Confirmed: {len(plan_obj.days)}-day trip to {city}", ""]
    for i, day in enumerate(plan_obj.days, 1):
        lines.append(f"### Day {i} - {day.date}")
        stops = []
        for act in day.activities:
            meta = session.place_index.get(act.name.lower(), {})
            stops.append({"lat": meta.get("lat"), "lon": meta.get("lon")})
            maps = cards.maps_place_link(act.name, city, meta.get("lat"), meta.get("lon"))
            cal = cards.gcal_link(f"{act.name} ({city})", day.date, act.start_time, act.duration_min,
                                  details="Planned by Trip Planner agent (ASI:One)", location=f"{act.name}, {city}")
            lines.append(f"- **{act.start_time}** [{act.name}]({maps}) - [add to calendar]({cal})")
        route = cards.maps_route_link(stops)
        if route:
            lines.append(f"- [Open full day route in Google Maps]({route})")
        lines.append("")
    if session.last_mock:
        lines.append("_Note: some data is mock / not from live APIs._")
    lines.append("_Costs are estimates. Opening hours are not verified - check before you go._")
    return Reply("\n".join(lines))


# ---------------------------------------------------------------------------
# Main entry point (pure Python - used by uAgents handler, tests and offline demo)
# ---------------------------------------------------------------------------

def process_message(sender: str, text: str) -> Reply:
    session = _get_or_create_session(sender)
    session.turns += 1
    metrics.inc_request()

    # 1) Card actions (direct @mention => JSON selection)
    sel = _try_json(text)
    if sel and "action" in sel:
        action = sel["action"]
        if action == "submit_trip":
            trip = _trip_from_form(sel, session.trip)
            if not trip.destination_city:
                return _form_reply("I still need a destination city.")
            session.trip = trip
            return _plan_trip(session, trip)
        if action == "pick_destination":
            base = session.trip or TripRequest()
            trip = _trip_from_form({**base.model_dump(), **sel}, base)
            session.trip = trip
            return _plan_trip(session, trip)
        if action == "approve_plan":
            return _final_itinerary(session)
        if action in ("reject_plan", "new_trip"):
            session.last_plan = None
            return _form_reply("No problem - update the details and I'll re-plan.")

    # 2) Prose fallback for card clicks relayed by the ASI:One planner
    if session.last_plan is not None and len(text) < 160:
        if _APPROVE_RE.search(text) and not _REJECT_RE.search(text.replace("approve", "")):
            return _final_itinerary(session)
        if _REJECT_RE.search(text) and "plan" not in text.lower().replace("change", ""):
            session.last_plan = None
            return _form_reply("No problem - update the details and I'll re-plan.")

    # 3) Free-text intent -> TripRequest
    extracted = _parse_trip_request(text) or _best_guess_parse(text)
    prev = session.trip
    if (prev is None or (extracted.destination_city and prev.destination_city and
                         extracted.destination_city.lower() != prev.destination_city.lower())):
        # first message, or the user switched destination: start a clean trip (no stale budget/interests)
        session.trip, session.clarification_count, session.last_plan = extracted, 0, None
    else:
        session.trip = _merge(prev, extracted)
    trip = session.trip

    if trip.needs_clarification and session.clarification_count < 2:
        session.clarification_count += 1
        return Reply(trip.clarification_question or "Could you clarify your destination?")

    if trip.destination_city is None:
        return _form_reply("I couldn't determine your destination. Tell me where you'd like to go, or fill in the form:")

    if session.clarification_count >= 2 and trip.needs_clarification:
        trip.needs_clarification = False

    reply = _plan_trip(session, trip)
    if reply.card_kind == "review":
        metrics.inc_success()
    return reply


def handle_message_offline(sender: str, text: str) -> str:
    """Text-only wrapper kept for tests and the offline demo."""
    return process_message(sender, text).text


_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
_CODES = "usd|eur|gbp|inr|jpy|aud|cad|thb|mxn|brl|chf|cny|krw"
_WORDS = {"rupees": "INR", "rupee": "INR", "rs": "INR", "dollars": "USD", "dollar": "USD", "bucks": "USD",
          "euros": "EUR", "euro": "EUR", "pounds": "GBP", "pound": "GBP", "quid": "GBP", "yen": "JPY",
          "baht": "THB", "pesos": "MXN", "reais": "BRL"}
_CUR = rf"(?:{_CODES}|{'|'.join(_WORDS)})"
_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(k|lakhs?|lacs?|m|million)?"


def _cur_code(tok: str) -> str:
    t = tok.lower().rstrip(".")
    return _WORDS.get(t, t.upper())


def _amount(num: str, mult: Optional[str]) -> float:
    v = float(num.replace(",", ""))
    m = (mult or "").lower()
    if m == "k":
        v *= 1_000
    elif m.startswith(("lakh", "lac")):
        v *= 100_000
    elif m in ("m", "million"):
        v *= 1_000_000
    return v


def _parse_budget(text: str) -> tuple[Optional[float], str]:
    """Return (amount, ISO currency) found in free text. Never confuses '3 days' with money."""
    t = re.sub(r"\b\d+\s*(?:-|\s)?(?:days?|nights?|d)\b", " ", text, flags=re.I)
    m = re.search(rf"{_NUM}\s*({_CUR})\b\.?", t, re.I)                      # 300 EUR / 20,000 rupees / 20k inr
    if m:
        return _amount(m.group(1), m.group(2)), _cur_code(m.group(3))
    m = re.search(rf"\b({_CUR})\b\.?\s*{_NUM}", t, re.I)                  # EUR 300 / Rupees 20,000 / Rs. 20000
    if m:
        return _amount(m.group(2), m.group(3)), _cur_code(m.group(1))
    m = re.search(rf"([$€£₹¥])\s*{_NUM}", t)                                # €300 / ₹20,000
    if m:
        return _amount(m.group(2), m.group(3)), _SYMBOLS[m.group(1)]
    m = re.search(rf"budget\D{{0,15}}?{_NUM}", t, re.I)                      # budget of 300
    if m:
        return _amount(m.group(1), m.group(2)), "USD"
    return None, "USD"


def _best_guess_parse(text: str) -> TripRequest:
    """Very simple keyword extraction when LLM is not available."""
    days_match = re.search(r"(\d+)\s*(?:-\s*)?(?:days?|nights?)", text, re.I)
    budget, currency = _parse_budget(text)
    return TripRequest(
        destination_city=_guess_city(text),
        days=max(1, min(7, int(days_match.group(1)))) if days_match else 3,
        budget=budget,
        currency=currency,
        interests=[],
    )


_CITY_STOP = {
    "a", "an", "the", "my", "me", "trip", "plan", "planning", "holiday", "vacation", "travel",
    "visit", "go", "going", "want", "need", "help", "with", "for", "and", "on", "at", "from",
    "budget", "under", "around", "about", "of", "i", "to", "in", "days", "day", "night",
    "nights", "week", "weekend", "please", "hi", "hello", "hey", "some", "another",
}


def _guess_city(text: str) -> Optional[str]:
    """Heuristic: words after 'in/to/visit/explore'. Never returns generic words like 'Plan'."""
    import re
    m = re.search(r"\b(?:in|to|visit|explore|visiting|exploring|for)\s+([A-Za-z][A-Za-z.\-']*(?:\s+[A-Za-z][A-Za-z.\-']*){0,2})", text, re.I)
    candidates = []
    if m:
        candidates.append(m.group(1))
    # bare reply such as "Lisbon" or "Rome, Italy" (<= 3 words, no digits)
    if len(text.split()) <= 3 and not re.search(r"\d", text):
        candidates.append(re.split(r"[,;]", text)[0])
    for cand in candidates:
        words = []
        for w in cand.split():
            if w.lower() in _CITY_STOP or w.lower() in {"days", "day"}:
                break
            words.append(w)
        if words:
            return " ".join(w.capitalize() for w in words)
    return None


def _merge(base: TripRequest, update: TripRequest) -> TripRequest:
    """Merge non-None fields from update into base (currency only follows an explicit budget)."""
    base_dict = base.model_dump()
    upd_dict = update.model_dump()
    for k, v in upd_dict.items():
        if k == "currency":
            continue
        if v is not None and v != [] and v != False:
            base_dict[k] = v
    if update.budget is not None:
        base_dict["currency"] = update.currency
    return TripRequest(**base_dict)


# ---------------------------------------------------------------------------
# uAgents Protocol (only active when uagents is installed)
# ---------------------------------------------------------------------------

def build_chat_message(reply: Reply) -> "ChatMessage":
    content: list = [TextContent(type="text", text=reply.text)]
    if reply.card_kind and reply.card_payload is not None:
        content.append(MetadataContent(type="metadata", metadata={
            "card_protocol_version": "1",
            "requires_card_interaction": "true",
            "card_kind": reply.card_kind,
            "card_payload": json.dumps(reply.card_payload),
            "preferred_drawer_width_px": "540",
        }))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


if _UAGENTS_AVAILABLE:
    chat_proto = Protocol(spec=chat_protocol_spec)

    @chat_proto.on_message(model=ChatMessage)
    async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
        # Acknowledge first (ACP requires the id of the message being acknowledged)
        await ctx.send(sender, ChatAcknowledgement(
            timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))

        text, started = "", False
        for item in msg.content:
            if isinstance(item, StartSessionContent):
                started = True
            elif isinstance(item, EndSessionContent):
                _sessions.pop(f"{sender}:{ctx.session}", None)
                return
            elif isinstance(item, TextContent):
                text += item.text + "\n"
        text = text.strip()

        if not text and not started:
            return
        key = f"{sender}:{ctx.session}"
        if not text:
            reply = _form_reply("Hi! I'm the Trip Planner. Tell me where you'd like to go "
                                "(e.g. \"3 days in Lisbon, EUR 300\") or fill in the form:")
        else:
            try:  # blocking HTTP/SQLite work stays off the event loop
                reply = await asyncio.to_thread(process_message, key, text)
            except Exception:
                ctx.logger.exception("process_message failed")
                reply = _form_reply("Sorry - something went wrong while planning. Please try again:")

        await ctx.send(sender, build_chat_message(reply))

    @chat_proto.on_message(model=ChatAcknowledgement)
    async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        ctx.logger.debug(f"Received chat ack from {sender}: {msg.acknowledged_msg_id}")
else:
    chat_proto = None  # type: ignore[assignment]
