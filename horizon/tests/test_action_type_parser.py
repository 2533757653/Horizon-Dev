"""Tests for action_type field in parser + TradeProposal."""
import json

import pytest

from horizon.internal.llm.parser import LLMResponseParser


WHITELIST = ["BTCUSDT", "ETHUSDT"]


def _make_response(action_type=None):
    proposal = {
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "side": "buy",
        "order_type": "market",
        "volume": "0.1",
        "confidence_score": 80,
        "risk_tier": "low",
        "rationale": "test",
    }
    if action_type is not None:
        proposal["action_type"] = action_type
    return json.dumps({
        "analysis_summary": "s",
        "confidence_explanation": "c",
        "proposals": [proposal],
    })


def test_parser_default_action_type_is_open():
    p = LLMResponseParser(asset_whitelist=WHITELIST)
    proposals = p.parse(_make_response(), {"tickers": [{"symbol":"BTCUSDT","price":"50000"}]}, {}, {})
    assert len(proposals) == 1
    assert proposals[0].action_type == "open"


def test_parser_accepts_close_action():
    p = LLMResponseParser(asset_whitelist=WHITELIST)
    proposals = p.parse(_make_response(action_type="close"), {"tickers":[{"symbol":"BTCUSDT","price":"50000"}]}, {}, {})
    assert proposals[0].action_type == "close"


def test_parser_accepts_reduce_action():
    p = LLMResponseParser(asset_whitelist=WHITELIST)
    proposals = p.parse(_make_response(action_type="reduce"), {"tickers":[{"symbol":"BTCUSDT","price":"50000"}]}, {}, {})
    assert proposals[0].action_type == "reduce"


def test_parser_invalid_action_type_defaults_to_open():
    p = LLMResponseParser(asset_whitelist=WHITELIST)
    proposals = p.parse(_make_response(action_type="explode"), {"tickers":[{"symbol":"BTCUSDT","price":"50000"}]}, {}, {})
    assert proposals[0].action_type == "open"
