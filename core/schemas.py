"""
core/schemas.py – Shared data contracts for the trip-planner multi-agent system.

All contracts are expressed as both TypedDict (for structural typing) and
pydantic BaseModel (for runtime validation / JSON serialisation).
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# TripRequest
# ---------------------------------------------------------------------------

class TripRequest(BaseModel):
    destination_city: Optional[str] = None
    country: Optional[str] = None
    days: Optional[int] = None
    budget: Optional[float] = None
    currency: str = Field(default="USD", description="ISO 4217 currency code")
    interests: List[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: Optional[str] = None

    @field_validator("currency")
    @classmethod
    def currency_must_be_iso(cls, v: str) -> str:
        if len(v) != 3:
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return v.upper()


# ---------------------------------------------------------------------------
# Place (used inside DestinationData)
# ---------------------------------------------------------------------------

class Place(BaseModel):
    name: str
    category: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    price_level: Optional[int] = Field(default=None, ge=1, le=4)
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    est_cost_usd: float = 0.0
    source: str = "unknown"


# ---------------------------------------------------------------------------
# DestinationData (returned by get_destination_data)
# ---------------------------------------------------------------------------

class DestinationData(BaseModel):
    city: str
    country: str
    lat: float
    lon: float
    timezone: str
    currency_code: str
    fx_rate_to_usd: float = 1.0
    places: List[Place] = Field(default_factory=list)
    fetched_at: str
    is_mock: bool = False


# ---------------------------------------------------------------------------
# WeatherSummary entry
# ---------------------------------------------------------------------------

class WeatherDay(BaseModel):
    date: str
    avg_temp_c: float
    rain_prob: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1, description="1 = warm and dry, 0 = stormy or freezing")


# ---------------------------------------------------------------------------
# RankedPlace (one entry in ResearchResponse.ranked)
# ---------------------------------------------------------------------------

class RankedPlace(BaseModel):
    rank: int
    name: str
    category: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    est_cost_usd: float = 0.0
    travel_minutes_from_center: Optional[float] = None
    weather_score: float = Field(default=0.5, ge=0, le=1)
    score: float = Field(default=0.0, ge=0, le=1)
    is_mock: bool = False


# ---------------------------------------------------------------------------
# TransportOption (one entry in ResearchResponse.transport_options)
# ---------------------------------------------------------------------------

class TransportOption(BaseModel):
    mode: str
    provider: str
    est_duration_min: Optional[int] = None
    est_price_usd: Optional[float] = None
    is_mock: bool = False
    label: str = ""


# ---------------------------------------------------------------------------
# ResearchResponse
# ---------------------------------------------------------------------------

class ResearchResponse(BaseModel):
    status: str = Field(default="ok", pattern="^(ok|error)$")
    city: Optional[str] = None
    ranked: List[RankedPlace] = Field(default_factory=list)
    transport_options: List[TransportOption] = Field(default_factory=list)
    weather_summary: List[WeatherDay] = Field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Activity (one item in a day's schedule)
# ---------------------------------------------------------------------------

class Activity(BaseModel):
    name: str
    start_time: str  # HH:MM
    duration_min: int = 90
    est_cost_usd: float = 0.0
    travel_minutes_from_previous: Optional[float] = None


# ---------------------------------------------------------------------------
# DayPlan
# ---------------------------------------------------------------------------

class DayPlan(BaseModel):
    date: str  # ISO 8601
    activities: List[Activity] = Field(default_factory=list)
    day_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

class Plan(BaseModel):
    days: List[DayPlan] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    budget_usd: float = 0.0
    currency: str = "USD"
    fx_rate_used: float = 1.0
