"""FastAPI Web Server with REST Endpoints for Horizon Trading Platform."""

import asyncio
import json
import os
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
from ..paper.simulator import PaperTradeError, PaperTradingSimulator
from ..paper.stats import compute_trade_stats


def _today_utc() -> str:
    """Return today's date in UTC as 'YYYY-MM-DD' (matches SQLite DATE('now','utc'))."""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _base_asset(symbol: str) -> str:
    """Normalize a trading symbol to its base asset (e.g. 'BTC/USDT' -> 'BTC')."""
    if not symbol:
        return ""
    s = symbol.upper()
    for quote in ("USDT", "USDC", "USD", "BUSD"):
        if s.endswith(quote) and not s.endswith(quote + quote):
            s = s[: -len(quote)]
            break
    if "/" in s:
        s = s.split("/", 1)[0]
    return s.strip("/") or symbol
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
    """Request model for order submission.

    The order can be expressed in two ways:
    - ``volume`` (in symbol base units, e.g. BTC amount)
    - ``volume_usd`` (in quote currency; server converts to base units using
      the current market price for market orders, or the limit price for
      limit orders)

    If both are provided, ``volume_usd`` takes precedence. Exactly one of the
    two must be supplied.
    """

    exchange: str = Field(..., description="Exchange name")
    symbol: str = Field(..., description="Trading symbol")
    side: Literal["buy", "sell"] = Field(..., description="Order side")
    type: Literal["market", "limit"] = Field(..., description="Order type")
    price: Optional[float] = Field(None, description="Limit price (required for limit orders)")
    volume: Optional[float] = Field(None, description="Order volume in base units (e.g. BTC amount)")
    volume_usd: Optional[float] = Field(None, description="Order notional in quote currency (USDT/USDC); converted server-side")


class ActiveExchangeModel(BaseModel):
    """Request model for setting active exchange."""

    exchange: str = Field(..., description="Exchange name to set as active")


class ActivePairListModel(BaseModel):
    """Request model for setting active PairList."""

    name: str = Field(..., description="Name of the PairList to activate")


class StrategyModeModel(BaseModel):
    """Request model for setting system mode."""

    mode: Literal["live", "paper"] = Field(..., description="System mode: live or paper")


class StrategyConfigModel(BaseModel):
    """Request model for updating strategy configuration."""

    mode: Optional[Literal["live", "paper"]] = Field(None, description="System mode")
    autonomy_enabled: Optional[bool] = Field(None, description="Enable autonomous execution")
    min_confidence_threshold: Optional[int] = Field(None, ge=0, le=100)
    max_risk_tier: Optional[Literal["low", "medium", "high"]] = None
    analysis_interval_hours: Optional[int] = Field(None, ge=1)
    asset_whitelist: Optional[list[str]] = None
    max_position_pct: Optional[float] = Field(None, gt=0, le=100)
    max_daily_loss_pct: Optional[float] = Field(None, gt=0, le=100)
    max_exchange_exposure_pct: Optional[float] = Field(None, gt=0, le=100)
    cooldown_seconds: Optional[int] = Field(None, ge=0)
    order_min_notional: Optional[float] = Field(None, gt=0)
    order_max_notional: Optional[float] = Field(None, gt=0)


class ApproveProposalModel(BaseModel):
    """Request model for approving a proposal."""

    approved_by: str = Field(..., description="Identifier of approver")


class RejectProposalModel(BaseModel):
    """Request model for rejecting a proposal."""

    rejected_by: str = Field(..., description="Identifier of rejecter")
    reason: Optional[str] = Field(None, description="Optional rejection reason")


class CreateCoPilotSessionModel(BaseModel):
    """Request model for creating a new Co-Pilot session."""

    title: Optional[str] = None


class SendCoPilotMessageModel(BaseModel):
    """Request model for sending a user message in a Co-Pilot session."""

    text: str = Field(..., min_length=1, max_length=8000)


def create_app(
    settings: Settings,
    db: aiosqlite.Connection,
    registry: ExchangeRegistry,
    fetcher: MarketDataFetcher,
    order_manager: OrderManager,
    portfolio_tracker: PortfolioTracker | None = None,
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
    async def get_portfolio(request: Request) -> JSONResponse:
        """Get current portfolio snapshot.

        Reads the PortfolioTracker from app.state so we use the same instance
        that the lifespan initialized and called start() on. This avoids the
        closure-capture pitfall where the create_app-constructed instance has
        never loaded any balances.
        """
        tracker: PortfolioTracker | None = request.app.state.portfolio_tracker
        if tracker is None:
            return CustomJSONResponse(content={
                "exchanges": {},
                "total_usdt_value": "0",
                "timestamp_ms": int(time.time() * 1000),
            })
        snapshot = await tracker.get_snapshot()
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

        # Normalize symbol: 'BTCUSDT' -> 'BTC/USDT' for API compatibility
        normalized_symbol = symbol
        if "/" not in normalized_symbol and normalized_symbol.endswith("USDT"):
            normalized_symbol = normalized_symbol[:-4] + "/USDT"

        try:
            candles = await datasource.get_klines(
                symbol=normalized_symbol,
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
    async def submit_order(request: Request, order_data: OrderRequestModel) -> JSONResponse:
        """Submit a new order (paper or live based on system mode).

        Volume can be supplied either as ``volume`` (base units) or ``volume_usd``
        (quote-currency notional). When ``volume_usd`` is given, the server
        converts it to base units using the current ticker price (market
        orders) or the supplied limit price (limit orders).
        """
        evaluator: RiskGuardrailEvaluator | None = request.app.state.guardrail_evaluator
        paper_sim: PaperTradingSimulator | None = request.app.state.paper_simulator
        fetcher: MarketDataFetcher | None = request.app.state.fetcher

        # Validate that exactly one of volume / volume_usd is provided
        if order_data.volume is None and order_data.volume_usd is None:
            raise HTTPException(
                status_code=400,
                detail="Must provide either 'volume' (base units) or 'volume_usd' (quote currency)",
            )
        if order_data.volume is not None and order_data.volume_usd is not None:
            raise HTTPException(
                status_code=400,
                detail="Provide only one of 'volume' or 'volume_usd', not both",
            )

        # Resolve volume (in base units). If volume_usd is provided, convert it.
        volume: Decimal
        if order_data.volume_usd is not None:
            usd = Decimal(str(order_data.volume_usd))
            if usd <= Decimal("0"):
                raise HTTPException(status_code=400, detail="volume_usd must be positive")

            # Choose conversion price: limit price for limit orders, market ticker for market orders
            if order_data.type == "limit":
                if order_data.price is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Limit orders require a 'price' field for USD conversion",
                    )
                conv_price = Decimal(str(order_data.price))
            else:
                # Market order: fetch current ticker (cache first, then direct adapter)
                normalized = order_data.symbol
                if "/" not in normalized and normalized.endswith("USDT"):
                    normalized = normalized[:-4] + "/USDT"
                ticker = fetcher.get_ticker(normalized, order_data.exchange) if fetcher else None
                if ticker is None and fetcher is not None and hasattr(fetcher, "_registry"):
                    # Fallback: fetch directly from the exchange adapter
                    adapter = fetcher._registry.get_active_adapter(order_data.exchange)
                    if adapter is not None:
                        try:
                            ticker = await adapter.fetch_ticker(normalized)
                        except Exception:
                            ticker = None
                if ticker is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No ticker available for {order_data.symbol} on {order_data.exchange}; cannot convert volume_usd",
                    )
                conv_price = ticker.price

            if conv_price <= Decimal("0"):
                raise HTTPException(status_code=400, detail=f"Invalid conversion price {conv_price}")

            volume = (usd / conv_price).quantize(Decimal("0.00000001"))
        else:
            volume = Decimal(str(order_data.volume))
            if volume <= Decimal("0"):
                raise HTTPException(status_code=400, detail="volume must be positive")

        # Check current system mode
        mode = SystemMode.LIVE
        if evaluator is not None:
            mode = await evaluator.get_current_mode()

        if mode == SystemMode.PAPER and paper_sim is not None:
            # PAPER mode: use paper simulator
            try:
                if order_data.type == "market":
                    result = await paper_sim.simulate_market_order(
                        proposal_id=None,
                        exchange=order_data.exchange,
                        symbol=order_data.symbol,
                        side=order_data.side,
                        volume=volume,
                    )
                else:
                    price = Decimal(str(order_data.price)) if order_data.price is not None else Decimal("0")
                    result = await paper_sim.simulate_limit_order(
                        proposal_id=None,
                        exchange=order_data.exchange,
                        symbol=order_data.symbol,
                        side=order_data.side,
                        price=price,
                        volume=volume,
                    )
            except PaperTradeError as e:
                raise HTTPException(status_code=400, detail=str(e))
            return CustomJSONResponse(content={
                "order_id": result.order_id,
                "exchange_order_id": result.order_id,
                "exchange": result.exchange,
                "symbol": result.symbol,
                "side": result.side,
                "order_type": result.order_type,
                "price": str(result.price),
                "volume": str(result.volume),
                "filled_volume": str(result.volume),
                "status": result.status,
                "created_at_ms": int(time.time() * 1000),
                "updated_at_ms": int(time.time() * 1000),
            })
        elif mode == SystemMode.PAPER and paper_sim is None:
            raise HTTPException(status_code=503, detail="Paper simulator not available")

        # LIVE mode: use order manager
        order_request = OrderManager.OrderRequest(
            exchange=order_data.exchange,
            symbol=order_data.symbol,
            side=order_data.side,
            order_type=order_data.type,
            price=Decimal(str(order_data.price)) if order_data.price is not None else None,
            volume=volume,
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
        total_trades, daily_pnl, plus paper cash info (initial_cash,
        current_cash, position_value, total_balance).
        """
        simulator: PaperTradingSimulator | None = request.app.state.paper_simulator
        db: aiosqlite.Connection = request.app.state.db

        if simulator is not None:
            pnl_summary = await simulator.get_paper_pnl_summary()
            cash = await simulator.get_cash()
            total_realized_pnl = pnl_summary.get("total_realized_pnl", 0.0)
            total_unrealized_pnl = pnl_summary.get("total_unrealized_pnl", 0.0)
            open_positions = pnl_summary.get("open_positions_count", 0)
            total_trades = pnl_summary.get("trades_count", 0)
        else:
            total_realized_pnl = 0.0
            total_unrealized_pnl = 0.0
            open_positions = 0
            total_trades = 0
            cash = {
                "initial_cash": 0.0,
                "current_cash": 0.0,
                "position_value": 0.0,
                "total_balance": 0.0,
            }

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
            "initial_cash": cash["initial_cash"],
            "current_cash": cash["current_cash"],
            "position_value": cash["position_value"],
            "total_balance": cash["total_balance"],
        })

    @app.get("/api/paper/trades/stats")
    async def get_paper_trades_stats(request: Request) -> JSONResponse:
        """Aggregate statistics for closed and open paper trades.

        Used by the released-trades stat cards in the main dashboard.
        """
        db: aiosqlite.Connection = request.app.state.db

        cursor = await db.execute(
            """
            SELECT id, exchange, symbol, side, order_type, price, volume,
                   notional_value, status, filled_at, paper_pnl,
                   closed_by_side, closed_at, created_at
            FROM paper_trades
            """
        )
        rows = await cursor.fetchall()

        trades = [
            {
                "id": row["id"],
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
            }
            for row in rows
        ]

        stats = compute_trade_stats(trades)
        return CustomJSONResponse(content=stats)

    @app.get("/api/portfolio/allocation")
    async def get_portfolio_allocation(request: Request) -> JSONResponse:
        """Return asset allocation breakdown for the portfolio page and balance panel.

        Returns:
            {
                "total_value": float,
                "allocations": [
                    {"asset": "BTC", "value": float, "pct": float, "kind": "position"|"cash"},
                    ...
                ]
            }
        """
        simulator: PaperTradingSimulator | None = request.app.state.paper_simulator

        if simulator is None:
            return CustomJSONResponse(content={"total_value": 0.0, "allocations": []})

        cash = await simulator.get_cash()
        positions = await simulator.get_paper_positions()

        cash_value = float(cash.get("current_cash", 0.0))
        position_values: dict[str, float] = {}
        for p in positions:
            symbol = p.get("symbol", "")
            base = _base_asset(symbol)
            current_price = float(p.get("current_price", 0.0))
            volume = float(p.get("volume", 0.0))
            value = current_price * volume
            position_values[base] = position_values.get(base, 0.0) + value

        total_value = cash_value + sum(position_values.values())

        allocations: list[dict[str, Any]] = []
        for asset, value in sorted(position_values.items(), key=lambda kv: -kv[1]):
            allocations.append({
                "asset": asset,
                "value": value,
                "pct": (value / total_value * 100.0) if total_value > 0 else 0.0,
                "kind": "position",
            })
        if cash_value > 0 or not allocations:
            allocations.append({
                "asset": "USDT",
                "value": cash_value,
                "pct": (cash_value / total_value * 100.0) if total_value > 0 else 100.0,
                "kind": "cash",
            })

        return CustomJSONResponse(content={
            "total_value": total_value,
            "allocations": allocations,
        })

    @app.get("/api/portfolio/equity-curve")
    async def get_portfolio_equity_curve(request: Request) -> JSONResponse:
        """Return cumulative equity curve from daily_pnl.

        First point is initial_cash (today's "zero" reference); subsequent
        points are initial_cash plus cumulative paper_pnl up to each date.
        """
        simulator: PaperTradingSimulator | None = request.app.state.paper_simulator
        db: aiosqlite.Connection = request.app.state.db

        initial_cash = 0.0
        if simulator is not None:
            cash = await simulator.get_cash()
            initial_cash = float(cash.get("initial_cash", 0.0))

        points: list[dict[str, Any]] = []
        if db is not None:
            cursor = await db.execute(
                """
                SELECT date, paper_pnl
                FROM daily_pnl
                ORDER BY date ASC
                """
            )
            rows = await cursor.fetchall()
            cumulative = initial_cash
            for row in rows:
                cumulative += float(row["paper_pnl"] or 0.0)
                points.append({
                    "date": row["date"],
                    "value": cumulative,
                })

        # Always include the starting point so a chart can render
        if not points:
            points.append({"date": "today", "value": initial_cash})
        else:
            points.insert(0, {"date": "initial", "value": initial_cash})

        return CustomJSONResponse(content={
            "points": points,
            "initial_cash": initial_cash,
        })

    @app.get("/api/portfolio/daily-change")
    async def get_portfolio_daily_change(request: Request) -> JSONResponse:
        """Return 24h P&L change for the main balance panel header.

        Uses today's paper_pnl from daily_pnl vs yesterday's closing
        paper_pnl to compute change_pnl. Change_pct is change_pnl /
        (current_balance - change_pnl) for the proportion.
        """
        simulator: PaperTradingSimulator | None = request.app.state.paper_simulator
        db: aiosqlite.Connection = request.app.state.db

        if simulator is None or db is None:
            return CustomJSONResponse(content={
                "change_pct": 0.0,
                "change_pnl": 0.0,
                "previous_balance": 0.0,
                "current_balance": 0.0,
            })

        cash = await simulator.get_cash()
        current_balance = float(cash.get("total_balance", 0.0))

        cursor = await db.execute(
            """
            SELECT date, paper_pnl FROM daily_pnl
            WHERE date IN (DATE('now', 'utc', '-1 day'), DATE('now', 'utc'))
            ORDER BY date ASC
            """
        )
        rows = await cursor.fetchall()

        today_pnl = 0.0
        for row in rows:
            if row["date"] == _today_utc():
                today_pnl = float(row["paper_pnl"] or 0.0)
        previous_balance = current_balance - today_pnl
        change_pnl = today_pnl
        change_pct = (
            (change_pnl / previous_balance * 100.0)
            if previous_balance > 0 else 0.0
        )

        return CustomJSONResponse(content={
            "change_pct": change_pct,
            "change_pnl": change_pnl,
            "previous_balance": previous_balance,
            "current_balance": current_balance,
        })

    # ---- Strategy Mode & Config Endpoints ----

    @app.post("/api/strategy/mode")
    async def update_strategy_mode(
        request: Request,
        body: StrategyModeModel,
    ) -> JSONResponse:
        """Switch system mode between live and paper."""
        evaluator: RiskGuardrailEvaluator | None = request.app.state.guardrail_evaluator

        if evaluator is None:
            raise HTTPException(status_code=503, detail="Guardrail evaluator not available")

        try:
            from ..guardrails.evaluator import SystemMode
            mode = SystemMode(body.mode)
            await evaluator.set_mode(mode)

            strategy_config = request.app.state.strategy_config
            if strategy_config is not None:
                strategy_config.mode = body.mode

            return CustomJSONResponse(content={
                "mode": body.mode,
                "message": f"System mode switched to {body.mode}",
            })
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid mode: {body.mode}")

    @app.get("/api/strategy/mode")
    async def get_strategy_mode(request: Request) -> JSONResponse:
        """Get current system mode."""
        evaluator: RiskGuardrailEvaluator | None = request.app.state.guardrail_evaluator

        if evaluator is None:
            raise HTTPException(status_code=503, detail="Guardrail evaluator not available")

        mode = await evaluator.get_current_mode()
        return CustomJSONResponse(content={"mode": mode.value})

    @app.put("/api/strategy/config")
    async def update_strategy_config(
        request: Request,
        body: StrategyConfigModel,
    ) -> JSONResponse:
        """Update strategy configuration fields."""
        db: aiosqlite.Connection = request.app.state.db

        updates = []
        params = []

        if body.mode is not None:
            updates.append("mode = ?")
            params.append(body.mode)
        if body.autonomy_enabled is not None:
            updates.append("autonomy_enabled = ?")
            params.append(int(body.autonomy_enabled))
        if body.min_confidence_threshold is not None:
            updates.append("min_confidence_threshold = ?")
            params.append(body.min_confidence_threshold)
        if body.max_risk_tier is not None:
            updates.append("max_risk_tier = ?")
            params.append(body.max_risk_tier)
        if body.analysis_interval_hours is not None:
            updates.append("analysis_interval_hours = ?")
            params.append(body.analysis_interval_hours)
        if body.asset_whitelist is not None:
            updates.append("asset_whitelist = ?")
            params.append(json.dumps(body.asset_whitelist))
        if body.max_position_pct is not None:
            updates.append("max_position_pct = ?")
            params.append(body.max_position_pct)
        if body.max_daily_loss_pct is not None:
            updates.append("max_daily_loss_pct = ?")
            params.append(body.max_daily_loss_pct)
        if body.max_exchange_exposure_pct is not None:
            updates.append("max_exchange_exposure_pct = ?")
            params.append(body.max_exchange_exposure_pct)
        if body.cooldown_seconds is not None:
            updates.append("cooldown_seconds = ?")
            params.append(body.cooldown_seconds)
        if body.order_min_notional is not None:
            updates.append("order_min_notional = ?")
            params.append(body.order_min_notional)
        if body.order_max_notional is not None:
            updates.append("order_max_notional = ?")
            params.append(body.order_max_notional)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc))
        params.append("default_long_term")

        query = f"UPDATE strategy_configs SET {', '.join(updates)} WHERE name = ?"
        await db.execute(query, params)
        await db.commit()

        return CustomJSONResponse(content={
            "message": "Strategy config updated",
            "updated_fields": len(updates) - 1,
        })

    # =========================================================================
    # Proposals endpoints (5)
    # =========================================================================

    @app.get("/api/proposals")
    async def list_proposals(
        status: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ) -> JSONResponse:
        """List trade proposals filtered by status."""
        queue = getattr(app.state, "proposal_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="Proposal queue not running")
        from ..proposals.models import ProposalStatus
        status_filter = None
        if status:
            try:
                status_filter = ProposalStatus(status)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"invalid status '{status}'")
        proposals = await queue.get_proposals(status=status_filter, limit=limit)
        return CustomJSONResponse({
            "proposals": [p.to_dict() for p in proposals],
            "count": len(proposals),
        })

    @app.get("/api/proposals/stats")
    async def proposal_stats() -> JSONResponse:
        """Get aggregate proposal counts by status."""
        queue = getattr(app.state, "proposal_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="Proposal queue not running")
        return CustomJSONResponse(await queue.get_stats())

    @app.get("/api/proposals/{proposal_id}")
    async def get_proposal_detail(proposal_id: str) -> JSONResponse:
        """Get full details for a single proposal."""
        queue = getattr(app.state, "proposal_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="Proposal queue not running")
        p = await queue.get_proposal(proposal_id)
        if p is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        return CustomJSONResponse(p.to_dict())

    @app.post("/api/proposals/{proposal_id}/approve")
    async def approve_proposal(proposal_id: str, body: ApproveProposalModel) -> JSONResponse:
        """Approve and execute a proposal."""
        queue = getattr(app.state, "proposal_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="Proposal queue not running")
        from ..proposals.queue import (
            ProposalNotFoundError, InvalidProposalStateError,
            ProposalExpiredError, InvalidSystemModeError,
        )
        try:
            p = await queue.approve(proposal_id, body.approved_by)
        except ProposalNotFoundError:
            raise HTTPException(status_code=404, detail="proposal not found")
        except InvalidProposalStateError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ProposalExpiredError as e:
            raise HTTPException(status_code=410, detail=str(e))
        except InvalidSystemModeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return CustomJSONResponse(p.to_dict())

    @app.post("/api/proposals/{proposal_id}/reject")
    async def reject_proposal(proposal_id: str, body: RejectProposalModel) -> JSONResponse:
        """Reject a proposal with optional reason."""
        queue = getattr(app.state, "proposal_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="Proposal queue not running")
        from ..proposals.queue import ProposalNotFoundError, InvalidProposalStateError
        try:
            p = await queue.reject(proposal_id, body.rejected_by, body.reason or "")
        except ProposalNotFoundError:
            raise HTTPException(status_code=404, detail="proposal not found")
        except InvalidProposalStateError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return CustomJSONResponse(p.to_dict())

    # =========================================================================
    # LLM history + manual trigger + Strategy config GET (3)
    # =========================================================================

    @app.get("/api/llm/history")
    async def llm_history(limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
        """Return recent LLM analysis runs (most recent first)."""
        cursor = await db.execute(
            "SELECT id, strategy_config_id, triggered_at, completion_status, "
            "latency_ms, token_count_input, token_count_output, "
            "parsed_proposals_count, error_message "
            "FROM llm_analysis_history ORDER BY triggered_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return CustomJSONResponse({"history": [dict(r) for r in rows]})

    @app.post("/api/llm/trigger")
    async def trigger_llm_now() -> JSONResponse:
        """Manually trigger one LLM analysis cycle."""
        scheduler = getattr(app.state, "llm_scheduler", None)
        if scheduler is None:
            raise HTTPException(status_code=503, detail="LLM scheduler not running")
        result = await scheduler.trigger_now()
        return CustomJSONResponse({
            "proposals_generated": len(result.proposals) if result else 0,
            "analysis_summary": result.market_analysis_summary if result else "",
            "confidence_explanation": result.confidence_explanation if result else "",
        })

    @app.get("/api/strategy/config")
    async def get_strategy_config() -> JSONResponse:
        """Return the currently active strategy configuration row."""
        cursor = await db.execute(
            "SELECT * FROM strategy_configs WHERE enabled = 1 LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no active strategy config")
        return CustomJSONResponse(dict(row))

    # Serve static HTML page
    @app.get("/")
    async def index():
        """Serve the main dashboard page."""
        return RedirectResponse(url="/static/index.html")

    @app.get("/portfolio", response_class=HTMLResponse)
    async def portfolio():
        """Serve the portfolio analytics page."""
        portfolio_path = os.path.join(
            os.path.dirname(__file__), "static", "portfolio.html"
        )
        with open(portfolio_path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())

    return app