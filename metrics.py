"""
metrics.py – In-process counters and latency samples for the trip-planner.

All counters are module-level integers/floats so any import sees the same
values within one process. Thread safety is provided by threading.Lock.

Usage
-----
    import metrics
    metrics.inc_request()
    metrics.record_latency(123.4)
    metrics.print_report()          # or:  python -m metrics
"""

from __future__ import annotations

import threading
import time
from typing import List

from core.storage import fetch_call_logs

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
_request_count: int = 0
_success_count: int = 0
_api_calls: int = 0
_api_errors: int = 0
_fallback_uses: int = 0
_validator_failures: int = 0

# ---------------------------------------------------------------------------
# Latency samples (milliseconds)
# ---------------------------------------------------------------------------
_latency_samples: List[float] = []

# ---------------------------------------------------------------------------
# Coverage (how many requests returned >= 1 day with >= 1 activity)
# ---------------------------------------------------------------------------
_covered_count: int = 0


# ---------------------------------------------------------------------------
# Increment helpers
# ---------------------------------------------------------------------------

def inc_request() -> None:
    global _request_count
    with _lock:
        _request_count += 1


def inc_success() -> None:
    global _success_count
    with _lock:
        _success_count += 1


def inc_api_call() -> None:
    global _api_calls
    with _lock:
        _api_calls += 1


def inc_api_error() -> None:
    global _api_errors
    with _lock:
        _api_errors += 1


def inc_fallback() -> None:
    global _fallback_uses
    with _lock:
        _fallback_uses += 1


def inc_validator_failure() -> None:
    global _validator_failures
    with _lock:
        _validator_failures += 1


def inc_covered() -> None:
    """Call when a request produced ≥1 day with ≥1 activity."""
    global _covered_count
    with _lock:
        _covered_count += 1


def record_latency(ms: float) -> None:
    with _lock:
        _latency_samples.append(ms)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def snapshot() -> dict:
    """Return a dict of all current metric values."""
    with _lock:
        samples = sorted(_latency_samples)
    p95 = _percentile(samples, 95)
    rq = _request_count
    return {
        "request_count": rq,
        "success_count": _success_count,
        "api_calls": _api_calls,
        "api_errors": _api_errors,
        "fallback_uses": _fallback_uses,
        "validator_failures": _validator_failures,
        "p95_latency_ms": p95,
        "success_rate": _safe_div(_success_count, rq),
        "api_error_rate": _safe_div(_api_errors, _api_calls),
        "fallback_rate": _safe_div(_fallback_uses, rq),
        "coverage_rate": _safe_div(_covered_count, rq),
        "covered_count": _covered_count,
    }


def _percentile(sorted_samples: List[float], pct: float) -> float | None:
    if not sorted_samples:
        return None
    k = (len(sorted_samples) - 1) * pct / 100
    f = int(k)
    c = f + 1
    if c >= len(sorted_samples):
        return sorted_samples[-1]
    return sorted_samples[f] + (k - f) * (sorted_samples[c] - sorted_samples[f])


def _safe_div(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


# ---------------------------------------------------------------------------
# Derive metrics from the SQLite call log (for offline/historical reporting)
# ---------------------------------------------------------------------------

def report_from_db(limit: int = 10_000) -> dict:
    """Aggregate the call_log table and return computed metrics."""
    rows = fetch_call_logs(limit=limit)
    total = len(rows)
    errors = sum(1 for r in rows if r["is_error"])
    fallbacks = sum(1 for r in rows if r["is_fallback"])
    latencies = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
    return {
        "total_calls_in_log": total,
        "error_count": errors,
        "fallback_count": fallbacks,
        "api_error_rate": _safe_div(errors, total),
        "fallback_rate": _safe_div(fallbacks, total),
        "p95_latency_ms": _percentile(latencies, 95),
    }


# ---------------------------------------------------------------------------
# Print report (entry point: python -m metrics)
# ---------------------------------------------------------------------------

def print_report() -> None:
    import json
    in_proc = snapshot()
    from_db = report_from_db()
    print("=== In-process metrics ===")
    print(json.dumps(in_proc, indent=2, default=str))
    print("\n=== Call-log metrics (SQLite) ===")
    print(json.dumps(from_db, indent=2, default=str))


if __name__ == "__main__":
    print_report()
