# Submission checklist (Fetch-A-Thon 3.0 - ASI:One Agent Challenge)

The code in this repo is complete and tested offline (`pytest` -> 125 passed). The steps below need **your**
accounts (Agentverse, ASI:One, GitHub) and could not be done from the build sandbox.

## 1. Run the agent (mailbox mode)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # set AGENT_SEED (long random string, keep fixed); optionally ASI1_API_KEY, AGENTVERSE_API_KEY
python agent_app.py      # prints agent1q... address and an Agent Inspector link
```
Keep it running (or use the Dockerfile on any always-on host: `docker build -t trip-planner . && docker run --env-file .env trip-planner`).

## 2. Register on Agentverse
1. Open the **Agent Inspector** URL from the startup log -> **Connect** -> **Mailbox** -> approve.
2. In Agentverse -> *Agents* -> your agent -> check the README shows (it is `AGENT_README.md`) and the
   `innovationlab` / `hackathon` tags are present; set a clear description; make it **Public**.
3. Confirm the *Chat Protocol* appears under protocols (manifest is published automatically).
4. Copy the **Agent Profile URL** (https://agentverse.ai/agents/details/agent1q...).

## 3. Test in ASI:One
1. https://asi1.ai -> enable **Agents** -> search "Trip Planner" (or `@agent1q...`).
2. Run the full flow: `Plan a trip` -> fill form -> review card -> **Confirm** -> itinerary links.
3. Also try: `Plan 3 days in Lisbon with a EUR 300 budget`.
4. Use **Share** on the conversation -> copy the **public shared chat URL**.
5. Optional: test card payloads at https://asi1.ai/developer/card-playground

## 4. Publish the repo
```bash
git remote add origin https://github.com/<you>/trip-planner-agent.git
git push -u origin main      # repo must be PUBLIC; .env is git-ignored
```

## 5. Submit at https://asi1.ai/festival/fetchathon3/dashboard
Use the **Project Submission Agent** and provide: GitHub URL, ASI:One shared-chat URL, Agentverse profile URL,
demo video (screen-record step 3, ~2 min), and this description:

> **Problem:** trip planning is spread over maps, weather, FX and budget tools.
> **Users:** independent travellers. **Outcome:** a validated, budget-checked day-by-day itinerary with live
> weather/FX, plus one-click Google Maps routes and Calendar events - completed entirely inside ASI:One using
> Interactive Cards (form -> review -> confirm).

## Not implemented (honest scope)
- Payment Protocol (bonus) and separately-deployed sub-agents (roles are in-process modules).
- Transport options are mock data (clearly labelled); no real booking is made.
