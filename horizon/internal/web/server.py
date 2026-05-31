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


def create_app(
    settings: Settings,
    db: aiosqlite.Connection,
    registry: ExchangeRegistry,
    fetcher: MarketDataFetcher,
    order_manager: OrderManager,
    portfolio_tracker: PortfolioTracker,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Application settings.
        db: SQLite database connection.
        registry: Exchange registry.
        fetcher: Market data fetcher.
        order_manager: Order manager.
        portfolio_tracker: Portfolio tracker.

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