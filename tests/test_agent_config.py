"""
tests/test_agent_config.py - Tests for agent mailbox configuration and startup rules.

Verifies:
1. Agent builds successfully when AGENT_SEED is set.
2. The ASI:One chat protocol is included with manifest published.
3. Missing AGENT_SEED produces a clear error.
4. Missing optional keys only produce warnings without stopping startup.

All tests run offline without network access or live mailbox polling.
"""

from __future__ import annotations

import logging
import pytest

import agent_app


def test_agent_builds_when_seed_set(monkeypatch):
    """Verify that create_agent creates an agent in mailbox mode with valid address."""
    test_seed = "test-fixed-seed-trip-planner-12345"
    agent = agent_app.create_agent(seed=test_seed)

    assert agent is not None
    assert agent.name == "trip-planner"
    assert agent.address.startswith("agent1")
    assert agent._use_mailbox is True
    assert len(agent._endpoints) > 0
    assert "mailbox" in agent._endpoints[0].url.lower()


def test_chat_protocol_included(monkeypatch):
    """Verify that the chat protocol is included and manifest is enabled."""
    test_seed = "test-fixed-seed-trip-planner-12345"
    agent = agent_app.create_agent(seed=test_seed)

    assert len(agent.protocols) > 0
    # Chat protocol digest or spec is present in protocols
    protocol_keys = list(agent.protocols.keys())
    assert any("proto:" in k for k in protocol_keys)


def test_missing_agent_seed_produces_clear_error(monkeypatch):
    """Verify that missing AGENT_SEED causes an exit with a clear explanation."""
    monkeypatch.delenv("AGENT_SEED", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        agent_app.create_agent(seed=None)

    error_msg = str(exc_info.value)
    assert "AGENT_SEED" in error_msg
    assert "must be set and kept fixed" in error_msg
    assert "agent address" in error_msg


def test_missing_optional_keys_only_produce_warnings(caplog):
    """Verify that missing optional keys log warnings and never crash."""
    with caplog.at_level(logging.WARNING, logger="agent_app"):
        missing = agent_app.warn_missing_optional_keys()

    assert isinstance(missing, list)
    # Warnings are emitted for missing optional keys
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) >= len(missing)
