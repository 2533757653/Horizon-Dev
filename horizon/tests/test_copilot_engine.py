"""Tests for CoPilotEngine."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_create_and_send_message(tmp_path):
    from horizon.internal.database.db import init_db
    from horizon.internal.llm.copilot import CoPilotEngine

    db = await init_db(str(tmp_path / "t.db"), "horizon/internal/database/migrations")

    # Mock Anthropic client
    fake_msg = MagicMock()
    # The production code filters blocks by `block.type == "text"`. The
    # integration agent added that filter when patching ThinkingBlock
    # interleaving — MagicMock's auto-attrs don't satisfy the filter
    # unless we set `.type` explicitly.
    text_block = MagicMock()
    text_block.text = json.dumps({
        "assistant_text": "Based on RSI 38, modest oversold. Consider a small entry.",
        "trade_suggestion": {
            "exchange": "binance", "symbol": "BTCUSDT",
            "side": "buy", "order_type": "limit",
            "price": "60000", "volume": "0.01",
            "rationale": "oversold bounce",
        }
    })
    text_block.type = "text"
    fake_msg.content = [text_block]
    fake_msg.usage = MagicMock(input_tokens=200, output_tokens=80)
    anth = MagicMock()
    anth.messages.create = AsyncMock(return_value=fake_msg)

    # Mock registry/fetcher/indicators/portfolio
    registry = MagicMock()
    registry.get_all_tickers = AsyncMock(return_value=[])
    fetcher = MagicMock()
    indicators = MagicMock()
    indicators.compute_for_symbol = AsyncMock(return_value=MagicMock(
        rsi_14=38, macd_line=0.1, macd_signal=0.05, macd_histogram=0.05,
        ema_20=60100, ema_50=59900, bollinger_upper=61000, bollinger_lower=59000, atr_14=300,
    ))
    portfolio_tracker = MagicMock()
    portfolio_tracker.get_snapshot = AsyncMock(return_value={"total_usdt_value": "20000", "balances": []})

    engine = CoPilotEngine(
        db=db, anthropic_client=anth, registry=registry, fetcher=fetcher,
        indicator_calculator=indicators, portfolio_tracker=portfolio_tracker,
        model_name="MiniMax-M3",
    )

    session = await engine.create_session(title="test")
    assert session.id

    reply = await engine.send_message(session.id, "BTC 现在能买吗?")
    assert reply.assistant_text
    assert reply.trade_suggestion is not None
    assert reply.trade_suggestion["symbol"] == "BTCUSDT"

    messages = await engine.get_messages(session.id)
    assert len(messages) == 2  # user + assistant
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_list_and_delete_session(tmp_path):
    from horizon.internal.database.db import init_db
    from horizon.internal.llm.copilot import CoPilotEngine

    db = await init_db(str(tmp_path / "t.db"), "horizon/internal/database/migrations")
    engine = CoPilotEngine(
        db=db, anthropic_client=MagicMock(), registry=MagicMock(), fetcher=MagicMock(),
        indicator_calculator=MagicMock(), portfolio_tracker=MagicMock(), model_name="m",
    )
    s1 = await engine.create_session(title="A")
    s2 = await engine.create_session(title="B")
    sessions = await engine.list_sessions()
    assert len(sessions) == 2

    await engine.delete_session(s1.id)
    sessions = await engine.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].id == s2.id
