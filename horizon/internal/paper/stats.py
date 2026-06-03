"""Pure-function trade statistics for UI display.

Mirrors the trade shape produced by ``/api/paper/trades``:
    {side, price, volume, paper_pnl, closed_by_side, closed_at, filled_at}
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _parse_ts(value: str | None) -> datetime | None:
    """Parse ISO-8601 timestamp, accepting trailing 'Z'."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compute_trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate closed-and-open trade metrics for stat cards.

    Returns a dict with the keys:
        total_trades, closed_trades, wins, losses, win_rate_pct,
        total_pnl, realized_pnl, unrealized_pnl,
        avg_pnl, avg_holding_seconds.
    """
    total = len(trades)
    if total == 0:
        return {
            "total_trades": 0,
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "total_pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "avg_pnl": 0.0,
            "avg_holding_seconds": 0.0,
        }

    closed = [t for t in trades if t.get("closed_by_side")]
    open_trades = [t for t in trades if not t.get("closed_by_side")]

    wins = sum(1 for t in closed if (t.get("paper_pnl") or 0) > 0)
    losses = sum(1 for t in closed if (t.get("paper_pnl") or 0) <= 0)
    closed_count = len(closed)
    win_rate_pct = (wins / closed_count * 100.0) if closed_count else 0.0

    total_pnl = sum((t.get("paper_pnl") or 0) for t in trades)
    realized_pnl = sum((t.get("paper_pnl") or 0) for t in closed)
    unrealized_pnl = sum((t.get("paper_pnl") or 0) for t in open_trades)

    avg_pnl = (realized_pnl / closed_count) if closed_count else 0.0

    holding_seconds: list[float] = []
    for t in closed:
        filled = _parse_ts(t.get("filled_at"))
        closed_at = _parse_ts(t.get("closed_at"))
        if filled and closed_at:
            holding_seconds.append((closed_at - filled).total_seconds())
    avg_holding_seconds = (
        sum(holding_seconds) / len(holding_seconds) if holding_seconds else 0.0
    )

    return {
        "total_trades": total,
        "closed_trades": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate_pct,
        "total_pnl": total_pnl,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "avg_pnl": avg_pnl,
        "avg_holding_seconds": avg_holding_seconds,
    }
