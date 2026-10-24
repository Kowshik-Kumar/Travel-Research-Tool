# Trip Planner - Multi-Agent System

A trip-planning agent built with **Python 3.11+**, **Fetch.ai uAgents** and the **Agent Chat Protocol**, discoverable on **Agentverse / ASI:One**.

**Problem:** planning a trip means juggling maps, weather, FX rates and budgets across five tabs.
**Users:** independent travellers who want a realistic, budget-checked plan in one chat.
**Outcome:** a validated day-by-day itinerary plus one-click Google Maps routes and Google Calendar events.

Internally it is orchestrated as specialised roles - Orchestrator (intent, sessions, cards), Research
(geocode -> places -> weather -> FX -> ranking) and Planner (scheduling + validator/repair loop) - exposed as one Agentverse agent.

**ASI:One flow (all inside chat):** free text or form card -> research + plan -> review card (approve / change) -> final itinerary with action links.

**Live, keyless data out of the box:** OpenStreetMap (Nominatim + Overpass), Open-Meteo, Frankfurter. Optional keys upgrade places/routing/weather.
See [SUBMISSION.md](SUBMISSION.md) for the deployment + submission checklist.

---

## Architecture

```
agent_app.py          <- uAgents entry point + Agentverse mailbox
agents/
  orchestrator.py     <- Chat-protocol handler; sessions, intent, card flow
  cards.py            <- ASI:One Interactive Card builders + Maps/Calendar links
  research.py         <- Pure research function: geocode -> places -> score -> rank
  planner.py          <- Day-by-day schedule builder with validator retry
tools/
  geocode.py          <- Nominatim geocoding (rate-limited, SQLite-cached)
  places.py           <- Geoapify + Foursquare + OSM fallback (merged, deduped, cached)
  currency.py         <- Frankfurter FX rates (daily-cached, no key needed)
  destination.py      <- Combines geocode + places + FX into DestinationData
  routing.py          <- OpenRouteService travel-time matrix
  weather.py          <- OpenWeatherMap 5-day forecast with score formula
  transport_mock.py   <- Mock transport options (train/bus/flight, all labelled)
  person2_adapter.py  <- Integration point for Person 2's get_destination_data
core/
  schemas.py          <- Pydantic models: TripRequest, DestinationData, Plan, ...
  storage.py          <- SQLite: http_cache, geocode_cache, places_cache, fx_rates, call_log
  logging_utils.py    <- HTTP wrapper with retry, backoff, timing, sanitised logging
  validator.py        <- Plan validation: budget, travel time, duplicates, dates, ...
metrics.py            <- In-process counters + call-log aggregation
tests/
  test_phase1.py      <- Storage, schemas, metrics, validator rules
  test_research.py    <- 7 research agent unit tests (all mocked)
  test_tools.py       <- Geocode, currency, routing, weather, places (mocked HTTP)
  test_planner.py     <- Planner ordering, currency, retry, validator edge cases
  test_orchestrator.py <- Orchestrator session state, clarification, LLM stub
  test_end_to_end.py  <- 15 prompts x 3 assertions (intent, validation, coverage)
  test_cards_and_protocol.py <- cards flow, FX, live-data parsers, ACP handler
  test_agent_config.py <- Mailbox mode, agent initialization, protocol, and error handling
  test_prompts.json   <- 15 prompts across 5+ countries, 2 ambiguous, 3 non-USD
  fixtures/           <- Recorded city fixture data (Kyoto, Lisbon, Cusco)
data/
  cache.sqlite        <- Auto-created on first run (gitignored)
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment variables
```bash
cp .env.example .env
# Edit .env and fill in your AGENT_SEED and API keys
```

### 3. Environment variables

| Variable | Default | Required for |
|---|---|---|
| `AGENT_SEED` | None (required) | Deterministic agent address (keep fixed) |
| `AGENTVERSE_API_KEY` | None (optional) | Agentverse account integration |
| `ASI1_API_KEY` | None | LLM intent extraction / ASI:One |
| `ASI1_BASE_URL` | `https://api.asi1.ai/v1` | ASI:One API endpoint |
| `ASI1_MODEL` | `asi1-mini` | ASI:One model selection |
| `AGENT_PORT` | `8000` | Local inspector/server port |
| `NOMINATIM_USER_AGENT` | None | Geocoding (Nominatim) |
| `GEOAPIFY_API_KEY` | None | Places + geocoding (Geoapify) |
| `FOURSQUARE_API_KEY` | None | Additional places data |
| `ORS_API_KEY` | None | Travel-time routing |
| `OPENWEATHER_API_KEY` | None | Weather forecast |
| `CACHE_DB` | `data/cache.sqlite` | SQLite path override |

**Missing optional keys never crash the app.** Each affected tool falls back to labelled mock data and logs a `WARNING`. `AGENT_SEED` is required because it guarantees a fixed agent address on Agentverse.

---

## Connecting to Agentverse (mailbox mode)

The agent operates in mailbox mode without requiring any public IP, port forwarding, reverse proxy, or webhook tunnel. It polls Agentverse directly for incoming messages using cryptographic attestation.

Follow these steps to connect and publish the agent:

1. **Create an account** at [agentverse.ai](https://agentverse.ai).
2. **Create an Agentverse API key** in account settings (Profile -> API Keys) and put it in `.env` as `AGENTVERSE_API_KEY`.
3. **Set AGENT_SEED** in `.env` to a long random string and **keep it fixed**. The seed determines your cryptographic private key and agent address (`agent1q...`). If the seed changes, your address changes and you will have to re-register.
4. **Run the agent locally**:
   ```bash
   python agent_app.py
   ```
   Copy the printed agent address from the startup log (starts with `agent1q...`).
5. **In Agentverse**, confirm the agent shows as connected through its mailbox. Give it a name, description, and README, and set its visibility to **Public**.
6. **Wait a few minutes**, then search for the agent in [ASI:One](https://asi1.ai) and send a test message such as:
   `"Plan 3 days in Lisbon with a EUR 800 budget"`

### Troubleshooting

- **Address changed:**
  - *Cause:* `AGENT_SEED` in `.env` was changed or not set consistently.
  - *Fix:* Ensure `AGENT_SEED` is preserved across restarts in your `.env` file.
- **Agent not discoverable:**
  - *Cause:* The agent is not set to **Public** in Agentverse, or the protocol manifest was not published.
  - *Fix:* Verify that visibility is toggled to Public in Agentverse, and confirm `agent.include(chat_proto, publish_manifest=True)` is called (already configured in `agent_app.py`).
- **No replies:**
  - *Cause:* The local `agent_app.py` process is not running, or the chat protocol is missing.
  - *Fix:* Verify `python agent_app.py` is actively running in your terminal and that you see the log confirming `Chat protocol included and manifest published.`

---

## Running Tests

All tests run fully offline without API keys or live network access:
```bash
pytest -v
```

Expected: **all tests pass** with mock/fixture data.

To run specific test files:
```bash
pytest tests/test_agent_config.py -v
pytest tests/test_phase1.py -v
pytest tests/test_research.py -v
pytest tests/test_end_to_end.py -v
```

---

## Running the Agent

```bash
python agent_app.py
```

- When `AGENT_SEED` is set, the agent starts in mailbox mode, prints its address, and begins polling the Agentverse mailbox.
- Missing optional external API keys log a `WARNING` and fall back to labelled mock data.

---

## Metrics

View in-process and call-log metrics:
```bash
python -m metrics
```

---

## What Is Mocked vs Live

| Component | Status |
|---|---|
| Geocoding (Nominatim) | Live by default (set `TRIP_OFFLINE=1` for mock) |
| Places (OpenStreetMap/Overpass) | Live, keyless; used when no Geoapify/Foursquare key |
| Places + geocoding (Geoapify) | Live when `GEOAPIFY_API_KEY` is set |
| Places (Foursquare) | Mock when `FOURSQUARE_API_KEY` not set |
| FX Rates (Frankfurter) | Mock fallback (Frankfurter needs no key; mock used only on network error) |
| Routing (ORS) | Straight-line estimates when `ORS_API_KEY` not set |
| Weather | Open-Meteo live (keyless); OpenWeatherMap if key set; mock only on failure |
| LLM (ASI:One) | Offline parser when `ASI1_API_KEY` not set |
| Transport options | Always mock (`transport_mock.py`) - no live API |
| Agentverse mailbox | Configured in mailbox mode; requires active Agentverse account to verify |
| ASI:One discoverability | Requires publishing on Agentverse to verify live |

All mock data is clearly labelled (`source: "mock"`, `is_mock: true`, `"MOCK DATA - not live"`).

---

## Data Contracts

See [`core/schemas.py`](core/schemas.py) for all Pydantic models:
- `TripRequest`
- `DestinationData`
- `ResearchResponse`
- `Plan`

---

## Authors

Multi-agent system designed across 4 roles:
- **Person 1**: Orchestrator + ASI:One chat protocol
- **Person 2**: Research tools (geocode, places, currency, destination)
- **Person 3**: Routing, weather, transport mock, research agent
- **Person 4**: Planner + validator
