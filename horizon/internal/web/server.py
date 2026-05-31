"""FastAPI Web Server with REST Endpoints for Horizon Trading Platform."""

import asyncio
import json
import time
from decimal import Decimal
from typing import Any, Literal, Optional

import aiosqlite
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config.settings import Settings
from ..exchange.registry import ExchangeRegistry
from ..marketdata.fetcher import MarketDataFetcher
from ..ordermanager.manager import OrderManager, OrderSubmissionError
from ..portfolio.tracker import PortfolioTracker
from ..proposals import ProposalQueue
from ..proposals.queue import InvalidProposalStateError, ProposalExpiredError, ProposalNotFoundError


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that converts Decimal to string."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def dumps_safe(obj: Any) -> str:
    """Serialize object to JSON string, handling Decimal conversion."""
    return json.dumps(obj, cls=DecimalEncoder)


def format_timestamp(value: Any) -> Optional[str]:
    """Format a timestamp value to ISO format string.

    Handles both datetime objects and string representations from SQLite.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


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


class ApproveRequestModel(BaseModel):
    """Request model for proposal approval."""

    approved_by: str = Field(..., description="Identifier of the approver")


class RejectRequestModel(BaseModel):
    """Request model for proposal rejection."""

    rejected_by: str = Field(..., description="Identifier of the rejector")
    reason: Optional[str] = Field(None, description="Rejection reason")


class StrategyConfigUpdateModel(BaseModel):
    """Request model for updating strategy configuration (partial updates allowed)."""

    min_confidence_threshold: Optional[int] = Field(None, ge=0, le=100, description="Minimum confidence threshold (0-100)")
    max_risk_tier: Optional[Literal["low", "medium", "high"]] = Field(None, description="Maximum risk tier")
    analysis_interval_hours: Optional[int] = Field(None, ge=1, description="Analysis interval in hours")
    asset_whitelist: Optional[list[str]] = Field(None, description="List of asset symbols to trade")
    max_position_pct: Optional[float] = Field(None, gt=0, description="Maximum position size as percentage of portfolio")
    max_daily_loss_pct: Optional[float] = Field(None, ge=0, description="Maximum daily loss as percentage")
    max_exchange_exposure_pct: Optional[float] = Field(None, gt=0, description="Maximum exchange exposure as percentage")
    cooldown_seconds: Optional[int] = Field(None, ge=0, description="Cooldown period between trades in seconds")
    order_min_notional: Optional[float] = Field(None, gt=0, description="Minimum order notional value")
    order_max_notional: Optional[float] = Field(None, gt=0, description="Maximum order notional value")
    price_drift_expiry_pct: Optional[float] = Field(None, gt=0, description="Price drift threshold for proposal expiry")
    system_prompt: Optional[str] = Field(None, min_length=1, description="System prompt for LLM analysis")


class StrategyModeModel(BaseModel):
    """Request model for updating strategy mode."""

    mode: Literal["paper", "live"] = Field(..., description="Trading mode")


def create_app(
    settings: Settings,
    db: aiosqlite.Connection,
    registry: ExchangeRegistry,
    fetcher: MarketDataFetcher,
    order_manager: OrderManager,
    portfolio_tracker: PortfolioTracker,
    proposal_queue: ProposalQueue,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Application settings.
        db: SQLite database connection.
        registry: Exchange registry.
        fetcher: Market data fetcher.
        order_manager: Order manager.
        portfolio_tracker: Portfolio tracker.
        proposal_queue: Proposal queue for trade proposals.

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
    app.state.proposal_queue = proposal_queue

    # Health check endpoint
    @app.get("/api/health")
    async def health() -> JSONResponse:
        """Health check endpoint."""
        return CustomJSONResponse(content={
            "status": "ok",
            "timestamp": int(time.time()),
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

    # ---- Proposal Endpoints ----

    @app.get("/api/proposals")
    async def list_proposals(
        status: Optional[str] = Query(
            None,
            description="Filter by status: proposed, approved, rejected, expired, executed",
        ),
        limit: int = Query(50, ge=1, le=200, description="Maximum number of proposals to return"),
    ) -> JSONResponse:
        """List proposals with optional status filter.

        Args:
            status: Optional status filter.
            limit: Maximum number of proposals to return (max 200).

        Returns:
            Dictionary with proposals list and count.
        """
        # Convert status string to ProposalStatus enum
        from ..proposals.models import ProposalStatus

        status_filter = None
        if status is not None:
            try:
                status_filter = ProposalStatus(status)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: {status}. Must be one of: proposed, approved, rejected, expired, executed",
                )

        proposals = await proposal_queue.get_proposals(status=status_filter, limit=limit)
        return CustomJSONResponse(content={
            "proposals": [p.to_dict() for p in proposals],
            "count": len(proposals),
        })

    @app.get("/api/proposals/stats")
    async def get_proposal_stats() -> JSONResponse:
        """Get proposal statistics by status.

        Returns:
            Dictionary with counts by status.
        """
        stats = await proposal_queue.get_stats()
        return CustomJSONResponse(content=stats)

    @app.get("/api/proposals/{proposal_id}")
    async def get_proposal(proposal_id: str) -> JSONResponse:
        """Get a single proposal by ID.

        Args:
            proposal_id: UUID of the proposal.

        Returns:
            Proposal dict.

        Raises:
            HTTP 404: If proposal not found.
        """
        proposal = await proposal_queue.get_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
        return CustomJSONResponse(content=proposal.to_dict())

    @app.post("/api/proposals/{proposal_id}/approve")
    async def approve_proposal(
        proposal_id: str,
        request: ApproveRequestModel,
    ) -> JSONResponse:
        """Approve a proposal and execute the corresponding order.

        Args:
            proposal_id: UUID of the proposal to approve.
            request: Approval request with approved_by field.

        Returns:
            Updated proposal dict with status 'executed'.

        Raises:
            HTTP 400: If proposal not in proposed state.
            HTTP 410: If proposal has expired.
        """
        try:
            proposal = await proposal_queue.approve(proposal_id, request.approved_by)
            return CustomJSONResponse(content=proposal.to_dict())
        except ProposalNotFoundError:
            raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
        except InvalidProposalStateError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ProposalExpiredError as e:
            raise HTTPException(status_code=410, detail=str(e))

    @app.post("/api/proposals/{proposal_id}/reject")
    async def reject_proposal(
        proposal_id: str,
        request: RejectRequestModel,
    ) -> JSONResponse:
        """Reject a proposal.

        Args:
            proposal_id: UUID of the proposal to reject.
            request: Rejection request with rejected_by and optional reason.

        Returns:
            Updated proposal dict with status 'rejected'.

        Raises:
            HTTP 400: If proposal not in proposed state.
        """
        try:
            proposal = await proposal_queue.reject(
                proposal_id,
                request.rejected_by,
                request.reason or "",
            )
            return CustomJSONResponse(content=proposal.to_dict())
        except ProposalNotFoundError:
            raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
        except InvalidProposalStateError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/llm/history")
    async def get_llm_history(
        limit: int = Query(50, ge=1, le=200, description="Maximum number of records to return"),
    ) -> JSONResponse:
        """Get LLM analysis history.

        Args:
            limit: Maximum number of records to return (max 200).

        Returns:
            Dictionary with history list.
        """
        cursor = await db.execute(
            """
            SELECT id, triggered_at, completion_status, latency_ms,
                   token_count_input, token_count_output, parsed_proposals_count, error_message
            FROM llm_analysis_history
            ORDER BY triggered_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        history = [
            {
                "id": row["id"],
                "triggered_at": format_timestamp(row["triggered_at"]),
                "completion_status": row["completion_status"],
                "latency_ms": row["latency_ms"],
                "token_count_input": row["token_count_input"],
                "token_count_output": row["token_count_output"],
                "parsed_proposals_count": row["parsed_proposals_count"],
                "error_message": row["error_message"],
            }
            for row in rows
        ]
        return CustomJSONResponse(content={"history": history})

    # ---- Strategy Config Endpoints ----

    @app.get("/api/strategy/config")
    async def get_strategy_config() -> JSONResponse:
        """Get the active strategy configuration.

        Returns:
            Active strategy config as JSON (excludes sensitive fields like autonomy_enabled).

        Raises:
            HTTP 404: If no active strategy config found.
        """
        cursor = await db.execute(
            "SELECT * FROM strategy_configs WHERE enabled = 1 LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No active strategy config found")

        # Parse asset_whitelist from JSON string if needed
        asset_whitelist = row["asset_whitelist"]
        if isinstance(asset_whitelist, str):
            import json
            asset_whitelist = json.loads(asset_whitelist)

        # Return config without sensitive fields (autonomy_enabled is NOT exposed)
        return CustomJSONResponse(content={
            "id": row["id"],
            "name": row["name"],
            "enabled": row["enabled"],
            "mode": row["mode"],
            "min_confidence_threshold": row["min_confidence_threshold"],
            "max_risk_tier": row["max_risk_tier"],
            "analysis_interval_hours": row["analysis_interval_hours"],
            "asset_whitelist": asset_whitelist,
            "max_position_pct": row["max_position_pct"],
            "max_daily_loss_pct": row["max_daily_loss_pct"],
            "max_exchange_exposure_pct": row["max_exchange_exposure_pct"],
            "cooldown_seconds": row["cooldown_seconds"],
            "order_min_notional": row["order_min_notional"],
            "order_max_notional": row["order_max_notional"],
            "price_drift_expiry_pct": row["price_drift_expiry_pct"],
            "system_prompt": row["system_prompt"],
            "created_at": format_timestamp(row["created_at"]),
            "updated_at": format_timestamp(row["updated_at"]),
        })

    @app.put("/api/strategy/config")
    async def update_strategy_config(config_data: StrategyConfigUpdateModel) -> JSONResponse:
        """Update the active strategy configuration (partial updates supported).

        Args:
            config_data: Updated configuration fields (only non-None fields are updated).

        Returns:
            Updated strategy config.

        Raises:
            HTTP 404: If no active strategy config found.
        """
        import json

        # Get the active config ID
        cursor = await db.execute(
            "SELECT id FROM strategy_configs WHERE enabled = 1 LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No active strategy config found")

        config_id = row["id"]

        # Build dynamic UPDATE based on provided fields
        updates = []
        params = []
        if config_data.min_confidence_threshold is not None:
            updates.append("min_confidence_threshold = ?")
            params.append(config_data.min_confidence_threshold)
        if config_data.max_risk_tier is not None:
            updates.append("max_risk_tier = ?")
            params.append(config_data.max_risk_tier)
        if config_data.analysis_interval_hours is not None:
            updates.append("analysis_interval_hours = ?")
            params.append(config_data.analysis_interval_hours)
        if config_data.asset_whitelist is not None:
            updates.append("asset_whitelist = ?")
            params.append(json.dumps(config_data.asset_whitelist))
        if config_data.max_position_pct is not None:
            updates.append("max_position_pct = ?")
            params.append(config_data.max_position_pct)
        if config_data.max_daily_loss_pct is not None:
            updates.append("max_daily_loss_pct = ?")
            params.append(config_data.max_daily_loss_pct)
        if config_data.max_exchange_exposure_pct is not None:
            updates.append("max_exchange_exposure_pct = ?")
            params.append(config_data.max_exchange_exposure_pct)
        if config_data.cooldown_seconds is not None:
            updates.append("cooldown_seconds = ?")
            params.append(config_data.cooldown_seconds)
        if config_data.order_min_notional is not None:
            updates.append("order_min_notional = ?")
            params.append(config_data.order_min_notional)
        if config_data.order_max_notional is not None:
            updates.append("order_max_notional = ?")
            params.append(config_data.order_max_notional)
        if config_data.price_drift_expiry_pct is not None:
            updates.append("price_drift_expiry_pct = ?")
            params.append(config_data.price_drift_expiry_pct)
        if config_data.system_prompt is not None:
            updates.append("system_prompt = ?")
            params.append(config_data.system_prompt)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(config_id)
            await db.execute(
                f"UPDATE strategy_configs SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            await db.commit()

        # Fetch and return the updated config
        cursor = await db.execute(
            "SELECT * FROM strategy_configs WHERE id = ?",
            (config_id,)
        )
        row = await cursor.fetchone()

        # Parse asset_whitelist from JSON string if needed
        asset_whitelist = row["asset_whitelist"]
        if isinstance(asset_whitelist, str):
            asset_whitelist = json.loads(asset_whitelist)

        return CustomJSONResponse(content={
            "id": row["id"],
            "name": row["name"],
            "enabled": row["enabled"],
            "mode": row["mode"],
            "min_confidence_threshold": row["min_confidence_threshold"],
            "max_risk_tier": row["max_risk_tier"],
            "analysis_interval_hours": row["analysis_interval_hours"],
            "asset_whitelist": asset_whitelist,
            "max_position_pct": row["max_position_pct"],
            "max_daily_loss_pct": row["max_daily_loss_pct"],
            "max_exchange_exposure_pct": row["max_exchange_exposure_pct"],
            "cooldown_seconds": row["cooldown_seconds"],
            "order_min_notional": row["order_min_notional"],
            "order_max_notional": row["order_max_notional"],
            "price_drift_expiry_pct": row["price_drift_expiry_pct"],
            "system_prompt": row["system_prompt"],
            "created_at": format_timestamp(row["created_at"]),
            "updated_at": format_timestamp(row["updated_at"]),
        })

    @app.post("/api/strategy/mode")
    async def update_strategy_mode(mode_data: StrategyModeModel) -> JSONResponse:
        """Update the active strategy mode.

        Args:
            mode_data: New mode ('paper' or 'live').

        Returns:
            Updated mode.

        Raises:
            HTTP 404: If no active strategy config found.
        """
        # Get the active config ID
        cursor = await db.execute(
            "SELECT id FROM strategy_configs WHERE enabled = 1 LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No active strategy config found")

        config_id = row["id"]

        # Update the mode
        await db.execute(
            "UPDATE strategy_configs SET mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (mode_data.mode, config_id),
        )
        await db.commit()

        return CustomJSONResponse(content={"mode": mode_data.mode})

    # Serve static HTML page
    @app.get("/")
    async def index() -> HTMLResponse:
        """Serve the main dashboard page."""
        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Horizon Trading Platform</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f1a; color: #e0e0e0; min-height: 100vh; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px 40px; }
        .header h1 { font-size: 24px; font-weight: 600; }
        .container { max-width: 1200px; margin: 0 auto; padding: 40px 20px; }
        .card { background: #1a1a2e; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .card h2 { color: #667eea; margin-bottom: 16px; font-size: 18px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .stat { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #2a2a4a; }
        .stat:last-child { border-bottom: none; }
        .stat-label { color: #888; }
        .stat-value { font-weight: 600; color: #fff; }
        .portfolio-value { font-size: 32px; color: #667eea; font-weight: 700; margin: 20px 0; }
        table { width: 100%; border-collapse: collapse; }
        th, td { text-align: left; padding: 12px; border-bottom: 1px solid #2a2a4a; }
        th { color: #667eea; font-weight: 600; }
        .positive { color: #4ade80; }
        .negative { color: #f87171; }
        .loading { text-align: center; padding: 40px; color: #888; }
        .error { color: #f87171; padding: 20px; text-align: center; background: #2a1a1a; border-radius: 8px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Horizon Trading Platform</h1>
    </div>
    <div class="container">
        <div class="grid">
            <div class="card">
                <h2>System Status</h2>
                <div id="health">
                    <div class="loading">Loading...</div>
                </div>
            </div>
            <div class="card">
                <h2>Portfolio Value</h2>
                <div id="portfolio-value" class="portfolio-value">Loading...</div>
                <div id="portfolio-breakdown"></div>
            </div>
        </div>
        <div class="card">
            <h2>Market Data</h2>
            <div id="market-data">
                <div class="loading">Loading...</div>
            </div>
        </div>
        <div class="card">
            <h2>Active Orders</h2>
            <div id="orders">
                <div class="loading">Loading...</div>
            </div>
        </div>
    </div>
    <script>
        async function loadHealth() {
            try {
                const res = await fetch('/api/health');
                const data = await res.json();
                document.getElementById('health').innerHTML =
                    '<div class="stat"><span class="stat-label">Status</span><span class="stat-value positive">' + data.status + '</span></div>' +
                    '<div class="stat"><span class="stat-label">Timestamp</span><span class="stat-value">' + new Date(data.timestamp * 1000).toLocaleString() + '</span></div>';
            } catch (e) {
                document.getElementById('health').innerHTML = '<div class="error">Failed to load health data</div>';
            }
        }

        async function loadPortfolio() {
            try {
                const res = await fetch('/api/portfolio');
                const data = await res.json();
                document.getElementById('portfolio-value').textContent = '$' + parseFloat(data.total_usdt_value).toLocaleString();
                let breakdown = '';
                for (const [exchange, balances] of Object.entries(data.exchanges)) {
                    breakdown += '<div style="margin-top:16px;font-weight:600;color:#667eea;">' + exchange.toUpperCase() + '</div>';
                    for (const b of balances) {
                        breakdown += '<div class="stat"><span class="stat-label">' + b.asset + '</span><span class="stat-value">' + parseFloat(b.free).toLocaleString() + '</span></div>';
                    }
                }
                document.getElementById('portfolio-breakdown').innerHTML = breakdown;
            } catch (e) {
                document.getElementById('portfolio-value').textContent = 'Error';
            }
        }

        async function loadMarketData() {
            const symbols = ['BTC/USDT', 'ETH/USDT'];
            let html = '<table><tr><th>Symbol</th><th>Exchange</th><th>Price</th><th>Volume (24h)</th></tr>';
            for (const symbol of symbols) {
                try {
                    const res = await fetch('/api/market-data/' + encodeURIComponent(symbol));
                    const data = await res.json();
                    for (const t of data.tickers) {
                        html += '<tr><td>' + t.symbol + '</td><td>' + t.exchange + '</td><td>$' + parseFloat(t.price).toLocaleString() + '</td><td>' + parseFloat(t.volume_24h).toLocaleString() + '</td></tr>';
                    }
                } catch (e) {}
            }
            html += '</table>';
            document.getElementById('market-data').innerHTML = html || '<div class="error">No market data available</div>';
        }

        async function loadOrders() {
            try {
                const res = await fetch('/api/orders?status=open');
                const data = await res.json();
                if (data.length === 0) {
                    document.getElementById('orders').innerHTML = '<div style="color:#888;text-align:center;padding:20px;">No active orders</div>';
                    return;
                }
                let html = '<table><tr><th>ID</th><th>Symbol</th><th>Side</th><th>Type</th><th>Volume</th><th>Price</th><th>Status</th></tr>';
                for (const o of data) {
                    const sideClass = o.side === 'buy' ? 'positive' : 'negative';
                    html += '<tr><td>' + o.order_id.substring(0, 8) + '...</td><td>' + o.symbol + '</td><td class="' + sideClass + '">' + o.side.toUpperCase() + '</td><td>' + o.order_type + '</td><td>' + parseFloat(o.volume).toLocaleString() + '</td><td>' + (o.price ? '$' + parseFloat(o.price).toLocaleString() : 'Market') + '</td><td>' + o.status + '</td></tr>';
                }
                html += '</table>';
                document.getElementById('orders').innerHTML = html;
            } catch (e) {
                document.getElementById('orders').innerHTML = '<div class="error">Failed to load orders</div>';
            }
        }

        loadHealth();
        loadPortfolio();
        loadMarketData();
        loadOrders();
        setInterval(() => { loadHealth(); loadPortfolio(); loadMarketData(); loadOrders(); }, 30000);
    </script>
</body>
</html>"""
        return HTMLResponse(content=html_content)

    return app