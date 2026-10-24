![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

# Trip Planner Agent

Turns a one-line travel request into a **budget-checked, weather-aware, day-by-day itinerary** with one-click
Google Maps routes and Google Calendar events - all inside the ASI:One chat.

## What it does
1. Understands intent ("3 days in Lisbon, EUR 300, love museums") via the ASI:One LLM (offline parser as fallback).
2. **Research:** geocodes the city (OpenStreetMap), pulls real attractions (Geoapify / OSM / Foursquare),
   a live 7-day forecast (Open-Meteo), live FX rates (Frankfurter) and travel-time estimates, then ranks places
   by price, distance and weather.
3. **Planner:** builds a day-by-day schedule, validates it (budget, max 90 min hops, no duplicates, dates) and
   self-repairs by dropping the costliest stop and retrying.
4. **Interactive cards:** form to collect details -> review card to approve -> final itinerary with Maps + Calendar links.

## Try it
Ask in ASI:One: `Plan 3 days in Lisbon with a EUR 300 budget` or just say `Plan a trip`.

## Honesty
Anything not from a live source is labelled MOCK. Costs are estimates; opening hours are not verified.
