"""Tests for /api/copilot/* endpoints."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from horizon.internal.database.db import init_db
    from horizon.internal.web.server import create_app
    from horizon.internal.config.settings import settings as real_settings
    from horizon.internal.exchange.registry import ExchangeRegistry
    from horizon.internal.marketdata.fetcher import MarketDataFetcher
    from horizon.internal.ordermanager.manager import OrderManager
    from horizon.internal.llm.copilot import CoPilotEngine, CoPilotReply

    async def build():
        db = await init_db(str(tmp_path / "t.db"), "horizon/internal/database/migrations")
        registry = ExchangeRegistry()
        fetcher = MarketDataFetcher(registry=registry, db=db, active_pairlist=None,
                                     active_exchange="binance", poll_interval_seconds=60)
        om = OrderManager(registry=registry, db=db, active_exchange="binance")
        app = create_app(settings=real_settings, db=db, registry=registry, fetcher=fetcher, order_manager=om)
        # Inject CoPilotEngine with stubbed dependencies (no real Anthropic calls)
        cp = CoPilotEngine(
            db=db, anthropic_client=MagicMock(), registry=MagicMock(),
            fetcher=MagicMock(), indicator_calculator=MagicMock(),
            portfolio_tracker=MagicMock(), model_name="test",
        )
        # Patch send_message to avoid real LLM calls
        cp.send_message = AsyncMock(return_value=CoPilotReply(
            message_id="m1", assistant_text="hello world",
            trade_suggestion=None, context_summary={"prices_fed": []},
            token_count_input=10, token_count_output=5, latency_ms=42,
        ))
        app.state.copilot = cp
        return app

    app = asyncio.run(build())
    return TestClient(app)


def test_create_session(client):
    r = client.post("/api/copilot/sessions", json={"title": "test session"})
    assert r.status_code == 200
    assert r.json()["title"] == "test session"


def test_list_sessions(client):
    client.post("/api/copilot/sessions", json={"title": "A"})
    client.post("/api/copilot/sessions", json={"title": "B"})
    r = client.get("/api/copilot/sessions")
    assert r.status_code == 200
    assert len(r.json()["sessions"]) >= 2


def test_send_message(client):
    s = client.post("/api/copilot/sessions", json={"title": "X"}).json()
    r = client.post(f"/api/copilot/sessions/{s['id']}/messages",
                     json={"text": "What about BTC?"})
    assert r.status_code == 200
    assert r.json()["assistant_text"] == "hello world"


def test_delete_session(client):
    s = client.post("/api/copilot/sessions", json={}).json()
    r = client.delete(f"/api/copilot/sessions/{s['id']}")
    assert r.status_code == 200
