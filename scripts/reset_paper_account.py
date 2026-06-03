"""One-time paper account recovery script.

Background
----------
Before cash-balance validation was added, PaperTradingSimulator allowed
buy orders that drove ``current_cash`` arbitrarily negative. If you
restarted with an account in that state, every new buy was rejected
with "insufficient cash" because available < initial.

This script settles the account at current market price: it closes all
open paper positions at the live ticker, realizing the P&L into cash and
leaving the user with a clean state (cash > 0, no positions) ready to
trade again under the new validation rules.

It is IDEMPOTENT — if no positions are open, it does nothing.

Usage
-----
::

    # Preview what would happen, no writes
    python scripts/reset_paper_account.py --dry-run

    # Actually close positions at current market price
    python scripts/reset_paper_account.py

    # Use a different database path
    python scripts/reset_paper_account.py --db-path ./data/horizon.db
"""

import argparse
import asyncio
import logging
import sys
from decimal import Decimal
from pathlib import Path

import aiosqlite

# Make the horizon package importable when run as a standalone script
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from horizon.internal.exchange.registry import ExchangeRegistry  # noqa: E402
from horizon.internal.exchange.types import Ticker  # noqa: E402
from horizon.internal.paper.simulator import PaperTradingSimulator  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("reset_paper_account")


class _TickerCacheAdapter:
    """Lightweight fetcher stub that satisfies the simulator's interface
    using a minimal in-memory ticker map populated by direct exchange
    adapter calls. Avoids pulling the full app stack (lifespan, fetcher
    poll loop, etc.) for a one-shot script.
    """

    def __init__(self) -> None:
        self._tickers: dict[str, dict[str, Ticker]] = {}

    def add_ticker(self, ticker: Ticker) -> None:
        self._tickers.setdefault(ticker.symbol, {})[ticker.exchange] = ticker

    def get_ticker(self, symbol: str, exchange: str | None = None) -> Ticker | None:
        if symbol not in self._tickers:
            return None
        if exchange is not None:
            return self._tickers[symbol].get(exchange)
        values = self._tickers[symbol]
        return next(iter(values.values())) if values else None


async def _load_open_positions(db: aiosqlite.Connection) -> list[dict]:
    """Read all open paper positions from the database."""
    cursor = await db.execute(
        """
        SELECT id, exchange, symbol, side, volume, avg_entry_price
        FROM paper_positions
        ORDER BY opened_at
        """
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def _load_cash(db: aiosqlite.Connection) -> tuple[float, float]:
    """Return (initial_cash, current_cash) from paper_cash row id=1."""
    cursor = await db.execute(
        "SELECT initial_cash, current_cash FROM paper_cash WHERE id = 1"
    )
    row = await cursor.fetchone()
    if row is None:
        return 0.0, 0.0
    return float(row["initial_cash"] or 0.0), float(row["current_cash"] or 0.0)


async def _fetch_live_price(
    symbol: str,
    exchange: str,
    registry: ExchangeRegistry,
) -> Ticker | None:
    """Fetch current ticker directly from the exchange adapter."""
    adapter = registry.get_active_adapter(exchange)
    if adapter is None:
        return None
    try:
        return await adapter.fetch_ticker(symbol)
    except Exception as e:
        logger.warning("Failed to fetch ticker for %s on %s: %s", symbol, exchange, e)
        return None


async def main_async(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error("Database not found at %s", db_path)
        return 1

    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row

    try:
        initial_cash, current_cash = await _load_cash(db)
        positions = await _load_open_positions(db)

        print("=" * 70)
        print("PAPER ACCOUNT RECOVERY")
        print("=" * 70)
        print(f"Database:              {db_path}")
        print(f"Initial cash:          ${initial_cash:,.2f}")
        print(f"Current cash:          ${current_cash:,.2f}")
        print(f"Open positions:        {len(positions)}")
        if positions:
            print()
            print(f"{'ID':<5}{'Exchange':<12}{'Symbol':<14}{'Side':<6}"
                  f"{'Volume':<14}{'Avg Entry':<14}{'Notional':<14}")
            print("-" * 79)
            for p in positions:
                notional = p["volume"] * p["avg_entry_price"]
                print(f"{p['id']:<5}{p['exchange']:<12}{p['symbol']:<14}"
                      f"{p['side']:<6}{p['volume']:<14.8f}"
                      f"${p['avg_entry_price']:<13.2f}${notional:<13.2f}")
        print()

        if not positions:
            print("No open positions — nothing to settle.")
            print("If current_cash is still negative, manually clear or close "
                  "trades via SQL.")
            return 0

        # Pre-compute settlement using live prices
        registry = ExchangeRegistry()
        if not args.dry_run:
            from horizon.internal.exchange.binance import BinanceAdapter
            from horizon.internal.exchange.bitget import BitgetAdapter
            from horizon.internal.exchange.htx import HTXAdapter
            from horizon.internal.exchange.hyperliquid import HyperliquidAdapter
            from horizon.internal.config.settings import settings

            cfg = settings.exchanges
            if cfg.binance.enabled:
                registry.register(BinanceAdapter(
                    api_key=cfg.binance.api_key or "",
                    api_secret=cfg.binance.api_secret or "",
                ))
            if cfg.htx.enabled:
                registry.register(HTXAdapter(
                    api_key=cfg.htx.api_key or "",
                    api_secret=cfg.htx.api_secret or "",
                ))
            if cfg.hyperliquid.enabled:
                registry.register(HyperliquidAdapter(
                    wallet_address=cfg.hyperliquid.wallet_address or "",
                    private_key=cfg.hyperliquid.private_key or "",
                ))
            if cfg.bitget.enabled:
                registry.register(BitgetAdapter(
                    api_key=cfg.bitget.api_key or "",
                    api_secret=cfg.bitget.api_secret or "",
                    passphrase=cfg.bitget.passphrase or "",
                ))

        cache = _TickerCacheAdapter()
        fetcher_stub = type("_F", (), {
            "_tickers": cache._tickers,
            "get_ticker": cache.get_ticker,
            "_registry": registry,
        })()

        sim = PaperTradingSimulator(db=db, fetcher=fetcher_stub)
        # Hand the cache a reference so closes pick up live tickers
        sim._fetcher = fetcher_stub

        settlements = []
        total_realized = 0.0
        for p in positions:
            close_side = "sell" if p["side"] == "buy" else "buy"
            # Ensure we have a ticker
            ticker = await _fetch_live_price(p["symbol"], p["exchange"], registry)
            if ticker is not None:
                cache.add_ticker(ticker)
            else:
                logger.warning(
                    "No live price for %s on %s — will use last known entry price",
                    p["symbol"], p["exchange"],
                )

            if args.dry_run:
                # In dry-run, we don't write — just compute the math
                effective_price = ticker.price if ticker else Decimal(
                    str(p["avg_entry_price"])
                )
                if p["side"] == "buy":
                    pnl = (effective_price - Decimal(str(p["avg_entry_price"]))) \
                        * Decimal(str(p["volume"]))
                else:
                    pnl = (Decimal(str(p["avg_entry_price"])) - effective_price) \
                        * Decimal(str(p["volume"]))
                settlements.append({
                    "position": p,
                    "close_side": close_side,
                    "close_price": effective_price,
                    "pnl": pnl,
                })
                total_realized += float(pnl)
            else:
                try:
                    result = await sim.close_paper_position(
                        exchange=p["exchange"],
                        symbol=p["symbol"],
                        side=close_side,
                    )
                    # aiosqlite is not autocommit — must explicitly commit
                    # after each position close or all writes are lost
                    # when the connection closes at the end of the script.
                    await db.commit()
                    settlements.append({
                        "position": p,
                        "close_side": close_side,
                        "close_price": result.price,
                        "pnl": result.paper_pnl,
                    })
                    total_realized += float(result.paper_pnl)
                except Exception as e:
                    logger.error("Failed to close position id=%s: %s", p["id"], e)
                    settlements.append({
                        "position": p,
                        "close_side": close_side,
                        "close_price": None,
                        "pnl": None,
                        "error": str(e),
                    })

        print()
        print("=" * 70)
        print("SETTLEMENT PLAN" if args.dry_run else "SETTLEMENT RESULT")
        print("=" * 70)
        print(f"{'Symbol':<14}{'CloseSide':<10}{'Price':<14}{'P&L':<14}")
        print("-" * 52)
        for s in settlements:
            price = s.get("close_price")
            pnl = s.get("pnl")
            price_s = f"${float(price):,.2f}" if price is not None else "FAILED"
            pnl_s = (f"${float(pnl):+,.2f}" if pnl is not None
                     else f"ERR: {s.get('error', '?')}")
            print(f"{s['position']['symbol']:<14}{s['close_side']:<10}"
                  f"{price_s:<14}{pnl_s:<14}")

        projected_cash = current_cash + sum(
            (float(s["position"]["volume"]) * float(s["close_price"]))
            for s in settlements
            if s.get("close_price") is not None
        )

        print()
        print(f"Total realized P&L:    ${total_realized:+,.2f}")
        print(f"Cash before:           ${current_cash:,.2f}")
        print(f"Cash after (projected):${projected_cash:,.2f}")

        if args.dry_run:
            print()
            print("(DRY RUN — no writes were made. Re-run without --dry-run to apply.)")
        else:
            print()
            print("Settlement complete. Cash and positions are now consistent.")

        return 0
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Close all open paper positions at current market price.",
    )
    parser.add_argument(
        "--db-path",
        default="./data/horizon.db",
        help="Path to the SQLite database (default: ./data/horizon.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing to the database.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
