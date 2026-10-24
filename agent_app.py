"""
agent_app.py - Main uAgents entry point for Trip Planner.

Configures and starts the trip-planner Agent in mailbox mode:
- Connects to Agentverse mailbox without requiring a public endpoint URL
- Exposes the ASI:One chat protocol via agents/orchestrator.py
- Publishes the protocol manifest so ASI:One can discover and chat with the agent
- Gracefully handles missing optional API keys by falling back to labelled mock data

Run:
    python agent_app.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
_log = logging.getLogger("agent_app")

# ---------------------------------------------------------------------------
# Optional keys (missing keys only log warnings, never crash)
# ---------------------------------------------------------------------------
_OPTIONAL_KEYS = {
    "ASI1_API_KEY": "ASI:One LLM calls will be skipped; orchestrator uses offline mode.",
    "GEOAPIFY_API_KEY": "Geoapify disabled; places/geocoding use free OpenStreetMap sources.",
    "FOURSQUARE_API_KEY": "Foursquare calls disabled; places fallback to mock data.",
    "ORS_API_KEY": "OpenRouteService disabled; routing returns null travel times.",
    "OPENWEATHER_API_KEY": "OpenWeatherMap disabled; weather fallback to mock data.",
    "NOMINATIM_USER_AGENT": "Nominatim disabled; geocoding fallback to mock data.",
    "AGENTVERSE_API_KEY": "Agentverse API key not provided; connecting in mailbox mode using agent attestation.",
}


def warn_missing_optional_keys() -> list[str]:
    """Log a warning for each missing optional key without halting execution."""
    missing = []
    for key, msg in _OPTIONAL_KEYS.items():
        if not os.getenv(key):
            missing.append(key)
            _log.warning("Missing optional env var %s - %s", key, msg)
    return missing


# Check optional keys immediately upon module load
warn_missing_optional_keys()

# ---------------------------------------------------------------------------
# uAgents imports
# ---------------------------------------------------------------------------
try:
    from uagents import Agent, Context, Model
    _UAGENTS_AVAILABLE = True
except ImportError:
    _UAGENTS_AVAILABLE = False
    _log.warning("uagents package not installed - running in offline demo mode.")

from agents.research import research
from agents.orchestrator import chat_proto, handle_message_offline


# ---------------------------------------------------------------------------
# Message models
# ---------------------------------------------------------------------------
if _UAGENTS_AVAILABLE:
    class ResearchRequest(Model):
        destination_city: str
        country: str = ""
        days: int = 3
        budget: float = 0.0
        currency: str = "USD"
        interests: list = []

    class ResearchResponseMsg(Model):
        payload: str  # JSON string of ResearchResponse


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------
def create_agent(
    seed: str | None = None,
    agentverse_api_key: str | None = None,
    readme_path: str | Path | None = None,
    port: int | None = None,
) -> Agent:
    """
    Build and configure the trip-planner Agent in mailbox mode.

    Exits with a clear message if AGENT_SEED is missing.
    """
    if not _UAGENTS_AVAILABLE:
        raise RuntimeError("uagents package is required to create the agent.")

    resolved_seed = seed if seed is not None else os.getenv("AGENT_SEED")
    if not resolved_seed:
        error_msg = (
            "AGENT_SEED environment variable is required, must be set and kept fixed, "
            "because it determines the deterministic agent address."
        )
        _log.error(error_msg)
        sys.exit(f"Error: {error_msg}")

    resolved_api_key = (
        agentverse_api_key
        if agentverse_api_key is not None
        else os.getenv("AGENTVERSE_API_KEY", "")
    )

    agentverse_config = (
        f"{resolved_api_key}@https://agentverse.ai" if resolved_api_key else None
    )
    resolved_readme = str(readme_path) if readme_path is not None else (
        "AGENT_README.md" if Path("AGENT_README.md").exists() else "README.md"
    )
    resolved_port = port if port is not None else int(os.getenv("AGENT_PORT", "8000"))

    agent = Agent(
        name="trip-planner",
        seed=resolved_seed,
        port=resolved_port,
        mailbox=True,
        publish_agent_details=True,
        readme_path=resolved_readme,
        agentverse=agentverse_config,
    )

    # Include chat protocol with manifest published for ASI:One discovery
    if chat_proto is not None:
        agent.include(chat_proto, publish_manifest=True)
        _log.info("Chat protocol included and manifest published.")

    @agent.on_event("startup")
    async def on_startup(ctx: Context):
        ctx.logger.info("Trip Planner Agent started. Address: %s", agent.address)
        ctx.logger.info(
            "Open https://agentverse.ai to verify that your agent is connected to its mailbox."
        )

    @agent.on_message(model=ResearchRequest)
    async def handle_research(ctx: Context, sender: str, msg: ResearchRequest):
        """Handle a JSON research request message and reply with a JSON response."""
        import json
        ctx.logger.info("Research request from %s: %s", sender, msg.destination_city)
        result = research({
            "destination_city": msg.destination_city,
            "country": msg.country or None,
            "days": msg.days,
            "budget": msg.budget or None,
            "currency": msg.currency,
            "interests": msg.interests,
        })
        await ctx.send(sender, ResearchResponseMsg(payload=json.dumps(result)))

    return agent


# ---------------------------------------------------------------------------
# Module-level agent instance
# ---------------------------------------------------------------------------
AGENT_SEED = os.getenv("AGENT_SEED")
AGENTVERSE_API_KEY = os.getenv("AGENTVERSE_API_KEY", "")

agent: Agent | None = None
if _UAGENTS_AVAILABLE and AGENT_SEED:
    agent = create_agent(AGENT_SEED, AGENTVERSE_API_KEY)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if not _UAGENTS_AVAILABLE:
        _log.info("Running in offline demo mode (uagents not installed).")
        reply = handle_message_offline("demo-user", "I want to visit Tokyo for 3 days with a $1000 budget")
        print("\n--- Demo reply ---")
        print(reply)
        sys.exit(0)

    if not AGENT_SEED:
        sys.exit(
            "Error: AGENT_SEED environment variable is required and must be kept fixed, "
            "because it determines the deterministic agent address. "
            "Please configure AGENT_SEED in your .env file."
        )

    if agent is None:
        agent = create_agent(AGENT_SEED, AGENTVERSE_API_KEY)

    _log.info("Agent address: %s", agent.address)
    _log.info("Open https://agentverse.ai to verify that your agent is connected to its mailbox.")
    _log.info("Starting uAgents agent in mailbox mode...")
    agent.run()
