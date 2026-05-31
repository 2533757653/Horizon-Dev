# LLM-Human Collaborative Quantitative Trading Platform — Design Specification

## Version: 1.0.0
## Date: 2026-05-30
## Status: DRAFT — Pending User Review

---

## 1. Vision and Purpose

**Project Name:** Horizon Trading System

**What it is:** A quantitative trading platform that combines LLM-driven autonomous trading with human oversight and collaboration. The system connects to multiple cryptocurrency exchanges (Binance, HTX, Hyperliquid, Bitget), fetches real-time market data, manages orders, and provides a web-based dashboard for monitoring, manual trading, and LLM-human collaborative order workflows.

**Why it exists:** To give a quantitative trader a single unified system where:
- LLM agents can analyze markets and propose trades
- Humans retain final approval authority over all orders
- Portfolio state and performance metrics are visible in real time
- The core foundation is simple, safe, and extensible

**Target User:** A quantitative trader who wants to experiment with LLM-driven strategies while maintaining human control, running locally on a Windows machine.

---

## 2. Design Principles

1. **Incremental Development** — Each step fully implements a specific feature. Steps are never merged half-done. Each step has Tasks, Goals, and Acceptance Criteria.
2. **Foundation First** — Exchange connectivity and market data are proven before any trading logic or LLM is introduced.
3. **Human-in-the-Loop** — No fully autonomous trading. Humans approve, reject, or modify LLM-proposed orders.
4. **Simplicity over Elegance** — Use boring technology (SQLite, vanilla Go, vanilla JS) until there is a demonstrated need to change.
5. **Extensibility** — Exchange adapters follow a common interface so new exchanges can be added without changing core logic.

---

## 3. System Architecture

### 3.1 Macro Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          GO MONOLITH (single binary)                       │
│                                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Exchange   │  │    Market    │  │    Order     │  │  Portfolio  │   │
│  │   Adapters   │  │     Data     │  │   Manager    │  │   Tracker   │   │
│  │  (Binance,   │  │   Fetcher    │  │              │  │              │   │
│  │  HTX, HL, BG)│  │              │  │              │  │              │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
│                                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │     LLM      │  │   Strategy   │  │ Collabora-   │  │   Web        │   │
│  │   Client     │  │   Engine     │  │   tive Ord.  │  │   Dashboard  │   │
│  │ (Anthropic)  │  │              │  │   Workflow   │  │   (HTML/JS)  │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                     SQLite Persistence Layer                         │  │
│  │         (Trades, Orders, Portfolio Snapshots, Market Data)          │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Package Structure (Go)

```
horizon/
├── cmd/
│   └── server/
│       └── main.go              # Entry point, wires all components
├── internal/
│   ├── config/
│   │   └── config.go            # YAML config loader
│   ├── exchange/
│   │   ├── adapter.go          # Common interface: FetchMarketData, PlaceOrder, CancelOrder, GetBalance
│   │   ├── binance/
│   │   │   └── client.go       # BinanceSpotClient implementing exchange.Adapter
│   │   ├── htx/
│   │   │   └── client.go       # HTXSpotClient implementing exchange.Adapter
│   │   ├── hyperliquid/
│   │   │   └── client.go       # HyperliquidClient implementing exchange.Adapter
│   │   └── bitget/
│   │       └── client.go       # BitgetSpotClient implementing exchange.Adapter
│   ├── marketdata/
│   │   └── fetcher.go          # Aggregates market data from all exchanges
│   ├── ordermanager/
│   │   └── manager.go         # Unified order routing, order lifecycle, fill tracking
│   ├── portfolio/
│   │   └── tracker.go         # Position tracking, PnL calculation, balance management
│   ├── llm/
│   │   └── client.go           # Anthropic API client (Step 2+)
│   ├── strategy/
│   │   └── engine.go          # LLM strategy orchestration (Step 2+)
│   ├── collaborative/
│   │   └── workflow.go        # Human-in-the-loop order approval (Step 3)
│   ├── metrics/
│   │   └── dashboard.go       # Metrics aggregation and serving (Step 1 / Step 5)
│   ├── database/
│   │   ├── sqlite.go          # SQLite connection management
│   │   └── migrations/       # Schema migrations
│   └── web/
│       ├── server.go          # HTTP server, route definitions
│       └── static/           # Embedded static files (HTML/JS/CSS)
├── configs/
│   └── config.yaml            # Runtime configuration
├── docs/
│   ├── specs/
│   └── arch/                  # Architecture diagrams
└── tests/
    └── integration/          # Integration tests per module
```

### 3.3 Key Interfaces

#### Exchange Adapter Interface

Every exchange client implements the `exchange.Adapter` interface:

```go
type Adapter interface {
    Name() string
    IsEnabled() bool

    // Market Data
    FetchTicker(ctx context.Context, symbol string) (*Ticker, error)
    FetchOrderBook(ctx context.Context, symbol string, depth int) (*OrderBook, error)
    FetchTrades(ctx context.Context, symbol string, limit int) ([]Trade, error)

    // Account
    FetchBalance(ctx context.Context, asset string) (*Balance, error)
    FetchAllBalances(ctx context.Context) ([]Balance, error)

    // Orders
    PlaceMarketOrder(ctx context.Context, symbol string, side string, volume float64) (*Order, error)
    PlaceLimitOrder(ctx context.Context, symbol string, side string, price, volume float64) (*Order, error)
    CancelOrder(ctx context.Context, symbol string, orderID string) error
    FetchOpenOrders(ctx context.Context, symbol string) ([]Order, error)
    FetchOrderHistory(ctx context.Context, symbol string, limit int) ([]Order, error)
}
```

#### Order Manager Interface

```go
type OrderManager interface {
    SubmitOrder(ctx context.Context, order OrderRequest) (*Order, error)
    CancelOrder(ctx context.Context, orderID string, exchange string) error
    GetOpenOrders(ctx context.Context, exchange string, symbol string) ([]Order, error)
    GetOrderHistory(ctx context.Context, exchange string, symbol string, limit int) ([]Order, error)
    GetPortfolioSnapshot(ctx context.Context) (*PortfolioSnapshot, error)
}
```

#### LLM Client Interface (Step 2+)

```go
type LLMClient interface {
    AnalyzeMarket(ctx context.Context, marketData MarketDataBundle) (AnalysisResult, error)
    ProposeTrade(ctx context.Context, analysis AnalysisResult) (*TradeProposal, error)
    EvaluateRisk(ctx context.Context, proposal TradeProposal, portfolio PortfolioSnapshot) (*RiskAssessment, error)
}
```

---

## 4. Module Step Breakdown

Each step is a **full-implementation milestone**. Each step has:

- **Tasks** — What the micro-development agent must build
- **Goals** — What the step must achieve before moving on
- **Acceptance Criteria** — How the developer self-inspects that the step is complete

---

### Step 1: Core Foundation + Metrics Dashboard

**Purpose:** Establish exchange connectivity, market data fetching, order management, SQLite persistence, and a live web dashboard. This is the hardest step — everything else builds on it.

#### Tasks

1. **Project Scaffold**
   - Initialize Go module (`go mod init`)
   - Create directory structure above
   - Add `go.mod`, `go.sum`, `config.yaml`
   - Add `cmd/server/main.go` that wires all components
   - Add `internal/config/config.go` for YAML config loading

2. **Exchange Adapter Layer**
   - Implement `exchange.Adapter` interface for Binance spot markets
   - Implement `exchange.Adapter` interface for HTX spot markets
   - Implement `exchange.Adapter` interface for Hyperliquid perpetual
   - Implement `exchange.Adapter` interface for Bitget spot markets
   - Each adapter loads its API credentials from `key.txt` (env vars or config)
   - All adapters registered in a map `exchangeRegistry[name]` in `internal/exchange/registry.go`

3. **Market Data Fetcher**
   - `internal/marketdata/fetcher.go` polls configured exchanges at a configurable interval (default 10s)
   - Stores latest ticker, orderbook, and recent trades per symbol in memory
   - Exposes HTTP SSE endpoint (`/api/marketdata/stream`) for dashboard subscription

4. **Order Manager**
   - `internal/ordermanager/manager.go` routes orders through the correct exchange adapter
   - Maintains in-memory state of open orders with order IDs
   - Records all order events (submitted, filled, cancelled, rejected) to SQLite via event log table
   - Supports market order, limit order, and order cancellation

5. **Portfolio Tracker**
   - `internal/portfolio/tracker.go` fetches balances from all exchanges on startup and on demand
   - Calculates unrealized PnL using latest market prices
   - Stores portfolio snapshots in SQLite every 60 seconds and on order fills

6. **SQLite Schema**
   - `internal/database/migrations/001_initial.sql`:
     ```sql
     CREATE TABLE IF NOT EXISTS orders (
         id TEXT PRIMARY KEY,
         exchange TEXT NOT NULL,
         symbol TEXT NOT NULL,
         side TEXT NOT NULL,
         order_type TEXT NOT NULL,
         price REAL,
         volume REAL NOT NULL,
         filled_volume REAL DEFAULT 0,
         status TEXT NOT NULL,
         created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
         updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
     );

     CREATE TABLE IF NOT EXISTS order_events (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         order_id TEXT NOT NULL,
         event_type TEXT NOT NULL,
         event_data TEXT,
         created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
         FOREIGN KEY (order_id) REFERENCES orders(id)
     );

     CREATE TABLE IF NOT EXISTS portfolio_snapshots (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         exchange TEXT NOT NULL,
         asset TEXT NOT NULL,
         free_balance REAL NOT NULL,
         locked_balance REAL NOT NULL,
         usdt_value REAL NOT NULL,
         snapshot_time DATETIME DEFAULT CURRENT_TIMESTAMP
     );

     CREATE TABLE IF NOT EXISTS market_data_cache (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         exchange TEXT NOT NULL,
         symbol TEXT NOT NULL,
         last_price REAL NOT NULL,
         volume_24h REAL,
         updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
     );
     ```

7. **Web Dashboard**
   - `internal/web/server.go` — Go HTTP server on port 8080
   - Serves static files from `/static` (HTML, JS, CSS)
   - REST API endpoints:
     - `GET /api/portfolio` — current portfolio snapshot
     - `GET /api/marketdata/:symbol` — latest market data for symbol
     - `GET /api/marketdata/stream` — SSE stream of market data updates
     - `GET /api/orders/open` — all open orders across exchanges
     - `POST /api/orders` — submit a new order (body: `{exchange, symbol, side, type, price, volume}`)
     - `DELETE /api/orders/:id` — cancel an order
     - `GET /api/health` — health check
   - Dashboard HTML (`/static/index.html`):
     - Header with system name and connection status
     - Portfolio section: balance table per exchange, total equity, unrealized PnL
     - Market Data section: symbol price ticker cards with 24h volume
     - Order Ticket section: form to submit market/limit orders
     - Open Orders section: live table of all open orders with cancel buttons
     - SSE subscription to `/api/marketdata/stream` for real-time price updates
   - Vanilla JS, no framework dependencies

#### Goals

- [ ] All four exchange adapters can fetch market data and account balances
- [ ] Market data updates stream to dashboard in real time via SSE
- [ ] Manual orders can be submitted and seen in the open orders table
- [ ] Portfolio snapshot is visible on dashboard with balance breakdown
- [ ] All order events are persisted to SQLite
- [ ] System starts with single `go run ./cmd/server` command
- [ ] Dashboard accessible at `http://localhost:8080`

#### Acceptance Criteria

1. `GET /api/health` returns `200 OK` with `{"status": "ok"}`
2. `GET /api/portfolio` returns a valid portfolio JSON with balances from all enabled exchanges
3. `GET /api/marketdata/BTCUSDT` returns current price, 24h volume from all exchanges
4. SSE endpoint delivers market data updates at the configured interval
5. `POST /api/orders` with a valid order body creates a real order on the target exchange
6. `GET /api/orders/open` returns open orders from all exchanges
7. `DELETE /api/orders/:id` cancels the order on the correct exchange
8. All order events are written to SQLite `order_events` table
9. Dashboard HTML loads and renders portfolio, market data, and order form
10. Opening `http://localhost:8080` shows a working dashboard with live data

---

### Step 2: LLM Integration

**Purpose:** Add LLM-driven market analysis, strategy engine, and automated trading loop. LLM proposes trades; they go to the collaborative workflow (Step 3) for human approval.

#### Tasks

1. **LLM Client**
   - `internal/llm/client.go` — Anthropic API client using SDK or raw HTTP
   - Supports `AnalyzeMarket` (takes market data bundle, returns structured analysis)
   - Supports `ProposeTrade` (takes analysis, returns a `TradeProposal` struct with symbol, side, price, volume, confidence, reasoning)
   - API key loaded from config/env, not hardcoded
   - Graceful fallback: if LLM is unavailable or returns an error, log and skip (do not block trading)

2. **Strategy Engine**
   - `internal/strategy/engine.go` — orchestration loop
   - Runs on a configurable interval (default: every 5 minutes)
   - Collects current market data → sends to LLM → receives trade proposals
   - Stores proposals in SQLite with status `pending_approval`
   - Does NOT auto-execute — all proposals go to the approval queue

3. **TradeProposal Data Model**
   ```go
   type TradeProposal struct {
       ID           string    `json:"id"`
       Symbol       string    `json:"symbol"`
       Side         string    `json:"side"`         // "buy" or "sell"
       OrderType    string    `json:"order_type"`   // "market" or "limit"
       Price        float64   `json:"price"`
       Volume       float64   `json:"volume"`
       Confidence   float64   `json:"confidence"`   // 0.0-1.0
       Reasoning    string    `json:"reasoning"`
       Exchange     string    `json:"exchange"`
       CreatedAt    time.Time `json:"created_at"`
       Status       string    `json:"status"`       // "pending_approval", "approved", "rejected", "executed", "expired"
   }
   ```

4. **API Extensions**
   - `GET /api/proposals` — list all proposals (filterable by status, symbol)
   - `GET /api/proposals/:id` — get a single proposal
   - `POST /api/proposals/:id/approve` — approve a proposal (transitions to Step 3 workflow)
   - `POST /api/proposals/:id/reject` — reject a proposal

5. **Dashboard Extensions**
   - Proposal list panel: shows pending proposals from LLM with confidence, reasoning, and one-click Approve/Reject buttons
   - LLM status indicator: shows "LLM Active" / "LLM Error" in header

#### Goals

- [ ] LLM client can successfully call Anthropic API and receive structured responses
- [ ] Strategy engine runs on a timer and generates trade proposals
- [ ] Proposals are persisted to SQLite and visible in the dashboard
- [ ] Approving a proposal triggers the collaborative order workflow
- [ ] LLM errors are handled gracefully without crashing the system

#### Acceptance Criteria

1. Strategy engine fires on the configured interval and generates proposals
2. Each proposal has symbol, side, volume, confidence, and reasoning fields
3. Proposals are stored in SQLite with `status = pending_approval`
4. `GET /api/proposals` returns all proposals with correct status filtering
5. `POST /api/proposals/:id/approve` transitions proposal status to `approved`
6. Dashboard shows pending proposals with Approve/Reject buttons
7. If Anthropic API is unreachable, system continues running and logs the error
8. LLM status indicator in dashboard reflects current LLM health state

---

### Step 3: Collaborative Order Workflow

**Purpose:** Implement the human-in-the-loop approval workflow. All orders — manual or LLM-proposed — flow through a unified order lifecycle that requires explicit human action to transition from "proposed" to "sent to exchange."

#### Tasks

1. **Order State Machine**

   ```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
   │   PROPOSED   │────▶│  APPROVED    │────▶│ SUBMITTED    │
   └──────────────┘     └──────────────┘     └──────────────┘
        │                     │                    │
        ▼                     ▼                    ▼
   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
   │  REJECTED    │     │  EXPIRED     │     │   FILLED     │
   └──────────────┘     └──────────────┘     └──────────────┘
                                                   │
                                                   ▼
                                            ┌──────────────┐
                                            │ CANCELLED    │
                                            └──────────────┘
   ```

2. **Collaborative Workflow Engine**
   - `internal/collaborative/workflow.go` — handles state transitions
   - `Proposed → Approved`: human clicks "Approve" in dashboard
   - `Proposed → Rejected`: human clicks "Reject" in dashboard
   - `Approved → Submitted`: system sends order to exchange via OrderManager
   - `Approved → Expired`: if not submitted within a configurable timeout (default: 5 minutes), auto-expire
   - `Submitted → Filled`: exchange reports fill, or OrderManager polls for fill status
   - `Submitted → Cancelled`: human clicks "Cancel" before fill

3. **Human Approval UI**
   - Order ticket form in dashboard submits to `POST /api/orders` which creates a proposal in `Proposed` state
   - For manual orders: user fills order form → immediate submission (skip proposal step) OR proposal flow (configurable)
   - Proposal panel shows pending items with confidence scores, reasoning, and Approve/Reject buttons

4. **Expiration Logic**
   - Background goroutine checks approved-but-not-submitted orders every 30 seconds
   - Orders older than `order_approval_timeout` (default 5 minutes) transition to `Expired`

5. **Notification System**
   - Browser notification or dashboard toast when a new proposal arrives
   - SSE push to dashboard on any order status change

#### Goals

- [ ] All orders (manual and LLM) go through Proposed → Approved → Submitted state machine
- [ ] Human can approve, reject, or cancel orders from the dashboard
- [ ] Approved orders auto-submit to the exchange
- [ ] Expired orders are handled gracefully
- [ ] Order status changes are pushed to the dashboard in real time

#### Acceptance Criteria

1. Manual order submission creates a `Proposed` order visible in the dashboard
2. Clicking "Approve" on a proposed order transitions it to `Approved` and submits to exchange
3. Clicking "Reject" transitions to `Rejected` and logs the rejection reason
4. Approved orders older than 5 minutes auto-expire if not submitted
5. Order status updates are delivered via SSE within 2 seconds of the transition
6. Filled orders update the portfolio snapshot automatically
7. Cancelled orders do not affect portfolio balances
8. Order history is queryable via API

---

### Step 4: Production Hardening

**Purpose:** Make the system deployable, monitorable, and maintainable. Error handling, structured logging, health checks, and configuration management are all finalized.

#### Tasks

1. **Structured Logging**
   - Replace all `fmt.Println` / `log.Printf` with structured logger (e.g., `slog` from Go stdlib or `zerolog`)
   - Log fields: timestamp, level, component, order_id, exchange, symbol, message
   - Log output: JSON to stdout (for container deployment) or plain text (for local terminal)
   - Configurable via `config.yaml`

2. **Error Handling Framework**
   - All exported functions return typed errors
   - Error types: `ExchangeError`, `OrderError`, `ValidationError`, `LLMError`, `DatabaseError`
   - Wrap all errors with context using `fmt.Errorf("exchange/binance: %w", err)`
   - Central error handler in `internal/web/server.go` that returns appropriate HTTP status codes

3. **Health Checks**
   - `GET /api/health` extended with:
     - Exchange connectivity check (ping each enabled exchange)
     - Database connectivity check
     - LLM API availability check (if enabled)
     - Memory/CPU usage
   - Returns `200 OK` only if all critical components are healthy
   - Returns `503 Service Unavailable` with detailed status if any component is down

4. **Configuration Management**
   - All configuration via `config.yaml` — no hardcoded values
   - Config schema:
     ```yaml
     app:
       host: "0.0.0.0"
       port: 8080
       log_level: "info"        # debug, info, warn, error
       log_format: "json"      # json, text

     exchanges:
       binance: { enabled: true, recv_window: 5000 }
       htx: { enabled: true, recv_window: 5000 }
       hyperliquid: { enabled: true }
       bitget: { enabled: true }

     trading:
       order_approval_timeout: 300  # seconds
       market_data_poll_interval: 10  # seconds
       strategy_poll_interval: 300  # seconds

     llm:
       enabled: false
       api_key: ""  # loaded from ANTHROPIC_API_KEY env var if empty here
       model: "claude-sonnet-4-7"
       max_tokens: 1024

     database:
       path: "./data/horizon.db"
     ```

5. **Graceful Shutdown**
   - `go run ./cmd/server` responds to `SIGINT` / `SIGTERM`
   - On shutdown: stop accepting new requests, wait for in-flight orders to complete (timeout: 30s), flush portfolio snapshot to SQLite, then exit

6. **Metrics Endpoint**
   - `GET /api/metrics` — returns Prometheus-format metrics (order count, PnL, latency histograms, error counts)
   - Counter: `horizon_orders_total{exchange, side, status}`
   - Histogram: `horizon_order_fill_latency_seconds`
   - Gauge: `horizon_portfolio_value_usdt`

#### Goals

- [ ] All logs are structured with consistent fields
- [ ] Errors are typed, wrapped, and handled at the right layer
- [ ] Health endpoint returns accurate status of all components
   - [ ] Config is the single source of truth — no hardcoded values in code
   - [ ] Graceful shutdown completes within 30 seconds
   - [ ] Prometheus metrics are exposed and scrapeable

#### Acceptance Criteria

1. Health endpoint returns `200 OK` only when all exchanges respond to ping
2. Health endpoint returns `503` with component-level status when any component fails
3. Logs are JSON-formatted with order_id, exchange, symbol fields on every order operation
4. `GET /api/metrics` returns valid Prometheus text format
5. SIGTERM triggers graceful shutdown: in-flight orders complete, SQLite is flushed
6. All configuration values are loaded from `config.yaml`, none hardcoded
7. Go race detector passes (`go test -race`) on core order manager tests
8. `go vet ./...` reports zero issues

---

### Step 5: Metrics Dashboard Enhancement

**Purpose:** Build the full analytics suite — P&L charts, trade history, performance analytics, and a professional-grade dashboard that provides full situational awareness.

#### Tasks

1. **Trade History Analytics**
   - `GET /api/analytics/trades` — paginated trade history with filters (symbol, exchange, side, date range)
   - `GET /api/analytics/pnl` — P&L summary: realized PnL, unrealized PnL, total PnL, win rate, Sharpe ratio (simplified)
   - `GET /api/analytics/performance` — time-series performance data for charting

2. **P&L Calculation Engine**
   - `internal/metrics/pnl.go` — calculates realized PnL from filled orders (price × volume, accounting for fees)
   - Unrealized PnL calculated from current market price vs. entry price
   - Stores daily P&L snapshots in SQLite for historical charting

3. **Enhanced Dashboard Charts**
   - Equity curve: line chart of portfolio value over time (using Chart.js via CDN or vanilla SVG)
   - P&L bar chart: daily P&L per day for the last 30 days
   - Win rate pie chart: winning vs. losing trades
   - Trade distribution histogram: trade sizes distribution
   - All charts update in real time via SSE or polling

4. **Dashboard Layout Enhancements**
   - Sidebar navigation: Dashboard, Trade History, Analytics, Proposals, Settings
   - Dark mode / light mode toggle
   - Responsive layout for desktop and tablet
   - Connection status indicator per exchange (green/red dot)
   - LLM activity log panel

5. **SQLite Schema Extensions**
   ```sql
   CREATE TABLE IF NOT EXISTS daily_pnl (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       date DATE UNIQUE NOT NULL,
       realized_pnl REAL NOT NULL,
       unrealized_pnl REAL NOT NULL,
       total_pnl REAL NOT NULL,
       trade_count INTEGER NOT NULL,
       win_count INTEGER NOT NULL,
       loss_count INTEGER NOT NULL
   );

   CREATE TABLE IF NOT EXISTS llm_events (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       event_type TEXT NOT NULL,
       proposal_id TEXT,
       raw_response TEXT,
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP
   );
   ```

#### Goals

- [ ] Equity curve shows portfolio value over last 30 days
- [ ] Daily P&L bar chart renders correctly
- [ ] Trade history is filterable and sortable
- [ ] Win rate and Sharpe ratio are calculated and displayed
- [ ] Dashboard is responsive and professional-looking
- [ ] All data persists across server restarts

#### Acceptance Criteria

1. Equity curve renders with at least 30 days of historical data (or fewer if system has been running less time)
2. Daily P&L chart shows correct realized + unrealized breakdown
3. Trade history table supports pagination (20 per page) and filtering by symbol/exchange/date
4. Win rate percentage is displayed accurately based on filled orders
5. Server restart does not lose historical data (SQLite persistence verified)
6. Dashboard is usable on a 1024×768 screen
7. All chart data updates when new orders are filled

---

## 5. Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Language | Go | 1.26.3 |
| Database | SQLite | (via `modernc.org/sqlite` or `github.com/mattn/go-sqlite3`) |
| Web Framework | Go standard library `net/http` | stdlib |
| Dashboard | Vanilla HTML/JS/CSS | — |
| Exchange APIs | REST API calls via `net/http` | stdlib |
| LLM API | Anthropic API | — |
| Configuration | YAML via `gopkg.in/yaml.v3` | v3 |
| Logging | Go `log/slog` | stdlib |
| Metrics | Prometheus client via `github.com/prometheus/client_golang` | v1 |

---

## 6. Data Flow Summary

### Order Flow (Manual)
```
User (Dashboard) → POST /api/orders
  → OrderManager.SubmitOrder()
    → Exchange Adapter (selected by exchange field)
      → Exchange API
        → Filled event stored in SQLite
          → Portfolio Tracker updated
            → SSE pushes update to Dashboard
```

### Order Flow (LLM)
```
Strategy Engine (timer) → Market Data Fetcher
  → LLM Client.AnalyzeMarket()
    → LLM Client.ProposeTrade()
      → TradeProposal stored (status=pending_approval)
        → Dashboard shows proposal

User clicks "Approve" → POST /api/proposals/:id/approve
  → Collaborative Workflow.Approve()
    → OrderManager.SubmitOrder()
      → Exchange API
        → rest of flow same as manual
```

---

## 7. Security Considerations

- API keys stored in `key.txt` (local file, not committed to git)
- API keys loaded into environment variables or config at startup
- No API key logged in plain text anywhere
- All exchange adapters use HTTPS only
- Dashboard does not expose admin functionality beyond order management
- Future: add authentication to dashboard (HTTP basic auth or token-based)

---

## 8. Error Handling Philosophy

| Error Type | Behavior |
|------------|----------|
| Exchange API timeout | Retry 3× with exponential backoff, then log error and return user-facing error |
| Invalid order parameters | Return `400 Bad Request` with specific validation message |
| Insufficient balance | Return `400 Bad Request` with balance info |
| LLM API error | Log and skip strategy cycle; do not affect manual orders |
| Database error | Log error, attempt reconnect, return `500 Internal Server Error` |
| Network failure | Log and retry; circuit breaker after 5 consecutive failures |

---

## 9. File Structure Summary

```
D:/Horizon-Dev/
├── CLAUDE.md
├── key.txt                    # API keys (not committed)
├── docs/
│   └── specs/
│       └── 2026-05-30-trading-platform-design.md
├── horizon/                  # Go module root
│   ├── cmd/
│   │   └── server/
│   │       └── main.go
│   ├── internal/
│   │   ├── config/
│   │   ├── exchange/
│   │   │   ├── adapter.go
│   │   │   ├── registry.go
│   │   │   ├── binance/
│   │   │   ├── htx/
│   │   │   ├── hyperliquid/
│   │   │   └── bitget/
│   │   ├── marketdata/
│   │   ├── ordermanager/
│   │   ├── portfolio/
│   │   ├── llm/
│   │   ├── strategy/
│   │   ├── collaborative/
│   │   ├── metrics/
│   │   ├── database/
│   │   │   └── migrations/
│   │   └── web/
│   │       └── static/
│   ├── configs/
│   │   └── config.yaml
│   └── tests/
│       └── integration/
└── data/                      # SQLite database file (created at runtime)
```

---

## 10. Dependencies (Go Module Packages)

```go
require (
    modernc.org/sqlite v1.x    // SQLite driver (pure Go, no CGO)
    gopkg.in/yaml.v3 v3        // YAML config parsing
    github.com/prometheus/client_golang v1  // Prometheus metrics
   github.com/anthropics/anthropic-sdk-go v0.x  // Anthropic LLM client (Step 2+)
)
```

Note: `modernc.org/sqlite` is used instead of `github.com/mattn/go-sqlite3` to avoid CGO dependencies, making cross-compilation simpler on Windows.

---

## 11. Step Dependency Map

```
Step 1 ─────────────────────────────────────────────────────┐
(Foundation)                                                 │
                                                              ▼
                                                    Step 2 ─────────────────────────────────────────────────────┐
                                                    (LLM Integration)                                                │
                                                                                                                      ▼
                                                                                                            Step 3 ─────────────────┐
                                                                                                            (Collaborative Orders)  │
                                                                                                                      │                  │
                                                                                                                      │                  ▼
                                                                                                                      │           Step 5 ─────────────────┐
                                                                                                                      │           (Metrics Dashboard)     │
                                                                                                                      │                                    │
                                                              ▼                                    │
                                                    Step 4 ◀────────────────────────────────────────────────────┘
                                                    (Production Hardening)
```

**Note:** Step 4 (Production Hardening) can be interleaved with Steps 2/3/5 as each new feature is added. It is listed last for clarity but should be applied continuously — each step's code must pass `go vet` and have structured logging before moving to the next step.

---

## 12. Next Steps After This Design

1. **User reviews this spec** — any sections that need clarification or revision
2. **Once approved**, invoke the `writing-plans` skill to create a detailed implementation plan for **Step 1: Core Foundation + Metrics Dashboard**
3. **Begin Step 1 implementation** — build exchange adapters, market data fetcher, order manager, portfolio tracker, SQLite schema, and web dashboard

---

*Spec written to: `docs/superpowers/specs/2026-05-30-trading-platform-design.md`*