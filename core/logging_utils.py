"""
core/logging_utils.py – Shared HTTP call wrapper with retry/backoff, timing,
and call-log integration. Never logs API keys.

Usage:
    from core.logging_utils import http_get, http_post

Both functions accept the same arguments as httpx.Client.get/post and return
an httpx.Response (or raise on permanent failure).
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Optional

import httpx

from core.storage import log_call

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.0  # seconds; doubles each retry
_DEFAULT_TIMEOUT = 15.0  # seconds


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _host_only(url: str) -> str:
    """Extract just the host from a URL to avoid logging paths with keys."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc or url
    except Exception:
        return url


def _sanitise_path(url: str) -> str:
    """Return the path portion with query string stripped (keys live there)."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.path or ""
    except Exception:
        return ""


def _do_request(
    method: str,
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> httpx.Response:
    """Execute one HTTP request, log the result, and return the response."""
    host = _host_only(url)
    path = _sanitise_path(url)
    t0 = time.perf_counter()
    status: Optional[int] = None
    is_error = False
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = getattr(client, method.lower())(url, **kwargs)
        status = resp.status_code
        is_error = resp.status_code >= 400
        return resp
    except Exception as exc:
        is_error = True
        raise exc
    finally:
        latency_ms = (time.perf_counter() - t0) * 1000
        log_call(
            method=method.upper(),
            url_host=host,
            path=path,
            status=status,
            latency_ms=latency_ms,
            is_error=is_error,
        )


def http_request(
    method: str,
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    retries: int = _MAX_RETRIES,
    **kwargs: Any,
) -> httpx.Response:
    """
    Execute an HTTP request with automatic retries and exponential backoff.

    :param method: HTTP method (GET, POST, …)
    :param url:    Full URL (query-string keys are never logged)
    :param timeout: Per-attempt timeout in seconds
    :param retries: Number of *additional* attempts after the first failure
    :raises httpx.HTTPError: on permanent failure after all retries
    """
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = _do_request(method, url, timeout=timeout, **kwargs)
            if resp.status_code < 500:
                # Don't retry client errors (4xx), only server errors (5xx)
                return resp
            # Treat 5xx as retriable
            last_exc = httpx.HTTPStatusError(
                f"Server error {resp.status_code}", request=resp.request, response=resp
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            last_exc = exc
        if attempt < retries:
            sleep_time = _BACKOFF_BASE * (2 ** attempt)
            time.sleep(sleep_time)
    raise last_exc  # type: ignore[misc]


def http_get(url: str, *, timeout: float = _DEFAULT_TIMEOUT, **kwargs: Any) -> httpx.Response:
    """Convenience wrapper for GET requests."""
    return http_request("GET", url, timeout=timeout, **kwargs)


def http_post(url: str, *, timeout: float = _DEFAULT_TIMEOUT, **kwargs: Any) -> httpx.Response:
    """Convenience wrapper for POST requests."""
    return http_request("POST", url, timeout=timeout, **kwargs)
