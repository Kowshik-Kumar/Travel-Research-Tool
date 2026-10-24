"""
core/storage.py – SQLite persistence layer for http cache, geocode cache,
places cache, FX rates, and call logs.

All tables are created on first import. The file is data/cache.sqlite
relative to the project root (or $CACHE_DB env var).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

# Resolve the database path once at import time.
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "cache.sqlite"
DB_PATH: Path = Path(os.getenv("CACHE_DB", str(_DEFAULT_DB)))


def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a thread-safe SQLite connection with WAL mode."""
    _ensure_dir()
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key         TEXT PRIMARY KEY,
    body        TEXT NOT NULL,
    status_code INTEGER NOT NULL DEFAULT 200,
    cached_at   REAL  NOT NULL,
    ttl_seconds REAL  NOT NULL DEFAULT 86400
);

CREATE TABLE IF NOT EXISTS geocode_cache (
    query       TEXT PRIMARY KEY,
    city        TEXT,
    country     TEXT,
    lat         REAL,
    lon         REAL,
    timezone    TEXT,
    cached_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS places_cache (
    city_key    TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    cached_at   REAL NOT NULL,
    ttl_seconds REAL NOT NULL DEFAULT 86400
);

CREATE TABLE IF NOT EXISTS fx_rates (
    base        TEXT NOT NULL,
    target      TEXT NOT NULL,
    rate        REAL NOT NULL,
    date        TEXT NOT NULL,
    cached_at   REAL NOT NULL,
    PRIMARY KEY (base, target, date)
);

CREATE TABLE IF NOT EXISTS call_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    method      TEXT    NOT NULL,
    url_host    TEXT    NOT NULL,
    path        TEXT    NOT NULL DEFAULT '',
    status      INTEGER,
    latency_ms  REAL,
    is_error    INTEGER NOT NULL DEFAULT 0,
    is_fallback INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);
"""


def init_db() -> None:
    """Create all tables if they do not exist."""
    with _conn() as con:
        con.executescript(_SCHEMA)


# Call init on import so all modules can use the db immediately.
try:
    init_db()
except Exception as exc:  # pragma: no cover
    import warnings
    warnings.warn(f"Could not initialise SQLite database: {exc}")


# ---------------------------------------------------------------------------
# HTTP cache helpers
# ---------------------------------------------------------------------------

def http_cache_get(key: str) -> Optional[dict[str, Any]]:
    """Return cached response dict or None if missing / expired."""
    with _conn() as con:
        row = con.execute(
            "SELECT body, status_code, cached_at, ttl_seconds FROM http_cache WHERE key=?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    body, status_code, cached_at, ttl = row
    if time.time() - cached_at > ttl:
        return None
    return {"body": json.loads(body), "status_code": status_code}


def http_cache_set(key: str, body: Any, status_code: int = 200, ttl_seconds: float = 86400) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO http_cache (key, body, status_code, cached_at, ttl_seconds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, json.dumps(body), status_code, time.time(), ttl_seconds),
        )


# ---------------------------------------------------------------------------
# Geocode cache helpers
# ---------------------------------------------------------------------------

def geocode_cache_get(query: str) -> Optional[dict[str, Any]]:
    with _conn() as con:
        row = con.execute(
            "SELECT city, country, lat, lon, timezone, cached_at FROM geocode_cache WHERE query=?",
            (query,),
        ).fetchone()
    if row is None:
        return None
    city, country, lat, lon, timezone, _ = row
    return {"city": city, "country": country, "lat": lat, "lon": lon, "timezone": timezone}


def geocode_cache_set(query: str, data: dict[str, Any]) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO geocode_cache (query, city, country, lat, lon, timezone, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query,
                data.get("city"),
                data.get("country"),
                data.get("lat"),
                data.get("lon"),
                data.get("timezone"),
                time.time(),
            ),
        )


# ---------------------------------------------------------------------------
# Places cache helpers
# ---------------------------------------------------------------------------

def places_cache_get(city_key: str) -> Optional[list]:
    with _conn() as con:
        row = con.execute(
            "SELECT payload, cached_at, ttl_seconds FROM places_cache WHERE city_key=?",
            (city_key,),
        ).fetchone()
    if row is None:
        return None
    payload, cached_at, ttl = row
    if time.time() - cached_at > ttl:
        return None
    return json.loads(payload)


def places_cache_set(city_key: str, places: list, ttl_seconds: float = 86400) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO places_cache (city_key, payload, cached_at, ttl_seconds)
            VALUES (?, ?, ?, ?)
            """,
            (city_key, json.dumps(places), time.time(), ttl_seconds),
        )


# ---------------------------------------------------------------------------
# FX rate helpers
# ---------------------------------------------------------------------------

def fx_rate_get(base: str, target: str, date: str) -> Optional[float]:
    with _conn() as con:
        row = con.execute(
            "SELECT rate FROM fx_rates WHERE base=? AND target=? AND date=?",
            (base, target, date),
        ).fetchone()
    return row[0] if row else None


def fx_rate_set(base: str, target: str, date: str, rate: float) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO fx_rates (base, target, rate, date, cached_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (base, target, rate, date, time.time()),
        )


# ---------------------------------------------------------------------------
# Call log helpers
# ---------------------------------------------------------------------------

def log_call(
    method: str,
    url_host: str,
    path: str = "",
    status: Optional[int] = None,
    latency_ms: Optional[float] = None,
    is_error: bool = False,
    is_fallback: bool = False,
    note: Optional[str] = None,
) -> None:
    try:
        with _conn() as con:
            con.execute(
                """
                INSERT INTO call_log (ts, method, url_host, path, status, latency_ms,
                                      is_error, is_fallback, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    method,
                    url_host,
                    path,
                    status,
                    latency_ms,
                    int(is_error),
                    int(is_fallback),
                    note,
                ),
            )
    except Exception:
        pass  # Never crash the app because of a logging failure.


def fetch_call_logs(limit: int = 1000) -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            """
            SELECT ts, method, url_host, path, status, latency_ms, is_error, is_fallback, note
            FROM call_log ORDER BY ts DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    keys = ["ts", "method", "url_host", "path", "status", "latency_ms",
            "is_error", "is_fallback", "note"]
    return [dict(zip(keys, r)) for r in rows]
