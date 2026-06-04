"""Tests for /api/proposals/* endpoints."""
import asyncio
import tempfile
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build a TestClient backed by a temp DB with a sample proposal seeded."""
    from horizon.internal.database.db import init_db
    from horizon.internal.proposals.models import TradeProposal, ProposalStatus
    from horizon.internal.proposals.queue import ProposalQueue
    from horizon.internal.web.server import create_app
    from horizon.internal.config.settings import settings as real_settings
    from horizon.internal.exchange.registry import ExchangeRegistry
    from horizon.internal.marketdata.fetcher import MarketDataFetcher
    from horizon.internal.ordermanager.manager import OrderManager

    async def build():
        db_path = str(tmp_path / "t.db")
        db = await init_db(db_path, "horizon/internal/database/migrations")
        await db.execute(
            "INSERT INTO exchanges(name, enabled) VALUES (?, 1)",
            ("binance",),
        )
        await db.commit()
        registry = ExchangeRegistry()
        fetcher = MarketDataFetcher(registry=registry, db=db, active_pairlist=None,
                                     active_exchange="binance", poll_interval_seconds=60)
        order_mgr = OrderManager(registry=registry, db=db, active_exchange="binance")
        queue = ProposalQueue(db=db, order_manager=order_mgr, strategy_config={"asset_whitelist":"[]"})
        p = TradeProposal(
            id=str(uuid.uuid4()), status=ProposalStatus.PROPOSED.value,
            exchange="binance", symbol="BTCUSDT", side="buy", order_type="market",
            price=None, volume=0.1, confidence_score=80, risk_tier="low",
            llm_rationale="test", llm_raw_response="{}", technical_context="{}",
            market_snapshot="{}", portfolio_snapshot="{}", guardrail_result=None,
            approved_by=None, approved_at=None, executed_order_id=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            price_drift_threshold_pct=3.0, proposed_price=50000.0,
            created_at=datetime.now(timezone.utc),
            action_type="open",
        )
        await queue.enqueue(p)
        app = create_app(settings=real_settings, db=db, registry=registry,
                          fetcher=fetcher, order_manager=order_mgr)
        app.state.proposal_queue = queue
        return app, p.id

    app, pid = asyncio.run(build())
    return TestClient(app), pid


def test_list_proposals(client):
    c, _ = client
    r = c.get("/api/proposals")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 1
    assert "proposals" in data


def test_get_proposal_detail(client):
    c, pid = client
    r = c.get(f"/api/proposals/{pid}")
    assert r.status_code == 200
    assert r.json()["id"] == pid


def test_get_proposal_404(client):
    c, _ = client
    r = c.get("/api/proposals/nonexistent-id")
    assert r.status_code == 404


def test_proposal_stats(client):
    c, _ = client
    r = c.get("/api/proposals/stats")
    assert r.status_code == 200
    assert "proposed" in r.json()


def test_reject_proposal(client):
    c, pid = client
    r = c.post(f"/api/proposals/{pid}/reject",
                json={"rejected_by": "dashboard_user", "reason": "test"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
