# Step 3: Graduated Autonomy + Risk Guardrails

> **For agentic workers:** This step implements the confidence-based graduated autonomy system. Low-risk + high-confidence proposals execute autonomously without human approval. All other proposals enter the human approval queue (from Step 2).

**Goal:** Enable autonomous execution of LLM-generated proposals meeting configurable confidence and risk-tier thresholds. Implement a multi-layered risk guardrail system evaluating every order before exchange submission. Implement paper trading mode as a global system switch. When guardrails breach, block the order, alert the human, and temporarily downgrade autonomy to collaborative mode.

**Dependencies:** Step 2 MUST be fully complete and passing all acceptance criteria.

---

## File Structure

horizon/
  internal/
    guardrails/ __init__.py evaluator.py rules.py cooldown_tracker.py
    paper/ __init__.py simulator.py
    proposals/ queue.py (MODIFY)
    ordermanager/ manager.py (MODIFY)
    web/ server.py (MODIFY), static/index.html (MODIFY)
    database/migrations/ 003_guardrails_and_paper.sql
  main.py (MODIFY)

---

## Task 1: Database Migration - 003_guardrails_and_paper.sql

Table guardrail_events: id AUTOINCREMENT, order_id TEXT(nullable), proposal_id TEXT, rule_name TEXT NOT NULL (asset_whitelist/cooldown/exchange_exposure/position_size/order_notional/daily_loss_limit), action_taken (blocked/downgrade_autonomy/alert_only), request_symbol/exchange/side TEXT, request_volume/request_price REAL, current_value REAL, threshold_value REAL, downgrade_active INTEGER DEFAULT 0, downgrade_expires_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP. FK order_id->orders(id), proposal_id->proposals(id).

Table cooldown_tracking: id AUTOINCREMENT, exchange TEXT NOT NULL, symbol TEXT NOT NULL, last_trade_at TIMESTAMP, cooldown_seconds INTEGER NOT NULL, UNIQUE(exchange,symbol).

Table daily_pnl: id AUTOINCREMENT, date TEXT NOT NULL UNIQUE(YYYY-MM-DD), realized_pnl REAL DEFAULT 0.0, paper_pnl REAL DEFAULT 0.0, autonomous_trades/manual_trades/llm_proposals INTEGER DEFAULT 0, updated_at TIMESTAMP.

Table paper_trades: id TEXT PRIMARY KEY, proposal_id TEXT(nullable), exchange/symbol/side/order_type TEXT NOT NULL, price/volume/notional_value REAL, status CHECK(pending/filled/expired/cancelled), filled_at TIMESTAMP, paper_pnl REAL DEFAULT 0.0, closed_by_side TEXT, closed_at TIMESTAMP, created_at TIMESTAMP.

Table paper_positions: id AUTOINCREMENT, exchange/symbol/side TEXT NOT NULL, volume/avg_entry_price/current_price/unrealized_pnl REAL, opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(exchange,symbol,side).

Acceptance: All 5 tables created. Unique constraints enforced. NULL order_id/proposal_id in guardrail_events allowed.

---

## Task 2: Cooldown Tracker - internal/guardrails/cooldown_tracker.py

Class CooldownTracker(db: aiosqlite.Connection):
- async record_trade(exchange, symbol, cooldown_seconds): UPSERT into cooldown_tracking, last_trade_at=now
- async is_in_cooldown(exchange, symbol) -> tuple[bool, int|None]: return (True, remaining) if elapsed < cooldown_seconds, else (False, None)
- async get_all_cooldowns() -> list[dict]: all active cooldowns with remaining_seconds computed
- async cleanup_expired(): delete rows where cooldown expired. Run every 5 minutes.

Acceptance: is_in_cooldown correct during/after cooldown. get_all_cooldowns returns active entries.

---

## Task 3: Guardrail Rules Library - internal/guardrails/rules.py

Enum GuardrailAction: BLOCK, BLOCK_AND_DOWNGRADE, ALERT

Dataclass GuardrailRule: name: str, action: GuardrailAction, description: str, async check(request, context) -> (bool, dict)

Six concrete rules, each returning details with current_value and threshold_value:

1. AssetWhitelistRule: symbol in strategy_config.asset_whitelist -> BLOCK_AND_DOWNGRADE
2. CooldownRule: not in cooldown (via cooldown_tracker) -> BLOCK_AND_DOWNGRADE
3. ExchangeExposureRule: (exchange_usdt + order_notional)/total_portfolio_usdt <= max_exchange_exposure_pct -> BLOCK_AND_DOWNGRADE
4. PositionSizeRule: (position_usdt + order_notional)/total_portfolio_usdt <= max_position_pct -> BLOCK_AND_DOWNGRADE
5. OrderNotionalRule: order_min_notional <= notional <= order_max_notional -> BLOCK (not downgrade)
6. DailyLossLimitRule: daily_pnl >= -max_daily_loss_pct * total_portfolio_usdt, ONLY for autonomous orders -> BLOCK_AND_DOWNGRADE

Acceptance: All rules work. DailyLossLimit only for autonomous. All details contain current_value and threshold_value.

---

## Task 4: Risk Guardrail Evaluator - internal/guardrails/evaluator.py

Dataclass GuardrailResult: passed: bool, violated_rules: list[str], blocking: bool, downgrade_autonomy: bool, cooldown_seconds_remaining: int|None, details: list[dict], reason: str

Enum SystemMode: LIVE=liven, PAPER=paper, COLLABORATIVE=collaborative

Class RiskGuardrailEvaluator(db, cooldown_tracker, strategy_config, fetcher):
- async evaluate(request: OrderRequest) -> GuardrailResult: run all 6 rules, collect results, set blocking/downgrade_autonomy, build reason, persist events on breach
- async _persist_guardrail_event(request, result): INSERT into guardrail_events per violated rule, set downgrade_active=1 + downgrade_expires_at=now+30min if downgrade_autonomy
- async get_current_mode() -> SystemMode: query latest downgrade_active=1 guardrail_event, if not expired return COLLABORATIVE, else from strategy_config.mode
- async set_mode(mode: SystemMode): UPDATE strategy_configs mode field
- async can_autonomously_execute(confidence_score, risk_tier, request) -> tuple[bool, str]: checks COLLABORATIVE, autonomy_enabled=0, confidence<threshold, risk_tier>max_risk_tier, PAPER mode. Returns (False, reason) or (True, allowed)
- async evaluate_proposal(proposal): convert to OrderRequest, call evaluate, store result JSON in proposals.guardrail_result

Acceptance: evaluate passes only when all rules pass. BLOCK_AND_DOWNGRADE sets downgrade_autonomy=True. can_autonomously_execute returns False in COLLABORATIVE. Guardrail events persisted. get_current_mode returns COLLABORATIVE during downgrade window.

---

## Task 5: Paper Trading Simulator - internal/paper/simulator.py

Class PaperTradingSimulator(db, fetcher):
- async simulate_market_order(proposal_id, exchange, symbol, side, volume) -> PaperTradeResult: fetch ticker, use ask for buy/bid for sell, notional=price*volume, INSERT paper_trades (status=filled), UPDATE/INSERT paper_positions (weighted avg entry price), UPDATE daily_pnl
- async simulate_limit_order(proposal_id, exchange, symbol, side, price, volume) -> PaperTradeResult: validate buy limit <= market*1.02, sell limit >= market*0.98, execute at limit price
- async close_paper_position(exchange, symbol, side, volume) -> PaperTradeResult: lookup position, compute P&L (long: (close-entry)*vol, short: (entry-close)*vol), UPDATE/DELETE paper_positions, INSERT paper_trades closing record, UPDATE daily_pnl with paper_pnl
- async update_market_prices(): background task, for each paper_positions row: fetch ticker, UPDATE current_price and unrealized_pnl (long: (current-entry)*vol, short: (entry-current)*vol)
- async get_paper_positions() -> list[dict]: all positions with unrealized P&L
- async get_paper_pnl_summary() -> dict: total_realized_pnl, total_unrealized_pnl, open_positions_count, trades_count

Dataclass PaperTradeResult: order_id, exchange, symbol, side, order_type, price, volume, notional_value, status, paper_pnl
Exception PaperTradeError(Exception)

Acceptance: market_order inserts into paper_trades and paper_positions. limit_order validates 2% range. close computes P&L for longs and shorts. update_market_prices updates all positions. No real exchange API calls.

---

## Task 6: Enhanced Proposal Queue with Auto-Execution - proposals/queue.py (MODIFY)

Constructor add: guardrail_evaluator, paper_simulator, mode

- async attempt_auto_execution(proposal: TradeProposal) -> bool:
  1. If status != PROPOSED: return False
  2. can_autonomously_execute check. If False: log reason, return False
  3. evaluate_proposal check. If failed: record breach in guardrail_events, return False
  4. If PAPER: simulate_market/simulate_limit. status=AUTO_EXECUTED, executed_order_id=paper_trade.id. return True
  5. If LIVE: submit_order(source=autonomous, proposal_id). status=AUTO_EXECUTED, executed_order_id=order_result.order_id. return True

- async start_auto_execution_scanner(): every 30s, query PROPOSED proposals, pre-filter by confidence>=threshold AND risk_tier<=max, call attempt_auto_execution. Log: checked N, executed M

- Modified approve(): if get_current_mode() == COLLABORATIVE: raise InvalidSystemModeError

Acceptance: auto-executes qualifying proposals. Below-threshold remains PROPOSED. Guardrail failures recorded. PAPER to simulator, LIVE to order_manager. COLLABORATIVE approve raises error.

---

## Task 7: Enhanced Expiry Scanner - proposals/queue.py (MODIFY)

start_expiry_scanner(): every 60s, query PROPOSED proposals, for each: fetch current price, check time expiry (now > expires_at), check price-drift expiry (abs(current-proposed)/proposed > threshold_pct/100), if expired call _expire(), else if autonomy enabled AND confidence>=threshold AND risk_tier<=max: attempt_auto_execution(). Log: N proposed, M expired, K auto-executed.

Acceptance: time and price-drift expiry work. Valid proposals auto-executed. Non-qualifying remain PROPOSED.

---

## Task 8: Order Manager Guardrail Integration - ordermanager/manager.py (MODIFY)

Constructor add: guardrail_evaluator: RiskGuardrailEvaluator | None = None

submit_order changes: before exchange submission, if guardrail_evaluator not None: evaluate(request). If not passed: record event_type=guardrail_blocked, raise GuardrailBlockedError(result.reason, result). If passed with warnings: log them. If guardrail_evaluator is None: proceed directly (backward compatibility). After success: cooldown_tracker.record_trade(exchange, symbol, cooldown_seconds).

Exception GuardrailBlockedError(Exception): reason: str, result: GuardrailResult, __str__: f"Guardrail blocked: {reason}"

New method async get_guardrail_status() -> dict: calls get_current_mode(), get_all_cooldowns(), queries today guardrail_events count. Returns mode, cooldowns, guardrail_events_today.

Acceptance: guardrail-blocked orders raise exception before reaching exchange. GuardrailBlockedError has full result. order_events records guardrail_blocked. Successful orders record cooldown. Backward compatible.

---

## Task 9: Dashboard UI - web/static/index.html (MODIFY)

1. Guardrail Status Bar (header): Mode badge (LIVE green/PAPER yellow/COLLABORATIVE red+icon). COLLABORATIVE: countdown "expires in Xm". Active cooldown count "N cooldowns" (clickable modal with exchange/symbol/remaining/last_trade).
2. Paper/Live Mode Toggle: Toggle switch, confirmation dialog "Switch to PAPER mode? No real orders will be executed." PAPER: prominent banner "PAPER TRADING - No real funds at risk".
3. Guardrail Breach Log (collapsible): "Guardrail Events" header with count badge. Columns: Time, Rule, Action, Symbol, Details. Color: BLOCK_AND_DOWNGRADE=red, BLOCK=orange. Auto-refresh 10s.
4. Paper Trading Performance Card: Total Realized P&L, Total Unrealized P&L, Open Positions, Total Trades. Open positions table: Symbol/Side/Volume/Avg Entry/Current Price/Unrealized P&L. P&L color: positive=green, negative=red.
5. Auto-Execution Toggle (Proposal Queue header): ON/OFF toggle. POST /api/strategy/config with autonomy_enabled.

JS functions: loadGuardrailStatus(), loadPaperTradingStats(), loadGuardrailEvents(), switchMode(mode), toggleAutonomy(enabled), updateModeIndicator(mode), showCooldownModal(cooldowns).

Acceptance: Mode badge updates real-time. COLLABORATIVE shows countdown. Toggle works. PAPER banner shows. Breach log shows events with colors. Paper positions show P&L with colors. Auto-execution toggle updates config.

---

## Task 10: Guardrail API Endpoints - web/server.py (MODIFY)

GET /api/guardrails/status: returns mode, cooldowns, downgrade_expires_at, guardrail_events_today
GET /api/guardrails/events: query limit(default 50), rule_name(optional filter), returns events list
GET /api/guardrails/cooldowns: returns cooldowns list with remaining seconds
GET /api/paper/positions: returns positions list with unrealized P&L computed
GET /api/paper/trades: query limit(default 100), returns trades list
GET /api/paper/summary: returns total_realized_pnl, total_unrealized_pnl, open_positions, total_trades, daily_pnl

Acceptance: All return correct data. Status shows mode. Events filters by rule_name. Paper positions have computed P&L. All handle empty results gracefully.

---

## Task 11: Application Wiring - main.py (MODIFY)

Startup: create CooldownTracker(db), RiskGuardrailEvaluator(db, cooldown_tracker, strategy_config, fetcher), PaperTradingSimulator(db, fetcher). Pass guardrail_evaluator to OrderManager. Pass guardrail_evaluator and paper_simulator to ProposalQueue. Lifespan startup: proposal_queue.start_auto_execution_scanner(), start paper_simulator.update_market_prices() loop, start cooldown_tracker.cleanup_expired() every 5min. Lifespan shutdown: cancel all new background tasks.

Acceptance: All components wired. Tasks start without errors. Graceful shutdown cancels all tasks cleanly.

---

## Task 12: Build Verification

1. GET /api/guardrails/status -> mode: live
2. POST /api/strategy/mode paper -> mode switches
3. Approve proposal in PAPER -> order in paper_trades, not in orders
4. GET /api/paper/summary -> P&L with realized and unrealized
5. Submit high-confidence(90)+low-risk proposal -> status AUTO_EXECUTED within 30s
6. Submit low-confidence(30) proposal -> status remains PROPOSED
7. Submit high-risk proposal -> status remains PROPOSED
8. Submit two orders same symbol within cooldown -> second blocked, guardrail_events records breach
9. After BLOCK_AND_DOWNGRADE breach -> system enters COLLABORATIVE mode
10. In COLLABORATIVE mode, approve proposal -> returns error
11. Dashboard shows COLLABORATIVE badge with countdown
12. Dashboard shows Paper Trading Performance card
13. Auto-execution toggle works correctly
14. No unhandled exceptions. Server graceful shutdown.

---

## Step 3 Global Acceptance Criteria

| # | Verification | Expected |
|---|---|---|
| 1 | GET /api/guardrails/status | mode: live |
| 2 | POST /api/strategy/mode paper | Mode switches |
| 3 | Approve proposal in PAPER mode | Order in paper_trades |
| 4 | SELECT COUNT(*) FROM paper_trades | Count increases |
| 5 | GET /api/paper/summary | P&L summary |
| 6 | High conf + low risk proposal | AUTO_EXECUTED within 30s |
| 7 | Low confidence proposal | PROPOSED status |
| 8 | High risk proposal | PROPOSED status |
| 9 | Two orders within cooldown | Second blocked, event recorded |
| 10 | SELECT COUNT(*) FROM guardrail_events | Non-zero |
| 11 | After BLOCK_AND_DOWNGRADE breach | COLLABORATIVE mode |
| 12 | Dashboard COLLABORATIVE badge | Visible with countdown |
| 13 | Approve in COLLABORATIVE mode | Error |
| 14 | GET /api/guardrails/events | Breach history |
| 15 | Dashboard paper P&L card | Correct numbers |
| 16 | Auto-execution toggle | Config updates |
| 17 | Graceful shutdown | All tasks cancelled |

---

## Spec Coverage Check

| Requirement | Tasks |
|---|---|
| Guardrail events table | 1 |
| Cooldown tracking table+logic | 1, 2 |
| Daily P&L tracking table | 1 |
| Paper trades table | 1 |
| Paper positions table | 1 |
| 6 guardrail rules | 3 |
| Risk Guardrail Evaluator | 4 |
| SystemMode enum | 4 |
| can_autonomously_execute | 4 |
| get_current_mode | 4 |
| Paper Trading Simulator | 5 |
| Realized+unrealized P&L | 5 |
| Proposal Queue auto-execution | 6 |
| Auto-execution scanner (30s) | 6 |
| Enhanced expiry scanner | 7 |
| Order Manager guardrail integration | 8 |
| GuardrailBlockedError | 8 |
| Guardrail dashboard UI | 9 |
| Paper mode toggle | 9 |
| Guardrail breach log | 9 |
| Paper trading card | 9 |
| Auto-execution toggle | 9 |
| Guardrail API endpoints | 10 |
| Paper API endpoints | 10 |
| Application wiring | 11 |
| Build verification | 12 |

All spec requirements covered. No placeholder gaps.

---

**Plan complete. Proceed to Step 4 only after ALL global acceptance criteria pass.**

---

# Step 4: Enhanced Metrics Dashboard + Strategy Configuration UI

> **For agentic workers:** This step focuses on the presentation and analytics layer. The backend is assumed to be complete from Steps 1-3. This step enhances the dashboard with comprehensive LLM performance analytics, proposal audit trail, trade history, P&L charts, and a full strategy configuration UI. No new backend logic is required — only new API endpoints and a significantly enhanced frontend.

**Goal:** Deliver a professional-grade trading dashboard that provides full transparency into LLM performance, trade history, risk status, and strategy configuration. The dashboard must make it trivial for a human operator to understand what the LLM is doing, why it proposed trades, what guardrails are active, and how the system is performing over time.

**Dependencies:** Steps 1, 2, and 3 MUST be fully complete and passing all acceptance criteria. The dashboard from Step 1 with enhancements from Steps 2 and 3 is the starting point.

---

## File Structure (New/Modified)

```
horizon/
├── internal/
│   ├── web/
│   │   ├── server.py          (MODIFY: add analytics + config endpoints)
│   │   └── static/
│   │       ├── index.html      (MODIFY: complete dashboard overhaul)
│   │       ├── dashboard.css   (NEW: dedicated stylesheet)
│   │       └── dashboard.js    (NEW: dedicated dashboard logic)
│   └── database/migrations/
│       └── 004_analytics_and_audit.sql (NEW: materialized views / summary tables)
└── main.py                    (MODIFY: no new components, verify wiring)
```

---

## Task 1: Analytics Database Layer

**Files:**
- Create: internal/database/migrations/004_analytics_and_audit.sql

**Dependencies:** Steps 1-3 complete.

**Specification:**

004_analytics_and_audit.sql

**Table trade_history_enhanced:**
A denormalized view of all executed trades (both live orders and paper trades) for fast analytics queries. Instead of querying `orders` and `paper_trades` separately, this table provides a unified view.

- id TEXT PRIMARY KEY (from original order or paper_trade id)
- source TEXT NOT NULL (manual/llm_proposal/autonomous)
- is_paper INTEGER NOT NULL DEFAULT 0
- exchange TEXT NOT NULL
- symbol TEXT NOT NULL
- side TEXT NOT NULL (buy/sell)
- order_type TEXT NOT NULL (market/limit)
- price REAL NOT NULL
- volume REAL NOT NULL
- notional_value REAL NOT NULL
- fee REAL DEFAULT 0.0 (estimated as notional * 0.001 for spot)
- executed_at TIMESTAMP NOT NULL
- proposal_id TEXT (nullable, links to proposals table)
- confidence_score INTEGER (nullable, from proposal)
- risk_tier TEXT (nullable, from proposal)
- llm_rationale TEXT (nullable, from proposal)
- created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

**Trigger or background task** (implemented in Python, not SQL trigger): After every order submission or paper trade execution, the TradeHistoryService computes a row in this table. The service queries the original order/paper_trade, joins with proposals where applicable, and inserts or updates the row.

**Table llm_proposal_analytics:**
Aggregated metrics per LLM analysis run.

- id INTEGER PRIMARY KEY (from llm_analysis_history)
- strategy_config_id INTEGER NOT NULL
- triggered_at TIMESTAMP NOT NULL
- completion_status TEXT NOT NULL
- latency_ms INTEGER
- token_count_input INTEGER
- token_count_output INTEGER
- parsed_proposals_count INTEGER DEFAULT 0
- proposals_approved INTEGER DEFAULT 0
- proposals_rejected INTEGER DEFAULT 0
- proposals_expired INTEGER DEFAULT 0
- proposals_auto_executed INTEGER DEFAULT 0
- win_rate_pct REAL (computed from closed proposals within 7 days)
- avg_confidence_score REAL
- avg_risk_tier_score REAL (low=1, medium=2, high=3)
- created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- Foreign key: strategy_config_id -> strategy_configs(id)

**Trigger or background task:** After every LLM analysis completes and after every proposal state transition, update the corresponding llm_proposal_analytics row with current counts.

**Table guardrail_summary:**
Daily summary of guardrail activity for charts.

- id INTEGER PRIMARY KEY AUTOINCREMENT
- date TEXT NOT NULL UNIQUE (YYYY-MM-DD)
- total_breaches INTEGER DEFAULT 0
- total_blocks INTEGER DEFAULT 0
- total_downgrades INTEGER DEFAULT 0
- cooldown_triggers INTEGER DEFAULT 0
- avg_response_time_ms REAL
- top_blocked_rule TEXT (the rule that blocked most frequently this day)
- created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

**Trigger or background task:** A daily aggregation job runs at midnight, computes summaries from guardrail_events, and upserts into guardrail_summary.

**Acceptance Criteria:**
1. trade_history_enhanced provides a unified view of all trades.
2. llm_proposal_analytics computes per-analysis aggregated metrics.
3. guardrail_summary provides daily aggregated guardrail stats.
4. Background tasks (in Python) keep these tables updated.

---

## Task 2: Analytics API Endpoints

**Files:**
- Modify: internal/web/server.py

**Dependencies:** Task 1.

**Specification:**

**GET /api/dashboard/metrics**
Returns a comprehensive dashboard metrics object:
```
{
  "total_portfolio_usdt": float,
  "paper_total_usdt": float,
  "live_realized_pnl_24h": float,
  "paper_realized_pnl_24h": float,
  "live_unrealized_pnl": float,
  "paper_unrealized_pnl": float,
  "total_trades_24h": int,
  "llm_proposals_24h": int,
  "auto_execution_rate_pct": float,
  "avg_confidence_score_7d": float,
  "guardrail_breaches_24h": int,
  "active_cooldowns": int,
  "system_mode": "live|paper|collaborative",
  "autonomy_enabled": bool,
  "next_analysis_at": ISO8601 string,
  "last_analysis_at": ISO8601 string
}
```

**GET /api/dashboard/trade-history**
Query params: source (optional: manual/llm_proposal/autonomous), symbol (optional), side (optional), start_date (optional), end_date (optional), limit (default 50, max 200), offset (default 0).

Returns: {trades: [...], total_count: int, page: int, page_size: int}

Each trade includes: id, source, is_paper, exchange, symbol, side, order_type, price, volume, notional_value, fee, executed_at, proposal_id, confidence_score, risk_tier, llm_rationale (truncated to 200 chars for list view).

**GET /api/dashboard/trade-history/{id}**
Returns full trade details including full llm_rationale (not truncated), full technical_context, and full market_snapshot from the linked proposal.

**GET /api/dashboard/llm-performance**
Query params: period (optional: 7d/30d/90d, default 30d), limit (default 50)

Returns: {performance: [...], summary: {...}}
performance is a list of analysis runs with: triggered_at, completion_status, latency_ms, token_count_input, token_count_output, proposals_count, approved_count, rejected_count, expired_count, auto_executed_count, win_rate_pct, avg_confidence, avg_risk_tier_score.

summary is: {total_analyses: int, avg_latency_ms: float, avg_confidence: float, auto_execution_rate_pct: float, proposals_by_risk_tier: {low: int, medium: int, high: int}, proposals_by_side: {buy: int, sell: int}, total_pnl_realized: float, total_pnl_unrealized: float}

**GET /api/dashboard/pnl-history**
Query params: period (7d/30d/90d), mode (live/paper/all, default all)
Returns daily P&L data for charting:
```
{
  "daily": [
    {date: "2026-05-25", realized_pnl: float, paper_pnl: float, cumulative_pnl: float},
    ...
  ],
  "summary": {
    period_pnl: float,
    best_day: {date, pnl},
    worst_day: {date, pnl},
    win_rate: float,
    sharpe_ratio: float (computed from daily returns)
  }
}
```

**GET /api/dashboard/guardrail-history**
Query params: period (7d/30d/90d, default 30d), limit (default 50)

Returns: {events: [...], summary: {...}}
events: recent guardrail_events with full details.
summary: {total_breaches: int, by_rule: {asset_whitelist: N, cooldown: N, ...}, by_action: {blocked: N, downgrade_autonomy: N}, avg_response_time_ms: float, daily_breach_trend: [{date, count}, ...]}

**GET /api/strategy/config**
Returns active strategy config (existing from Step 2). Adds new fields: `last_analysis_at` and `next_analysis_at`.

**PUT /api/strategy/config**
Updates editable fields (existing from Step 2). Validates all fields strictly.

**POST /api/strategy/config/test**
Accepts a test prompt and calls the LLM with it (does not persist, just tests). Returns the raw response and parsed proposals count. Useful for humans to test prompt effectiveness before updating the strategy config.

**Acceptance Criteria:**
1. /api/dashboard/metrics returns complete dashboard data.
2. /api/dashboard/trade-history supports all filter params and pagination.
3. /api/dashboard/llm-performance computes win_rate and risk tier distribution.
4. /api/dashboard/pnl-history provides daily data for charting with cumulative.
5. /api/dashboard/guardrail-history provides event list and summary.
6. /api/strategy/config includes next_analysis_at and last_analysis_at.
7. /api/strategy/config/test calls LLM and returns response without persisting.

---

## Task 3: Dedicated Dashboard CSS

**Files:**
- Create: internal/web/static/dashboard.css

**Dependencies:** None (frontend only).

**Specification:**

dashboard.css

A comprehensive stylesheet for the trading dashboard. Use CSS custom properties (variables) for the color palette to make theming easy.

**Color Palette (CSS Variables):**
```css
:root {
  --bg-primary: #0f1419;
  --bg-card: #1c1f23;
  --bg-card-hover: #22262b;
  --bg-input: #2f3336;
  --border: #2f3336;
  --border-accent: #3d444d;
  --text-primary: #e7e9ea;
  --text-secondary: #8899a6;
  --text-muted: #536471;
  --accent-blue: #1d9bf0;
  --accent-blue-hover: #1a8cd8;
  --positive: #00c853;
  --negative: #ff1744;
  --warning: #ffab00;
  --collaborative-red: #ff3d3d;
  --paper-yellow: #ffd600;
  --live-green: #00e676;
}
```

**Layout:**
- Responsive grid layout. Min-width 1024px for full layout.
- Cards: background var(--bg-card), border-radius 12px, padding 20px.
- Grid gap: 20px.
- Header: full-width bar with title, mode badge, connection status, cooldown indicator.

**Component Styles:**
- **Mode Badge:** Rounded pill, colored background with matching text (live-green, paper-yellow, collaborative-red).
- **Portfolio Card:** Table with zebra striping, sortable columns.
- **Market Data Cards:** 4-column grid of ticker cards, price in large font, volume in small secondary font.
- **Order Ticket:** Form with labeled inputs, styled selects, prominent submit button.
- **Proposal Queue Card:** Full-width table with color-coded status column, action buttons with hover states.
- **Guardrail Status Bar:** Compact horizontal bar with mode, cooldowns, breach count.
- **Analytics Charts:** Placeholder sections for P&L line chart, trade distribution pie chart. (Charts implemented in Task 4 — CSS just provides container styling).

**Typography:**
- Font family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif.
- Headings: 14px uppercase letter-spaced labels.
- Body: 14px regular.
- Numbers: tabular-nums for alignment.

**Acceptance Criteria:**
1. All cards have consistent padding and border-radius.
2. Mode badge colors match system modes.
3. Positive/negative P&L numbers are colored green/red respectively.
4. Responsive layout works at 1024px and above.
5. All interactive elements have hover states.
6. CSS variables enable easy theme changes.

---

## Task 4: Dedicated Dashboard JavaScript

**Files:**
- Create: internal/web/static/dashboard.js

**Dependencies:** Task 3, Task 2.

**Specification:**

dashboard.js

A comprehensive JavaScript module for the trading dashboard. All code must be encapsulated in a single `HorizonDashboard` class. This class manages all data fetching, UI updates, and event handling.

**Constructor:**
`constructor(apiBase: string)` — sets API base URL. Initializes all DOM element references. Starts all polling intervals.

**Public Methods:**
- `init()` — called once on page load. Fetches initial data for all sections. Sets up all event listeners.
- `destroy()` — called on page unload. Cancels all polling intervals, closes SSE connection.

**Private Methods (prefixed with _):**

**_fetchMetrics()` — calls GET /api/dashboard/metrics. Updates all metric displays.
**_fetchPortfolio()` — calls GET /api/portfolio. Updates portfolio table and total.
**_fetchMarketData()` — calls GET /api/market-data/BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT in parallel. Updates ticker cards.
**_fetchProposals()` — calls GET /api/proposals. Updates proposal queue table and badges.
**_fetchLLMHistory()` — calls GET /api/llm/history. Updates LLM analysis history table.
**_fetchTradeHistory(params)` — calls GET /api/dashboard/trade-history with filters. Updates trade history table.
**_fetchPnLHistory(period)` — calls GET /api/dashboard/pnl-history. Prepares data for charting.
**_fetchGuardrailHistory()` — calls GET /api/dashboard/guardrail-history. Updates guardrail events log.
**_fetchStrategyConfig()` — calls GET /api/strategy/config. Populates strategy config form.

**_renderMetrics(data)` — updates the metrics card with all values. Formats currency and percentages.
**_renderPortfolio(data)` — rebuilds the portfolio table with current balances.
**_renderMarketData(tickers)` — updates all ticker card prices and volumes.
**_renderProposals(proposals)` — rebuilds the proposal queue table. Adds event listeners to Approve/Reject buttons.
**_renderTradeHistory(trades)` — rebuilds the trade history table with pagination controls.
**_renderPnLChart(data)` — renders a line chart using Canvas API (no external chart library required). X-axis: dates, Y-axis: cumulative P&L. Two lines: live (solid blue) and paper (dashed yellow).
**_renderGuardrailEvents(events)` — rebuilds the guardrail breach log table.
**_renderStrategyConfig(config)` — populates all form fields.

**_startPolling()` — starts intervals:
- /api/dashboard/metrics: 10s
- /api/portfolio: 10s
- /api/proposals: 10s
- /api/market-data/stream (SSE): continuous
- /api/dashboard/trade-history: 30s
- /api/dashboard/llm-performance: 60s

**_connectSSE()` — opens SSE connection to /api/market-data/stream. Parses incoming events and updates ticker cards in real-time.

**_onApproveClick(proposalId)` — calls POST /api/proposals/{id}/approve. Refreshes proposal list on success. Shows error on failure.
**_onRejectClick(proposalId, reason)` — calls POST /api/proposals/{id}/reject. Refreshes proposal list on success.
**_onSwitchMode(mode)` — calls POST /api/strategy/mode. Updates mode badge and banner.
**_onToggleAutonomy(enabled)` — calls PUT /api/strategy/config with autonomy_enabled. Updates toggle state.
**_onStrategyConfigSubmit(formData)` — calls PUT /api/strategy/config with form data. Shows save confirmation.

**Event Listeners:**
- Order form submit: prevents default, calls API, shows alert on success/failure.
- Mode toggle: confirmation dialog, then _onSwitchMode.
- Autonomy toggle: immediate PUT request.
- Strategy config form submit: validation, then _onStrategyConfigSubmit.
- Proposal Approve button: shows modal with full details, then _onApproveClick.
- Proposal Reject button: shows modal with reason input, then _onRejectClick.
- Trade history filter changes: calls _fetchTradeHistory with new params.
- P&L chart period toggle (7d/30d/90d): calls _fetchPnLHistory with new period.

**Acceptance Criteria:**
1. HorizonDashboard class encapsulates all dashboard logic.
2. init() fetches and renders all sections on page load.
3. destroy() cancels all polling and closes SSE.
4. All API calls are made via fetch() with proper error handling.
5. P&L chart renders using Canvas API with no external dependencies.
6. All interactive elements have event listeners.
7. Polling intervals are tracked and cancelled on destroy.
8. SSE reconnection logic handles disconnection gracefully.

---

## Task 5: Dashboard HTML Overhaul

**Files:**
- Modify: internal/web/static/index.html

**Dependencies:** Task 3, Task 4.

**Specification:**

Replace the current index.html with a comprehensive single-page dashboard that includes all sections from Steps 1-3 plus the new analytics sections.

**Page Structure:**

```
<body>
  <div class="dashboard">
    <!-- Header -->
    <header>
      <h1>Horizon Trading Platform</h1>
      <div class="header-controls">
        <span id="modeBadge" class="mode-badge mode-live">LIVE</span>
        <span id="connectionStatus" class="status-dot green"></span>
        <span id="cooldownBadge" class="cooldown-badge" style="display:none">0 cooldowns</span>
        <label class="mode-toggle">
          <input type="checkbox" id="paperLiveToggle">
          <span>PAPER</span>
        </label>
      </div>
    </header>

    <!-- System Alert Banner (PAPER mode only) -->
    <div id="paperBanner" class="paper-banner" style="display:none">
      PAPER TRADING — No real funds at risk
    </div>

    <!-- Collaborative Warning Banner -->
    <div id="collaborativeBanner" class="collaborative-banner" style="display:none">
      <span id="collaborativeMessage">Autonomy downgraded — all proposals require manual approval</span>
      <span id="countdownTimer"></span>
    </div>

    <!-- Dashboard Metrics Row -->
    <div class="metrics-row">
      <div class="metric-card">
        <div class="metric-label">Live Portfolio</div>
        <div class="metric-value" id="livePortfolio">--</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Paper Portfolio</div>
        <div class="metric-value" id="paperPortfolio">--</div>
      </div>
      <div class="metric-card positive">
        <div class="metric-label">Live P&L 24h</div>
        <div class="metric-value" id="livePnL24h">--</div>
      </div>
      <div class="metric-card positive">
        <div class="metric-label">Paper P&L 24h</div>
        <div class="metric-value" id="paperPnL24h">--</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Proposals Today</div>
        <div class="metric-value" id="proposalsToday">--</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Auto-Exec Rate</div>
        <div class="metric-value" id="autoExecRate">--</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Guardrail Breaches 24h</div>
        <div class="metric-value" id="breaches24h">--</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Next LLM Analysis</div>
        <div class="metric-value" id="nextAnalysis">--</div>
      </div>
    </div>

    <!-- Main Grid -->
    <div class="main-grid">
      <!-- Column 1: Portfolio + Market Data -->
      <div class="grid-column">
        <!-- Portfolio Card -->
        <div class="card" id="portfolioCard">
          <h2>Portfolio</h2>
          <table class="portfolio-table">
            <thead><tr><th>Exchange</th><th>Asset</th><th>Free</th><th>Locked</th><th>USDT</th></tr></thead>
            <tbody id="portfolioBody"></tbody>
          </table>
          <div class="card-footer">Total: <span id="portfolioTotal">$0.00</span></div>
        </div>

        <!-- Market Data Card -->
        <div class="card" id="marketDataCard">
          <h2>Market Data</h2>
          <div class="ticker-grid" id="tickerGrid">
            <!-- BTC, ETH, SOL, BNB ticker cards -->
          </div>
        </div>
      </div>

      <!-- Column 2: Order Ticket + Open Orders -->
      <div class="grid-column">
        <!-- Order Ticket Card -->
        <div class="card" id="orderTicketCard">
          <h2>Order Ticket</h2>
          <form class="order-form" id="orderForm">
            <!-- Exchange, Symbol, Side, Type, Price, Volume inputs -->
          </form>
        </div>

        <!-- Open Orders Card -->
        <div class="card" id="openOrdersCard">
          <h2>Open Orders</h2>
          <table id="openOrdersTable"><thead>...</thead><tbody>...</tbody></table>
        </div>
      </div>

      <!-- Column 3: Proposal Queue -->
      <div class="grid-column full-width">
        <div class="card" id="proposalQueueCard">
          <div class="card-header">
            <h2>LLM Proposals</h2>
            <div class="card-controls">
              <label class="auto-exec-toggle">
                <input type="checkbox" id="autoExecToggle">
                <span>Auto-Execution</span>
              </label>
              <span class="badge" id="proposalCount">0</span>
            </div>
          </div>
          <table id="proposalsTable"><thead>...</thead><tbody>...</tbody></table>
        </div>
      </div>

      <!-- Column 4: P&L Chart + Trade History -->
      <div class="grid-column full-width">
        <!-- P&L Chart Card -->
        <div class="card" id="pnlChartCard">
          <div class="card-header">
            <h2>P&L History</h2>
            <div class="period-toggle">
              <button class="period-btn" data-period="7d">7D</button>
              <button class="period-btn active" data-period="30d">30D</button>
              <button class="period-btn" data-period="90d">90D</button>
            </div>
          </div>
          <div class="chart-container">
            <canvas id="pnlChart" width="800" height="200"></canvas>
          </div>
          <div class="pnl-summary">
            <span>Period P&L: <span id="periodPnL">--</span></span>
            <span>Best Day: <span id="bestDay">--</span></span>
            <span>Worst Day: <span id="worstDay">--</span></span>
            <span>Win Rate: <span id="winRate">--</span></span>
          </div>
        </div>

        <!-- Trade History Card -->
        <div class="card" id="tradeHistoryCard">
          <div class="card-header">
            <h2>Trade History</h2>
            <div class="filter-bar">
              <select id="tradeSourceFilter"><option value="">All Sources</option><option>manual</option><option>llm_proposal</option><option>autonomous</option></select>
              <select id="tradeSymbolFilter"><option value="">All Symbols</option></select>
              <select id="tradeSideFilter"><option value="">All Sides</option><option>buy</option><option>sell</option></select>
            </div>
          </div>
          <table id="tradeHistoryTable"><thead>...</thead><tbody>...</tbody></table>
          <div class="pagination" id="tradePagination"></div>
        </div>
      </div>

      <!-- Column 5: LLM Performance + Guardrail Events -->
      <div class="grid-column full-width">
        <!-- LLM Performance Card -->
        <div class="card" id="llmPerformanceCard">
          <div class="card-header">
            <h2>LLM Performance</h2>
            <span class="badge" id="llmHistoryCount">0 analyses</span>
          </div>
          <table id="llmHistoryTable"><thead>...</thead><tbody>...</tbody></table>
        </div>

        <!-- Guardrail Events Card -->
        <div class="card" id="guardrailEventsCard">
          <div class="card-header">
            <h2>Guardrail Events</h2>
            <span class="badge" id="guardrailCount">0</span>
          </div>
          <table id="guardrailEventsTable"><thead>...</thead><tbody>...</tbody></table>
        </div>
      </div>

      <!-- Column 6: Strategy Configuration -->
      <div class="grid-column full-width">
        <div class="card" id="strategyConfigCard">
          <div class="card-header">
            <h2>Strategy Configuration</h2>
            <button id="testPromptBtn" class="btn-secondary">Test Prompt</button>
          </div>
          <form class="strategy-config-form" id="strategyConfigForm">
            <!-- Autonomy settings, guardrail thresholds, whitelist, system prompt -->
          </form>
        </div>
      </div>
    </div>
  </div>
</body>
```

The HTML must load: dashboard.css, dashboard.js. Initialize with `HorizonDashboard.init()`.

**Acceptance Criteria:**
1. All sections visible and properly laid out.
2. Mode badge and toggle work.
3. Paper banner shows when in paper mode.
4. Collaborative banner shows when in collaborative mode.
5. Metrics row shows all 8 metrics.
6. P&L chart canvas is present and sized correctly.
7. Trade history table has filter controls.
8. Strategy config form has all fields.
9. HorizonDashboard.init() is called on page load.

---

## Task 6: Cooldown Modal

**Files:**
- Create: internal/web/static/cooldown-modal.js (or embed in dashboard.js)

**Dependencies:** Task 4.

**Specification:**

A modal dialog that shows all active cooldowns when the user clicks the cooldown badge.

**Modal content:**
- Header: "Active Cooldowns" with close button.
- Body: table with columns: Exchange, Symbol, Remaining Time, Last Trade Time.
- Footer: "Clear All" button (optional, admin function).

**Implementation:**
- When cooldown badge is clicked, show the modal.
- Modal content is populated from the cooldown tracker data fetched via /api/guardrails/cooldowns.
- Each row shows remaining time as both seconds and human-readable (e.g., "4m 32s").
- Timer updates every second to show live countdown.
- Close button or clicking outside the modal dismisses it.

**Acceptance Criteria:**
1. Modal opens on cooldown badge click.
2. All active cooldowns shown with correct remaining times.
3. Timer updates live every second.
4. Modal closes on close button or outside click.

---

## Task 7: Proposal Approval Modal

**Files:**
- Create: internal/web/static/proposal-modal.js (or embed in dashboard.js)

**Dependencies:** Task 4.

**Specification:**

A detailed modal for approving proposals.

**Modal content:**
- Header: "Approve Proposal" with proposal ID (truncated).
- Body sections:
  1. **Order Details:** Exchange, Symbol, Side (color-coded), Type, Volume, Price.
  2. **LLM Rationale:** Full text from llm_rationale field. Scrollable if long.
  3. **Technical Context:** RSI, MACD, EMA values at proposal time. Formatted as key-value pairs.
  4. **Market Snapshot:** Prices across exchanges at proposal time. Comparison to current price.
  5. **Guardrail Check Result:** Which rules passed/failed. Highlight any warnings.
  6. **Confidence & Risk:** Score (0-100) and tier (low/medium/high) displayed prominently.
- Footer: "Confirm Approval" (green) and "Cancel" buttons.

**Implementation:**
- Show modal when Approve button is clicked on a proposal row.
- Populate modal with all proposal details fetched from /api/proposals/{id}.
- "Confirm Approval" calls /api/proposals/{id}/approve.
- "Cancel" dismisses modal.

**Acceptance Criteria:**
1. Modal shows full proposal details including full LLM rationale.
2. Technical context and market snapshot are readable.
3. Guardrail result shows pass/fail for each rule.
4. Confirm triggers API call and updates proposal list on success.

---

## Task 8: Build Verification

**Files:** None (verification task).

**Dependencies:** Tasks 1-7 complete.

**Verification sequence:**

1. Dashboard loads at http://localhost:8080/ with all sections visible.
2. Mode badge shows correct state (live/paper/collaborative).
3. Metrics row shows 8 values (all non-"--" after initial load).
4. Portfolio table populates with exchange/asset balances.
5. Market data ticker cards show prices updating via SSE.
6. Proposal queue shows all proposals with correct status colors.
7. Clicking "Approve" on a proposal opens the detailed modal.
8. P&L chart renders on the Canvas with two lines (live and paper).
9. Trade history table shows filters and pagination.
10. LLM performance table shows analysis history.
11. Guardrail events table shows breach history.
12. Strategy config form is populated with current values.
13. Test Prompt button calls /api/strategy/config/test and shows response.
14. Paper/Live toggle switches mode and shows/hides appropriate banners.
15. Auto-execution toggle updates strategy config.
16. Cooldown modal shows active cooldowns with live countdown.
17. No console errors during normal operation.

---

## Step 4 Global Acceptance Criteria

| # | Verification | Expected |
|---|---|---|
| 1 | Dashboard loads all sections | Visible |
| 2 | Mode badge matches system state | Correct color and label |
| 3 | 8 metrics all populated | Non-"--" values |
| 4 | Portfolio table populated | Exchange/asset rows |
| 5 | Market data updates via SSE | Prices change in real-time |
| 6 | Proposal queue shows all proposals | Table populated |
| 7 | Approve modal shows full details | LLM rationale + tech context |
| 8 | P&L chart renders | Canvas with 2 lines |
| 9 | Trade history filters work | Filtering changes results |
| 10 | LLM performance shows history | Table populated |
| 11 | Guardrail events show history | Table populated |
| 12 | Strategy config form populated | All fields filled |
| 13 | Test Prompt button works | Shows LLM response |
| 14 | Paper/Live toggle works | Mode changes, banners show |
| 15 | Auto-execution toggle works | Config updates |
| 16 | Cooldown modal opens and updates | Live countdown |
| 17 | No console errors | Clean |
| 18 | Server graceful shutdown | All tasks cancelled |

---

## Spec Coverage Check

| Requirement | Tasks |
|---|---|
| Analytics database layer | 1 |
| Trade history enhanced table | 1 |
| LLM proposal analytics table | 1 |
| Guardrail summary table | 1 |
| Dashboard metrics endpoint | 2 |
| Trade history endpoint | 2 |
| LLM performance endpoint | 2 |
| P&L history endpoint | 2 |
| Guardrail history endpoint | 2 |
| Strategy config test endpoint | 2 |
| Dedicated dashboard CSS | 3 |
| Color palette and component styles | 3 |
| Dedicated dashboard JavaScript | 4 |
| HorizonDashboard class | 4 |
| P&L Canvas chart rendering | 4 |
| SSE connection management | 4 |
| Dashboard HTML overhaul | 5 |
| Metrics row (8 metrics) | 5 |
| P&L chart card | 5 |
| Trade history card with filters | 5 |
| LLM performance card | 5 |
| Guardrail events card | 5 |
| Strategy config card | 5 |
| Cooldown modal | 6 |
| Proposal approval modal | 7 |
| Build verification | 8 |

All spec requirements covered. No placeholder gaps.

---

**Plan complete. This is the final step. After ALL acceptance criteria pass, the Horizon Trading Platform is complete.**

