"""FastAPI Web Server with REST Endpoints for Horizon Trading Platform."""

import asyncio
import json
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Optional

import aiosqlite
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config.settings import Settings
from ..exchange.registry import ExchangeRegistry
from ..guardrails.evaluator import RiskGuardrailEvaluator, SystemMode
from ..guardrails.cooldown_tracker import CooldownTracker
from ..guardrails.rules import StrategyConfig
from ..marketdata.fetcher import MarketDataFetcher
from ..ordermanager.manager import OrderManager, OrderSubmissionError
from ..paper.simulator import PaperTradingSimulator
from ..portfolio.tracker import PortfolioTracker
from ..datasource import KlineCache, DataSourceRegistry
if TYPE_CHECKING:
    from ..pairlist.base import PairList
else:
    from ..pairlist.registry import PairListRegistry
    from ..pairlist.cache import SymbolCache
    from ..datasource.cryptocompare import CryptoCompareAdapter


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that converts Decimal to string."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def dumps_safe(obj: Any) -> str:
    """Serialize object to JSON string, handling Decimal conversion."""
    return json.dumps(obj, cls=DecimalEncoder)


class CustomJSONResponse(JSONResponse):
    """Custom JSON response that handles Decimal serialization."""

    def __init__(self, content: Any, **kwargs: Any) -> None:
        # Pre-serialize to handle Decimals
        serialized = json.loads(dumps_safe(content))
        super().__init__(content=serialized, **kwargs)


# Pydantic models for request validation
class OrderRequestModel(BaseModel):
    """Request model for order submission."""

    exchange: str = Field(..., description="Exchange name")
    symbol: str = Field(..., description="Trading symbol")
    side: Literal["buy", "sell"] = Field(..., description="Order side")
    type: Literal["market", "limit"] = Field(..., description="Order type")
    price: Optional[float] = Field(None, description="Limit price (required for limit orders)")
    volume: float = Field(..., description="Order volume")


class ActiveExchangeModel(BaseModel):
    """Request model for setting active exchange."""

    exchange: str = Field(..., description="Exchange name to set as active")


class ActivePairListModel(BaseModel):
    """Request model for setting active PairList."""

    name: str = Field(..., description="Name of the PairList to activate")


def create_app(
    settings: Settings,
    db: aiosqlite.Connection,
    registry: ExchangeRegistry,
    fetcher: MarketDataFetcher,
    order_manager: OrderManager,
    portfolio_tracker: PortfolioTracker,
    guardrail_evaluator: RiskGuardrailEvaluator | None = None,
    cooldown_tracker: CooldownTracker | None = None,
    paper_simulator: PaperTradingSimulator | None = None,
    strategy_config: StrategyConfig | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Application settings.
        db: SQLite database connection.
        registry: Exchange registry.
        fetcher: Market data fetcher.
        order_manager: Order manager.
        portfolio_tracker: Portfolio tracker.
        guardrail_evaluator: Optional guardrail evaluator.
        cooldown_tracker: Optional cooldown tracker.
        paper_simulator: Optional paper trading simulator.
        strategy_config: Optional strategy config.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="Horizon Trading Platform",
        version="1.0.0",
        description="Quantitative Cryptocurrency Trading System",
    )

    # Add CORS middleware for local development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store components in app state
    app.state.settings = settings
    app.state.db = db
    app.state.registry = registry
    app.state.fetcher = fetcher
    app.state.order_manager = order_manager
    app.state.portfolio_tracker = portfolio_tracker
    app.state.guardrail_evaluator = guardrail_evaluator
    app.state.cooldown_tracker = cooldown_tracker
    app.state.paper_simulator = paper_simulator
    app.state.strategy_config = strategy_config

    # Initialize kline cache and datasource
    kline_cache = KlineCache(
        cache_file=settings.datasource.cache_file,
        stale_seconds=settings.datasource.cache_stale_seconds,
    )
    kline_cache.load()

    cc_adapter = CryptoCompareAdapter(
        api_key=settings.datasource.cryptocompare_api_key,
        base_url=settings.datasource.cryptocompare_base_url,
    )

    datasource_registry = DataSourceRegistry(
        cache=kline_cache,
        cryptocompare_adapter=cc_adapter,
    )

    app.state.kline_cache = kline_cache
    app.state.datasource_registry = datasource_registry

    # Mount static files
    app.mount("/static", StaticFiles(directory="horizon/internal/web/static"), name="static")

    # Health check endpoint
    @app.get("/api/health")
    async def health() -> JSONResponse:
        """Health check endpoint."""
        return CustomJSONResponse(content={
            "status": "ok",
            "timestamp": int(time.time()),
        })

    # Exchange endpoints
    @app.get("/api/exchanges")
    async def list_exchanges(request: Request) -> JSONResponse:
        """List all enabled exchanges and their active status."""
        registry: ExchangeRegistry = request.app.state.registry
        settings: Settings = request.app.state.settings
        active_exchange = settings.exchanges.active_exchange
        result = []
        for adapter in registry.list_all():
            result.append({
                "name": adapter.name,
                "enabled": adapter.enabled,
                "active": adapter.name == active_exchange,
            })
        return CustomJSONResponse(content={"exchanges": result})

    @app.post("/api/exchanges/active")
    async def set_active_exchange(request: Request, body: ActiveExchangeModel) -> JSONResponse:
        """Set which exchange is active for all operations."""
        registry: ExchangeRegistry = request.app.state.registry
        enabled_names = {a.name for a in registry.list_enabled() if a.enabled}

        if body.exchange not in enabled_names:
            raise HTTPException(
                status_code=400,
                detail=f"Exchange '{body.exchange}' not enabled. Enabled: {sorted(enabled_names)}"
            )

        # Update settings and all components
        settings = request.app.state.settings
        settings.exchanges.active_exchange = body.exchange

        # Update fetcher
        fetcher = request.app.state.fetcher
        if hasattr(fetcher, '_active_exchange'):
            fetcher._active_exchange = body.exchange

        # Update portfolio tracker
        portfolio = request.app.state.portfolio_tracker
        if hasattr(portfolio, '_active_exchange'):
            portfolio._active_exchange = body.exchange

        # Update order manager
        order_mgr = request.app.state.order_manager
        if hasattr(order_mgr, '_active_exchange'):
            order_mgr._active_exchange = body.exchange

        # Trigger symbol cache refresh from new active exchange
        cache = request.app.state.symbol_cache
        adapter = registry.get(body.exchange)
        if adapter:
            asyncio.create_task(cache.refresh_active_exchange(adapter))

        return CustomJSONResponse(content={
            "active_exchange": body.exchange,
        })

    # PairList endpoints
    @app.get("/api/pairlists")
    async def list_pairlists(request: Request) -> JSONResponse:
        """List all available PairLists."""
        registry: PairListRegistry = request.app.state.pairlist_registry
        pairlists = registry.list_all()
        return CustomJSONResponse(content={"pairlists": pairlists})

    @app.get("/api/pairlists/active")
    async def get_active_pairlist(request: Request) -> JSONResponse:
        """Get the currently active PairList and its symbols."""
        pairlist: PairList = request.app.state.active_pairlist
        db: aiosqlite.Connection = request.app.state.db

        if pairlist is None:
            raise HTTPException(status_code=404, detail="No active PairList set")

        symbols = await pairlist.get_pairs(db)
        return CustomJSONResponse(content={
            "name": pairlist.name,
            "symbol_count": len(symbols),
            "symbols": symbols,
        })

    @app.post("/api/pairlists/active")
    async def set_active_pairlist(request: Request, body: ActivePairListModel) -> JSONResponse:
        """Set the active PairList by name."""
        registry: PairListRegistry = request.app.state.pairlist_registry
        db: aiosqlite.Connection = request.app.state.db

        if not registry.exists(body.name):
            available = [pl["name"] for pl in registry.list_all()]
            raise HTTPException(
                status_code=404,
                detail=f"PairList '{body.name}' not found. Available: {available}"
            )

        pairlist = registry.create(body.name)
        symbols = await pairlist.get_pairs(db)
        request.app.state.active_pairlist = pairlist

        return CustomJSONResponse(content={
            "name": pairlist.name,
            "symbol_count": len(symbols),
            "symbols": symbols,
        })

    @app.post("/api/pairlists/refresh-cache")
    async def refresh_symbol_cache(request: Request) -> JSONResponse:
        """Force-refresh the exchange_symbols cache from active exchange."""
        registry: ExchangeRegistry = request.app.state.registry
        settings: Settings = request.app.state.settings
        active_exchange = settings.exchanges.active_exchange
        cache: SymbolCache = request.app.state.symbol_cache

        async def refresh_task():
            adapter = registry.get(active_exchange)
            if adapter:
                await cache.refresh_for_exchange(adapter)

        asyncio.create_task(refresh_task())

        return CustomJSONResponse(content={
            "status": "started",
            "message": f"Symbol cache refresh initiated for exchange '{active_exchange}'",
            "active_exchange": active_exchange,
        })

    # Portfolio endpoint
    @app.get("/api/portfolio")
    async def get_portfolio() -> JSONResponse:
        """Get current portfolio snapshot."""
        snapshot = await portfolio_tracker.get_snapshot()
        return CustomJSONResponse(content={
            "exchanges": {
                exchange: [
                    {
                        "asset": b.asset,
                        "free": str(b.free),
                        "locked": str(b.locked),
                        "exchange": b.exchange,
                    }
                    for b in balances
                ]
                for exchange, balances in snapshot.exchanges.items()
            },
            "total_usdt_value": str(snapshot.total_usdt_value),
            "timestamp_ms": snapshot.timestamp_ms,
        })

    # Market data endpoint for specific symbol
    @app.get("/api/market-data/{symbol}")
    async def get_market_data(symbol: str) -> JSONResponse:
        """Get all tickers for a symbol across exchanges."""
        tickers = fetcher.get_all_tickers(symbol)
        return CustomJSONResponse(content={
            "symbol": symbol,
            "tickers": [
                {
                    "symbol": t.symbol,
                    "price": str(t.price),
                    "volume_24h": str(t.volume_24h),
                    "exchange": t.exchange,
                    "timestamp_ms": t.timestamp_ms,
                }
                for t in tickers
            ],
        })

    # Kline endpoint for chart data
    @app.get("/api/kline/{symbol}")
    async def get_kline(
        symbol: str,
        timeframe: str = Query("1h", description="Timeframe: 1m, 5m, 15m, 1h, 4h, 1d"),
        limit: int = Query(500, description="Max candles to return"),
        refresh: bool = Query(False, description="Force refresh from API"),
    ) -> JSONResponse:
        """Get historical OHLCV kline/candlestick data for a symbol.

        Uses lazy loading with cache:
        - Returns cached data if fresh (not stale)
        - Fetches from CryptoCompare if stale or refresh=true
        """
        datasource: DataSourceRegistry = app.state.datasource_registry

        try:
            candles = await datasource.get_klines(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                force_refresh=refresh,
            )
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"No kline data for {symbol}: {str(e)}")

        return CustomJSONResponse(content={
            "symbol": symbol,
            "timeframe": timeframe,
            "exchange": "cryptocompare",
            "candles": [
                {
                    "time": c["time"],
                    "open": str(c["open"]),
                    "high": str(c["high"]),
                    "low": str(c["low"]),
                    "close": str(c["close"]),
                    "volume": str(c["volume"]),
                }
                for c in candles
            ],
        })

    # SSE endpoint for market data streaming
    @app.get("/api/market-data/stream")
    async def market_data_stream(request: Request) -> StreamingResponse:
        """Server-Sent Events endpoint for real-time market data."""
        queue = fetcher.subscribe()

        async def event_generator() -> Any:
            try:
                while True:
                    # Wait for data on the queue
                    data = await queue.get()
                    # Format as SSE
                    yield f"data: {dumps_safe(data)}\n\n"
            except asyncio.CancelledError:
                # Clean up on client disconnect
                fetcher.unsubscribe(queue)
                raise

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # Order submission endpoint
    @app.post("/api/orders")
    async def submit_order(order_data: OrderRequestModel) -> JSONResponse:
        """Submit a new order."""
        # Create OrderRequest
        order_request = OrderManager.OrderRequest(
            exchange=order_data.exchange,
            symbol=order_data.symbol,
            side=order_data.side,
            order_type=order_data.type,
            price=Decimal(str(order_data.price)) if order_data.price is not None else None,
            volume=Decimal(str(order_data.volume)),
            source="manual",
        )

        try:
            result = await order_manager.submit_order(order_request)
            return CustomJSONResponse(content={
                "order_id": result.order_id,
                "exchange_order_id": result.exchange_order_id,
                "exchange": result.exchange,
                "symbol": result.symbol,
                "side": result.side,
                "order_type": result.order_type,
                "price": str(result.price) if result.price else None,
                "volume": str(result.volume),
                "filled_volume": str(result.filled_volume),
                "status": result.status,
                "created_at_ms": result.created_at_ms,
                "updated_at_ms": result.updated_at_ms,
            })
        except OrderSubmissionError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Order cancellation endpoint
    @app.delete("/api/orders/{order_id}")
    async def cancel_order(order_id: str) -> JSONResponse:
        """Cancel an open order."""
        success = await order_manager.cancel_order(order_id)
        if success:
            return CustomJSONResponse(content={"status": "cancelled"})
        else:
            raise HTTPException(status_code=404, detail="Order not found or cannot be cancelled")

    # Order listing endpoint
    @app.get("/api/orders")
    async def list_orders(
        exchange: Optional[str] = Query(None, description="Filter by exchange"),
        symbol: Optional[str] = Query(None, description="Filter by symbol"),
        status: Optional[str] = Query(None, description="Filter by status: 'open' or 'history'"),
    ) -> JSONResponse:
        """List orders with optional filters."""
        if status == "open":
            orders = await order_manager.get_open_orders(exchange=exchange, symbol=symbol)
            return CustomJSONResponse(content=[
                {
                    "order_id": o.order_id,
                    "exchange_order_id": o.exchange_order_id,
                    "exchange": o.exchange,
                    "symbol": o.symbol,
                    "side": o.side,
                    "order_type": o.order_type,
                    "price": str(o.price) if o.price else None,
                    "volume": str(o.volume),
                    "filled_volume": str(o.filled_volume),
                    "status": o.status,
                    "created_at_ms": o.created_at_ms,
                    "updated_at_ms": o.updated_at_ms,
                }
                for o in orders
            ])
        else:
            orders = await order_manager.get_order_history(
                exchange=exchange,
                symbol=symbol,
                limit=100,
            )
            # Handle both dict objects and OrderResult objects
            def serialize_order(o):
                if hasattr(o, 'order_id'):  # It's an OrderResult object
                    return {
                        "order_id": o.order_id,
                        "exchange_order_id": o.exchange_order_id,
                        "exchange": o.exchange,
                        "symbol": o.symbol,
                        "side": o.side,
                        "order_type": o.order_type,
                        "price": str(o.price) if o.price else None,
                        "volume": str(o.volume),
                        "filled_volume": str(o.filled_volume),
                        "status": o.status,
                        "created_at_ms": o.created_at_ms,
                        "updated_at_ms": o.updated_at_ms,
                    }
                else:  # It's a dict
                    return {
                        "order_id": o.get("id"),
                        "exchange_order_id": o.get("exchange_order_id"),
                        "exchange": o.get("exchange"),
                        "symbol": o.get("symbol"),
                        "side": o.get("side"),
                        "order_type": o.get("order_type"),
                        "price": str(o.get("price")) if o.get("price") else None,
                        "volume": str(o.get("volume")),
                        "filled_volume": str(o.get("filled_volume", 0)),
                        "status": o.get("status"),
                        "created_at_ms": 0,
                        "updated_at_ms": 0,
                    }
            return CustomJSONResponse(content=[serialize_order(o) for o in orders])

    # ---- Guardrail API Endpoints ----

    @app.get("/api/guardrails/status")
    async def get_guardrails_status(request: Request) -> JSONResponse:
        """Get current guardrail system status.

        Returns mode, cooldowns, downgrade_expires_at, and guardrail_events_today.
        """
        evaluator: RiskGuardrailEvaluator | None = request.app.state.guardrail_evaluator
        cooldown_tracker: CooldownTracker | None = request.app.state.cooldown_tracker
        db: aiosqlite.Connection = request.app.state.db

        # Get current mode
        if evaluator is not None:
            mode = await evaluator.get_current_mode()
            mode_value = mode.value
        else:
            mode_value = "unknown"

        # Get cooldowns
        cooldowns = []
        if cooldown_tracker is not None:
            cooldowns = await cooldown_tracker.get_all_cooldowns()

        # Get today's guardrail events count
        today_count = 0
        if db is not None:
            cursor = await db.execute(
                """
                SELECT COUNT(*) as count FROM guardrail_events
                WHERE DATE(created_at) = DATE('now', 'utc')
                """
            )
            row = await cursor.fetchone()
            today_count = row["count"] if row else 0

        # Get most recent downgrade_expires_at if any
        downgrade_expires_at = None
        if db is not None:
            cursor = await db.execute(
                """
                SELECT downgrade_expires_at FROM guardrail_events
                WHERE downgrade_active = 1
                ORDER BY created_at DESC LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if row and row["downgrade_expires_at"]:
                downgrade_expires_at = row["downgrade_expires_at"]

        return CustomJSONResponse(content={
            "mode": mode_value,
            "cooldowns": cooldowns,
            "downgrade_expires_at": downgrade_expires_at,
            "guardrail_events_today": today_count,
        })

    @app.get("/api/guardrails/events")
    async def get_guardrail_events(
        request: Request,
        limit: int = Query(50, description="Maximum number of events to return"),
        rule_name: str | None = Query(None, description="Filter by rule name"),
    ) -> JSONResponse:
        """Get guardrail events with optional filtering.

        Args:
            limit: Maximum number of events to return (default 50).
            rule_name: Optional rule name filter.
        """
        db: aiosqlite.Connection = request.app.state.db

        query = "SELECT * FROM guardrail_events WHERE 1=1"
        params = []

        if rule_name is not None:
            query += " AND rule_name = ?"
            params.append(rule_name)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        events = []
        for row in rows:
            events.append({
                "id": row["id"],
                "order_id": row["order_id"],
                "proposal_id": row["proposal_id"],
                "rule_name": row["rule_name"],
                "action_taken": row["action_taken"],
                "request_symbol": row["request_symbol"],
                "request_exchange": row["request_exchange"],
                "request_side": row["request_side"],
                "request_volume": row["request_volume"],
                "request_price": row["request_price"],
                "current_value": row["current_value"],
                "threshold_value": row["threshold_value"],
                "downgrade_active": bool(row["downgrade_active"]),
                "downgrade_expires_at": row["downgrade_expires_at"],
                "created_at": row["created_at"],
            })

        return CustomJSONResponse(content={"events": events, "count": len(events)})

    @app.get("/api/guardrails/cooldowns")
    async def get_guardrail_cooldowns(request: Request) -> JSONResponse:
        """Get all active cooldowns with remaining seconds.

        Returns cooldown list with remaining seconds computed.
        """
        cooldown_tracker: CooldownTracker | None = request.app.state.cooldown_tracker

        if cooldown_tracker is None:
            return CustomJSONResponse(content={"cooldowns": []})

        cooldowns = await cooldown_tracker.get_all_cooldowns()
        return CustomJSONResponse(content={"cooldowns": cooldowns})

    # ---- Paper Trading API Endpoints ----

    @app.get("/api/paper/positions")
    async def get_paper_positions(request: Request) -> JSONResponse:
        """Get all paper trading positions with unrealized P&L.

        Returns positions list with unrealized P&L computed.
        """
        simulator: PaperTradingSimulator | None = request.app.state.paper_simulator

        if simulator is None:
            return CustomJSONResponse(content={"positions": []})

        positions = await simulator.get_paper_positions()
        return CustomJSONResponse(content={"positions": positions})

    @app.get("/api/paper/trades")
    async def get_paper_trades(
        request: Request,
        limit: int = Query(100, description="Maximum number of trades to return"),
    ) -> JSONResponse:
        """Get paper trading trades history.

        Args:
            limit: Maximum number of trades to return (default 100).
        """
        db: aiosqlite.Connection = request.app.state.db

        cursor = await db.execute(
            """
            SELECT id, proposal_id, exchange, symbol, side, order_type,
                   price, volume, notional_value, status, filled_at,
                   paper_pnl, closed_by_side, closed_at, created_at
            FROM paper_trades
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()

        trades = []
        for row in rows:
            trades.append({
                "id": row["id"],
                "proposal_id": row["proposal_id"],
                "exchange": row["exchange"],
                "symbol": row["symbol"],
                "side": row["side"],
                "order_type": row["order_type"],
                "price": row["price"],
                "volume": row["volume"],
                "notional_value": row["notional_value"],
                "status": row["status"],
                "filled_at": row["filled_at"],
                "paper_pnl": row["paper_pnl"],
                "closed_by_side": row["closed_by_side"],
                "closed_at": row["closed_at"],
                "created_at": row["created_at"],
            })

        return CustomJSONResponse(content={"trades": trades, "count": len(trades)})

    @app.get("/api/paper/summary")
    async def get_paper_summary(request: Request) -> JSONResponse:
        """Get paper trading summary.

        Returns total_realized_pnl, total_unrealized_pnl, open_positions,
        total_trades, and daily_pnl.
        """
        simulator: PaperTradingSimulator | None = request.app.state.paper_simulator
        db: aiosqlite.Connection = request.app.state.db

        if simulator is not None:
            pnl_summary = await simulator.get_paper_pnl_summary()
            total_realized_pnl = pnl_summary.get("total_realized_pnl", 0.0)
            total_unrealized_pnl = pnl_summary.get("total_unrealized_pnl", 0.0)
            open_positions = pnl_summary.get("open_positions_count", 0)
            total_trades = pnl_summary.get("trades_count", 0)
        else:
            total_realized_pnl = 0.0
            total_unrealized_pnl = 0.0
            open_positions = 0
            total_trades = 0

        # Get today's daily P&L
        daily_pnl = 0.0
        if db is not None:
            cursor = await db.execute(
                "SELECT realized_pnl, paper_pnl FROM daily_pnl WHERE date = DATE('now', 'utc')"
            )
            row = await cursor.fetchone()
            if row:
                daily_pnl = (row["realized_pnl"] or 0.0) + (row["paper_pnl"] or 0.0)

        return CustomJSONResponse(content={
            "total_realized_pnl": total_realized_pnl,
            "total_unrealized_pnl": total_unrealized_pnl,
            "open_positions": open_positions,
            "total_trades": total_trades,
            "daily_pnl": daily_pnl,
        })

    # Serve static HTML page
    @app.get("/")
    async def index():
        """Serve the main dashboard page."""
        return RedirectResponse(url="/static/index.html")

    return app