"""Horizon Trading Platform - Main Entry Point.

Wires all components together with correct startup/shutdown sequence.
"""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncGenerator

import aiosqlite
import uvicorn
from fastapi import FastAPI

from horizon.internal.config.settings import settings
from horizon.internal.database.db import close_db, init_db
from horizon.internal.exchange.binance import BinanceAdapter
from horizon.internal.exchange.bitget import BitgetAdapter
from horizon.internal.exchange.hyperliquid import HyperliquidAdapter
from horizon.internal.exchange.htx import HTXAdapter
from horizon.internal.exchange.registry import ExchangeRegistry
from horizon.internal.guardrails.cooldown_tracker import CooldownTracker
from horizon.internal.guardrails.evaluator import RiskGuardrailEvaluator
from horizon.internal.guardrails.rules import StrategyConfig
from horizon.internal.indicators.calculator import TechnicalIndicatorCalculator
from horizon.internal.llm.client import (
    LLMCredentialsError,
    build_anthropic_client,
    load_llm_credentials,
)
from horizon.internal.llm.engine import LLMStrategyEngine
from horizon.internal.llm.scheduler import LLMScheduler
from horizon.internal.marketdata.fetcher import MarketDataFetcher
from horizon.internal.ordermanager.manager import OrderManager
from horizon.internal.pairlist import PairListRegistry, SymbolCache
from horizon.internal.pairlist.base import PairList
from horizon.internal.paper.simulator import PaperTradingSimulator
from horizon.internal.portfolio.tracker import PortfolioTracker
from horizon.internal.proposals.queue import ProposalQueue


def _setup_logging() -> None:
    """Configure Python logging with structured format."""
    log_level = getattr(logging, settings.app.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def _create_minimal_app() -> FastAPI:
    """Create a minimal FastAPI app when web server module is not available."""
    application = FastAPI(
        title="Horizon Trading Platform",
        description="Quantitative Cryptocurrency Trading System",
        version="0.1.0",
    )

    @application.get("/api/health")
    async def health_check() -> dict[str, Any]:
        """Health check endpoint."""
        return {"status": "ok", "timestamp": int(time.time())}

    @application.get("/api/portfolio")
    async def get_portfolio() -> dict[str, Any]:
        """Get current portfolio snapshot (placeholder when web server unavailable)."""
        return {
            "exchanges": {},
            "total_usdt_value": "0",
            "timestamp_ms": 0,
        }

    @application.get("/api/orders")
    async def get_orders() -> dict[str, Any]:
        """Get open orders (placeholder when web server unavailable)."""
        return []

    @application.get("/api/market-data/{symbol}")
    async def get_market_data(symbol: str) -> dict[str, Any]:
        """Get market data for a symbol (placeholder when web server unavailable)."""
        return {
            "symbol": symbol,
            "tickers": [],
        }

    @application.get("/api/market-data/stream")
    async def market_data_stream() -> dict[str, Any]:
        """SSE endpoint placeholder."""
        return {"message": "SSE not available in minimal mode"}

    @application.post("/api/orders")
    async def submit_order() -> dict[str, Any]:
        """Submit order placeholder."""
        return {"error": "Order submission not available in minimal mode"}

    return application


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager.

    Handles startup and shutdown events for the Horizon Trading Platform.
    """
    logger = logging.getLogger(__name__)

    # Track background tasks for clean shutdown
    background_tasks: list[asyncio.Task] = []

    # ---- Startup ----
    logger.info("Starting Horizon server...")

    # 1. Database initialization with migrations
    migrations_dir = os.path.join(os.path.dirname(__file__), "internal", "database", "migrations")
    db = await init_db(settings.database.db_path, migrations_dir)
    logger.info("Database initialized at %s", settings.database.db_path)

    # 2. Exchange registry setup
    registry = ExchangeRegistry()

    # Register Binance if enabled
    binance_config = settings.exchanges.binance
    if binance_config.enabled:
        adapter = BinanceAdapter(
            api_key=binance_config.api_key or "",
            api_secret=binance_config.api_secret or "",
            recv_window_ms=binance_config.recv_window_ms,
        )
        registry.register(adapter)
        logger.info("Binance adapter registered (enabled=%s)", adapter.enabled)

    # Register HTX if enabled
    htx_config = settings.exchanges.htx
    if htx_config.enabled:
        adapter = HTXAdapter(
            api_key=htx_config.api_key or "",
            api_secret=htx_config.api_secret or "",
            recv_window_ms=htx_config.recv_window_ms,
        )
        registry.register(adapter)
        logger.info("HTX adapter registered (enabled=%s)", adapter.enabled)

    # Register Hyperliquid if enabled
    hyperliquid_config = settings.exchanges.hyperliquid
    if hyperliquid_config.enabled:
        adapter = HyperliquidAdapter(
            wallet_address=hyperliquid_config.wallet_address or "",
            private_key=hyperliquid_config.private_key or "",
        )
        registry.register(adapter)
        logger.info("Hyperliquid adapter registered (enabled=%s)", adapter.enabled)

    # Register Bitget if enabled
    bitget_config = settings.exchanges.bitget
    if bitget_config.enabled:
        adapter = BitgetAdapter(
            api_key=bitget_config.api_key or "",
            api_secret=bitget_config.api_secret or "",
            passphrase=bitget_config.passphrase or "",
        )
        registry.register(adapter)
        logger.info("Bitget adapter registered (enabled=%s)", adapter.enabled)

    # 2a. Initialize PairList registry and discover plugins
    pairlist_registry = PairListRegistry()
    pairlist_registry.discover("horizon.pairlists")
    logger.info("PairList plugins discovered: %s", [p["name"] for p in pairlist_registry.list_all()])

    # 2b. Initialize symbol cache
    symbol_cache = SymbolCache(db)

    # 2c. Set active exchange from settings (single exchange mode)
    active_exchange = settings.exchanges.active_exchange
    logger.info("Active exchange: %s", active_exchange)

    # 2d. Refresh symbol cache from active exchange on startup
    adapter = registry.get(active_exchange)
    if adapter and adapter.enabled:
        try:
            await asyncio.wait_for(
                symbol_cache.refresh_active_exchange(adapter),
                timeout=10.0,
            )
            logger.info("Symbol cache refreshed from active exchange: %s", active_exchange)
        except asyncio.TimeoutError:
            logger.warning("Symbol cache refresh timed out for %s", active_exchange)
        except Exception as e:
            logger.warning("Symbol cache refresh failed for %s: %s", active_exchange, e)
    else:
        logger.warning("Active exchange '%s' not found or not enabled", active_exchange)
    logger.info("Symbol cache populated from active exchange: %s", active_exchange)

    # 2e. Set default active PairList (first discovered)
    available = pairlist_registry.list_all()
    if available:
        default_pairlist_name = available[0]["name"]
        active_pairlist = pairlist_registry.create(default_pairlist_name)
        logger.info("Default PairList: %s", default_pairlist_name)
    else:
        active_pairlist = None
        logger.warning("No PairList plugins found!")

    # 3. Market data fetcher setup
    fetcher = MarketDataFetcher(
        registry=registry,
        db=db,
        active_pairlist=active_pairlist,
        active_exchange=active_exchange,
        poll_interval_seconds=settings.trading.market_data_poll_interval_seconds,
    )

    # 4. Portfolio tracker setup
    portfolio_tracker = PortfolioTracker(
        registry=registry,
        db=db,
        fetcher=fetcher,
        active_exchange=active_exchange,
        snapshot_interval_seconds=settings.trading.portfolio_snapshot_interval_seconds,
    )

    # 5. Load strategy config from database
    strategy_config = await _load_strategy_config(db)
    if strategy_config is None:
        logger.warning("No strategy config found in database, using defaults")
        strategy_config = _get_default_strategy_config()

    # 6. CooldownTracker setup
    cooldown_tracker = CooldownTracker(db)
    logger.info("CooldownTracker initialized")

    # 7. RiskGuardrailEvaluator setup
    guardrail_evaluator = RiskGuardrailEvaluator(
        db=db,
        cooldown_tracker=cooldown_tracker,
        strategy_config=strategy_config,
        fetcher=fetcher,
    )
    logger.info("RiskGuardrailEvaluator initialized")

    # 8. PaperTradingSimulator setup
    paper_simulator = PaperTradingSimulator(
        db=db,
        fetcher=fetcher,
        initial_cash_usdt=settings.trading.initial_cash_usdt,
    )
    await paper_simulator.init_cash()
    logger.info(
        "PaperTradingSimulator initialized with initial cash %.2f USDT",
        settings.trading.initial_cash_usdt,
    )

    # 9. OrderManager setup with guardrail_evaluator
    order_manager = OrderManager(
        registry=registry,
        db=db,
        active_exchange=active_exchange,
        guardrail_evaluator=guardrail_evaluator,
        cooldown_tracker=cooldown_tracker,
        strategy_config=strategy_config,
    )
    logger.info("OrderManager initialized with guardrail_evaluator")

    # 10. ProposalQueue setup with guardrail_evaluator and paper_simulator
    proposal_queue = ProposalQueue(
        db=db,
        order_manager=order_manager,
        strategy_config=strategy_config,
        guardrail_evaluator=guardrail_evaluator,
        paper_simulator=paper_simulator,
    )
    proposal_queue.set_registry(registry)
    logger.info("ProposalQueue initialized with guardrail_evaluator and paper_simulator")

    # 10a. LLM stack: client + engine + scheduler (best-effort — server still runs if LLM unavailable)
    anthropic_client = None
    llm_engine = None
    llm_scheduler = None
    indicator_calculator = TechnicalIndicatorCalculator(db)
    try:
        anthropic_client = build_anthropic_client()
        logger.info("Anthropic client built successfully")
    except LLMCredentialsError as e:
        logger.warning("LLM disabled — credentials unavailable: %s", e)

    if anthropic_client is not None:
        # Build a dict version of the strategy config row for the engine
        cursor = await db.execute("SELECT * FROM strategy_configs WHERE enabled = 1 LIMIT 1")
        sc_row = await cursor.fetchone()
        strategy_config_dict = dict(sc_row) if sc_row else {}
        llm_engine = LLMStrategyEngine(
            db=db,
            registry=registry,
            proposal_queue=proposal_queue,
            anthropic_client=anthropic_client,
            strategy_config=strategy_config_dict,
            indicator_calculator=indicator_calculator,
            fetcher=fetcher,
        )
        llm_scheduler = LLMScheduler(llm_engine, interval_seconds=8 * 3600, run_on_start=False)
        logger.info("LLMStrategyEngine + LLMScheduler initialized")

    # Store components in app state
    application.state.db = db
    application.state.registry = registry
    application.state.fetcher = fetcher
    application.state.order_manager = order_manager
    application.state.portfolio_tracker = portfolio_tracker
    application.state.pairlist_registry = pairlist_registry
    application.state.active_pairlist = active_pairlist
    application.state.active_exchange = active_exchange
    application.state.symbol_cache = symbol_cache
    application.state.cooldown_tracker = cooldown_tracker
    application.state.guardrail_evaluator = guardrail_evaluator
    application.state.paper_simulator = paper_simulator
    application.state.proposal_queue = proposal_queue
    application.state.llm_engine = llm_engine
    application.state.llm_scheduler = llm_scheduler
    application.state.indicator_calculator = indicator_calculator

    # 11. Start background tasks

    # Start market data fetcher
    await fetcher.start()

    # Start portfolio tracker
    await portfolio_tracker.start()

    # Start order manager sync loop
    await order_manager.start_sync_loop()

    # Start auto-execution scanner
    await proposal_queue.start_auto_execution_scanner()

    # Start proposal expiry scanner
    await proposal_queue.start_expiry_scanner()
    logger.info("Proposal expiry scanner started")

    # Start LLM scheduler (8h periodic analysis) if available
    if llm_scheduler is not None:
        await llm_scheduler.start()
        logger.info("LLM scheduler started (interval 8h)")

    # Start paper_simulator.update_market_prices() loop (every 30 seconds)
    async def paper_price_update_loop() -> None:
        while True:
            try:
                await asyncio.sleep(30)
                await paper_simulator.update_market_prices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in paper price update loop: {e}")

    paper_price_task = asyncio.create_task(paper_price_update_loop())
    background_tasks.append(paper_price_task)

    # Start cooldown_tracker.cleanup_expired() every 5 minutes
    async def cooldown_cleanup_loop() -> None:
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                await cooldown_tracker.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cooldown cleanup loop: {e}")

    cooldown_cleanup_task = asyncio.create_task(cooldown_cleanup_loop())
    background_tasks.append(cooldown_cleanup_task)

    logger.info("All background tasks started")

    logger.info("Horizon server started on %s:%s", settings.app.host, settings.app.port)

    yield

    # ---- Shutdown ----
    logger.info("Stopping Horizon server...")

    # Cancel all background tasks
    for task in background_tasks:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    logger.info("Background tasks cancelled")

    # Stop auto-execution scanner
    await proposal_queue.stop_auto_execution_scanner()
    logger.info("Proposal queue auto-execution scanner stopped")

    # Stop LLM scheduler if running
    if llm_scheduler is not None:
        await llm_scheduler.stop()
        logger.info("LLM scheduler stopped")

    # Stop proposal expiry scanner
    await proposal_queue.stop_expiry_scanner()
    logger.info("Proposal expiry scanner stopped")

    # Stop fetcher
    await fetcher.stop()
    logger.info("Market data fetcher stopped")

    # Stop portfolio tracker
    await portfolio_tracker.stop()
    logger.info("Portfolio tracker stopped")

    # Stop order manager
    await order_manager.stop()
    logger.info("Order manager stopped")

    # Close database
    await close_db(db)
    logger.info("Database connection closed")

    logger.info("Horizon server stopped")


async def _load_strategy_config(db: aiosqlite.Connection) -> StrategyConfig | None:
    """Load the active strategy config from the database.

    Args:
        db: Async SQLite database connection.

    Returns:
        StrategyConfig instance or None if not found.
    """
    cursor = await db.execute(
        "SELECT * FROM strategy_configs WHERE enabled = 1 LIMIT 1"
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    # Parse asset_whitelist from JSON string if needed
    asset_whitelist = row["asset_whitelist"]
    if isinstance(asset_whitelist, str):
        import json
        try:
            asset_whitelist = json.loads(asset_whitelist)
        except json.JSONDecodeError:
            asset_whitelist = []

    return StrategyConfig(
        mode=row["mode"] if row["mode"] else "paper",
        asset_whitelist=asset_whitelist,
        order_min_notional=Decimal(str(row["order_min_notional"])) if row["order_min_notional"] else Decimal("10"),
        order_max_notional=Decimal(str(row["order_max_notional"])) if row["order_max_notional"] else Decimal("1000000"),
        max_exchange_exposure_pct=row["max_exchange_exposure_pct"] if row["max_exchange_exposure_pct"] else 0.5,
        max_position_pct=row["max_position_pct"] / 100.0 if row["max_position_pct"] else 0.3,
        cooldown_seconds=row["cooldown_seconds"] if row["cooldown_seconds"] else 300,
        max_daily_loss_pct=row["max_daily_loss_pct"] / 100.0 if row["max_daily_loss_pct"] else 0.05,
    )


def _get_default_strategy_config() -> StrategyConfig:
    """Get default strategy config when none is found in database.

    Returns:
        StrategyConfig with default values.
    """
    return StrategyConfig(
        asset_whitelist=[],
        order_min_notional=Decimal("10"),
        order_max_notional=Decimal("1000000"),
        max_exchange_exposure_pct=0.5,
        max_position_pct=0.3,
        cooldown_seconds=300,
        max_daily_loss_pct=0.05,
    )


def _create_app() -> FastAPI:
    """Create the FastAPI application with lifespan.

    Attempts to import create_app from internal.web.server, falls back to minimal app.

    Returns:
        Configured FastAPI application instance.
    """
    # Try to use the full web server create_app
    try:
        from horizon.internal.web.server import create_app as _create_app_func

        # Create database directory if needed
        db_path = settings.database.db_path
        parent_dir = os.path.dirname(db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # Initialize database synchronously with migrations
        migrations_dir = os.path.join(os.path.dirname(__file__), "internal", "database", "migrations")
        db = asyncio.run(init_db(db_path, migrations_dir))

        # Create registry and register adapters
        registry = ExchangeRegistry()

        # Register adapters
        binance_config = settings.exchanges.binance
        if binance_config.enabled:
            adapter = BinanceAdapter(
                api_key=binance_config.api_key or "",
                api_secret=binance_config.api_secret or "",
                recv_window_ms=binance_config.recv_window_ms,
            )
            registry.register(adapter)

        htx_config = settings.exchanges.htx
        if htx_config.enabled:
            adapter = HTXAdapter(
                api_key=htx_config.api_key or "",
                api_secret=htx_config.api_secret or "",
                recv_window_ms=htx_config.recv_window_ms,
            )
            registry.register(adapter)

        hyperliquid_config = settings.exchanges.hyperliquid
        if hyperliquid_config.enabled:
            adapter = HyperliquidAdapter(
                wallet_address=hyperliquid_config.wallet_address or "",
                private_key=hyperliquid_config.private_key or "",
            )
            registry.register(adapter)

        bitget_config = settings.exchanges.bitget
        if bitget_config.enabled:
            adapter = BitgetAdapter(
                api_key=bitget_config.api_key or "",
                api_secret=bitget_config.api_secret or "",
                passphrase=bitget_config.passphrase or "",
            )
            registry.register(adapter)

        # Create components
        pairlist_registry = PairListRegistry()
        pairlist_registry.discover("horizon.pairlists")
        symbol_cache = SymbolCache(db)
        active_exchange = settings.exchanges.active_exchange

        available = pairlist_registry.list_all()
        active_pairlist = pairlist_registry.create(available[0]["name"]) if available else None

        fetcher = MarketDataFetcher(
            registry=registry,
            db=db,
            active_pairlist=active_pairlist,
            active_exchange=active_exchange,
            poll_interval_seconds=settings.trading.market_data_poll_interval_seconds,
        )

        # NOTE: Do NOT instantiate PortfolioTracker here. The lifespan() below
        # is the canonical owner: it creates the tracker, calls start() to load
        # balances, and stores it in app.state.portfolio_tracker. Endpoints
        # read from app.state so they always see the running instance with
        # fresh data. Creating a second instance here would result in the
        # API endpoint seeing a tracker with no balances ever loaded.

        order_manager = OrderManager(
            registry=registry,
            db=db,
            active_exchange=active_exchange,
        )

        # Call the web server's create_app - this returns an app with all endpoints
        application = _create_app_func(
            settings=settings,
            db=db,
            registry=registry,
            fetcher=fetcher,
            order_manager=order_manager,
            portfolio_tracker=None,  # Created and started by lifespan()
        )

        # Override lifespan with our custom one for startup/shutdown
        application.router.lifespan_context = lifespan

        return application

    except (ImportError, Exception) as e:
        logging.warning(f"Could not load full web server, using minimal app: {e}")
        import traceback
        traceback.print_exc()
        # Fall back to minimal app
        application = _create_minimal_app()
        application.router.lifespan_context = lifespan
        return application


# Create module-level app instance
app = _create_app()


def main() -> None:
    """Run the Horizon Trading Platform server."""
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Launching Horizon Trading Platform...")

    uvicorn.run(
        "horizon.main:app",
        host=settings.app.host,
        port=settings.app.port,
        log_level=settings.app.log_level.lower(),
        reload=False,
        # Bound the time uvicorn waits for in-flight keep-alive connections
        # to drain on Ctrl+C. Without this, uvicorn's `Server.shutdown()`
        # awaits `_wait_tasks_to_complete()` with timeout=None — any active
        # HTTP client (SSE subscribers, the frontend dashboard's polling
        # loop) holds a keep-alive connection open, the wait never finishes,
        # the lifespan's post-yield shutdown code is never reached, and the
        # process never exits. 5s is long enough for one in-flight request
        # to complete, short enough to feel responsive.
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()