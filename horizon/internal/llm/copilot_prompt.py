"""Conversational system prompt for the Co-Pilot mode.

Distinct from prompt_builder.py (which is for autonomous market analysis).
This prompt frames the LLM as a deliberative trading partner.
"""

COPILOT_SYSTEM_PROMPT = """You are Horizon Co-Pilot — a quantitative trading partner.

A human trader is bringing you a feeling, an intuition, or a partially-formed
idea. Your job is to help them think through it using the live market data,
technical indicators, and their current portfolio that I provide each turn.

You are NOT executing trades. You provide analysis. If — and only if — the
discussion converges on a concrete, justified trade, you may attach a
trade_suggestion in your structured response. The human will still review
and submit the order themselves.

Behavior:
- Be direct. Skip platitudes. Cite numbers.
- If the user is vague ("what do you think?"), ask one focused question
  rather than guessing at five interpretations.
- Disagree with the user when the data disagrees. Explain why.
- Consider position concentration: if the user already has 40% in BTC,
  don't ignore that.
- When suggesting a trade, prefer limit orders with reasoned price levels.
- If no trade is justified yet, do NOT fabricate trade_suggestion.

Output format: you MUST respond with valid JSON matching exactly:
{
  "assistant_text": "<your reply to the user, plain text>",
  "trade_suggestion": null   OR   {
    "exchange": "binance|htx|hyperliquid|bitget",
    "symbol": "BTCUSDT",
    "side": "buy|sell",
    "order_type": "market|limit",
    "price": "<string decimal, omit for market>",
    "volume": "<string decimal>",
    "rationale": "<one-paragraph justification>"
  }
}

No prose outside the JSON. No markdown fences.
"""


def _portfolio_total(portfolio) -> str:
    """Return the portfolio's total USDT value as a string, regardless of shape."""
    if portfolio is None:
        return "?"
    # Dict form (legacy / tests): {"total_usdt_value": ...}
    if isinstance(portfolio, dict):
        return str(portfolio.get("total_usdt_value", "?"))
    # Dataclass form (PortfolioSnapshot): .total_usdt_value
    if hasattr(portfolio, "total_usdt_value"):
        return str(getattr(portfolio, "total_usdt_value"))
    return "?"


def _portfolio_balances(portfolio) -> list[dict]:
    """Flatten any PortfolioSnapshot to a list of {asset, exchange, free} dicts."""
    if portfolio is None:
        return []
    if isinstance(portfolio, dict):
        return list(portfolio.get("balances", []) or [])
    if hasattr(portfolio, "exchanges"):
        flat: list[dict] = []
        for exchange_name, balances in (portfolio.exchanges or {}).items():
            for b in balances or []:
                flat.append({
                    "asset": getattr(b, "asset", "?"),
                    "exchange": exchange_name,
                    "free": str(getattr(b, "free", "0")),
                })
        return flat
    return []


def build_context_block(prices: list[dict], indicators: dict, portfolio) -> str:
    """Render the live-context block prepended to each user turn.

    ``portfolio`` may be either a dict (legacy / test shape) or a
    ``PortfolioSnapshot`` dataclass — the helpers above normalize both.
    """
    lines = ["### Live market context", ""]
    if prices:
        lines.append("Prices:")
        for t in prices[:8]:
            lines.append(f"  - {t.get('symbol','?')} @ {t.get('price','?')} ({t.get('exchange','')})")
    if indicators:
        lines.append("")
        lines.append("Technical indicators:")
        for sym, ind in list(indicators.items())[:6]:
            lines.append(
                f"  - {sym}: RSI={ind.get('rsi_14','-')}, "
                f"MACD={ind.get('macd_line','-')}/{ind.get('macd_signal','-')}, "
                f"EMA20={ind.get('ema_20','-')}, EMA50={ind.get('ema_50','-')}"
            )
    if portfolio:
        lines.append("")
        lines.append(f"Portfolio total: {_portfolio_total(portfolio)} USDT")
        balances = _portfolio_balances(portfolio)
        if balances:
            lines.append("Holdings:")
            for b in balances[:10]:
                try:
                    if float(b.get("free", "0") or 0) > 0:
                        lines.append(f"  - {b.get('asset','?')} on {b.get('exchange','?')}: free={b.get('free','?')}")
                except (TypeError, ValueError):
                    continue
    return "\n".join(lines)
