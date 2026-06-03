"""Tests for trade statistics computation.

The compute_trade_stats function is a pure function that aggregates
trading metrics for display in the UI stat cards. It must work on
the trade shape produced by /api/paper/trades.
"""

from horizon.internal.paper.stats import compute_trade_stats


class TestComputeTradeStatsEmpty:
    """Empty input must produce safe zero-valued stats."""

    def test_empty_list_returns_zero_total(self):
        stats = compute_trade_stats([])
        assert stats["total_trades"] == 0

    def test_empty_list_returns_zero_win_rate(self):
        stats = compute_trade_stats([])
        assert stats["win_rate_pct"] == 0.0

    def test_empty_list_returns_zero_pnl(self):
        stats = compute_trade_stats([])
        assert stats["total_pnl"] == 0.0

    def test_empty_list_returns_zero_avg_holding_seconds(self):
        stats = compute_trade_stats([])
        assert stats["avg_holding_seconds"] == 0.0

    def test_empty_list_returns_zero_wins_losses(self):
        stats = compute_trade_stats([])
        assert stats["wins"] == 0
        assert stats["losses"] == 0


class TestComputeTradeStatsWinRate:
    """Win rate is wins / closed_trades; ignores open positions."""

    def test_three_wins_out_of_four_closes_yields_75_pct(self):
        trades = [
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 50.0, "closed_by_side": "sell", "closed_at": "2026-01-02T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 25.0, "closed_by_side": "sell", "closed_at": "2026-01-03T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 10.0, "closed_by_side": "sell", "closed_at": "2026-01-04T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": -20.0, "closed_by_side": "sell", "closed_at": "2026-01-05T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
        ]
        stats = compute_trade_stats(trades)
        assert stats["wins"] == 3
        assert stats["losses"] == 1
        assert stats["win_rate_pct"] == 75.0

    def test_open_positions_excluded_from_win_rate(self):
        trades = [
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 0.0, "closed_by_side": None, "closed_at": None,
             "filled_at": "2026-01-01T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 50.0, "closed_by_side": "sell", "closed_at": "2026-01-02T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
        ]
        stats = compute_trade_stats(trades)
        # One open + one closed-and-winning → win rate over closes = 100%
        assert stats["total_trades"] == 2
        assert stats["closed_trades"] == 1
        assert stats["wins"] == 1
        assert stats["win_rate_pct"] == 100.0

    def test_all_losses_yields_zero_win_rate(self):
        trades = [
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": -10.0, "closed_by_side": "sell", "closed_at": "2026-01-02T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": -5.0, "closed_by_side": "sell", "closed_at": "2026-01-03T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
        ]
        stats = compute_trade_stats(trades)
        assert stats["win_rate_pct"] == 0.0
        assert stats["losses"] == 2


class TestComputeTradeStatsPnL:
    """Total P&L sums paper_pnl over all trades (open + closed)."""

    def test_total_pnl_sums_paper_pnl_across_all_trades(self):
        trades = [
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 50.0, "closed_by_side": "sell", "closed_at": "2026-01-02T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": -20.0, "closed_by_side": "sell", "closed_at": "2026-01-03T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 0.0, "closed_by_side": None, "closed_at": None,
             "filled_at": "2026-01-04T00:00:00Z"},
        ]
        stats = compute_trade_stats(trades)
        assert stats["total_pnl"] == 30.0

    def test_realized_pnl_sums_only_closed_trades(self):
        trades = [
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 50.0, "closed_by_side": "sell", "closed_at": "2026-01-02T00:00:00Z",
             "filled_at": "2026-01-01T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 0.0, "closed_by_side": None, "closed_at": None,
             "filled_at": "2026-01-04T00:00:00Z"},
        ]
        stats = compute_trade_stats(trades)
        assert stats["realized_pnl"] == 50.0
        assert stats["unrealized_pnl"] == 0.0


class TestComputeTradeStatsAvgHolding:
    """Average holding time is computed from filled_at to closed_at over closed trades."""

    def test_avg_holding_time_for_two_trades(self):
        # Trade 1: held 1 day = 86400s
        # Trade 2: held 3 days = 259200s
        # Average = (86400 + 259200) / 2 = 172800s
        trades = [
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 10.0, "closed_by_side": "sell",
             "filled_at": "2026-01-01T00:00:00Z",
             "closed_at": "2026-01-02T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 20.0, "closed_by_side": "sell",
             "filled_at": "2026-01-01T00:00:00Z",
             "closed_at": "2026-01-04T00:00:00Z"},
        ]
        stats = compute_trade_stats(trades)
        assert stats["avg_holding_seconds"] == 172800.0

    def test_open_trades_excluded_from_avg_holding(self):
        trades = [
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 10.0, "closed_by_side": "sell",
             "filled_at": "2026-01-01T00:00:00Z",
             "closed_at": "2026-01-02T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 0.0, "closed_by_side": None,
             "filled_at": "2026-01-04T00:00:00Z",
             "closed_at": None},
        ]
        stats = compute_trade_stats(trades)
        # Only one closed trade with 1 day holding = 86400s
        assert stats["avg_holding_seconds"] == 86400.0


class TestComputeTradeStatsAvgPnL:
    """Average P&L is computed over closed trades only."""

    def test_avg_pnl_over_closed_trades(self):
        # Closed P&L: 100, -50, 25 → avg = 25
        # Open P&L: 0 (excluded)
        trades = [
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 100.0, "closed_by_side": "sell",
             "filled_at": "2026-01-01T00:00:00Z",
             "closed_at": "2026-01-02T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": -50.0, "closed_by_side": "sell",
             "filled_at": "2026-01-01T00:00:00Z",
             "closed_at": "2026-01-03T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 25.0, "closed_by_side": "sell",
             "filled_at": "2026-01-01T00:00:00Z",
             "closed_at": "2026-01-04T00:00:00Z"},
            {"side": "buy", "price": 100.0, "volume": 1.0,
             "paper_pnl": 999.0, "closed_by_side": None,
             "filled_at": "2026-01-05T00:00:00Z",
             "closed_at": None},
        ]
        stats = compute_trade_stats(trades)
        assert stats["avg_pnl"] == 25.0
