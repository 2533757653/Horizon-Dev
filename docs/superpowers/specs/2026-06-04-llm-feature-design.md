# LLM Frontend + Backend Feature — Design Document

> **Status:** Approved (brainstorming complete 2026-06-04)
> **Workflow Chain:** CLAUDE.md §4.2 Major Module (Minor Version)
> **Branch strategy:** `feature/llm-frontend-backend` semantically; physically committed on `dev` (worktree skipped per user authorization)
> **Next step:** `writing-plans` → `subagent-driven-development`

---

## §1. Scope & Workflow Exception

**Goal:** Promote Horizon's LLM capability from "code-complete-but-never-running" to a fully online dual-mode UI: autonomous trading analysis + human-initiated co-pilot consultation. Two parallel subagents (Backend, Frontend) will deliver one cohesive commit.

**Two Operating Modes:**

| Mode | Initiator | Core Action | Execution |
|---|---|---|---|
| 🤖 **Autonomous** | System (8h cron + manual button) | LLM scans market → generates **open / close / reduce** proposals → guardrails → auto-exec (if `autonomy_enabled`) or queued | System |
| 🤝 **Co-Pilot (人机协作)** | User in chat box | Multi-turn dialogue, LLM sees market+indicators+positions+session history → reasons → when a concrete trade emerges, attaches `[Pre-fill Order]` button | Human manually `Submit` via existing order ticket |

**Workflow Exception Log (CLAUDE.md §5.1):**

> User authorized **skipping the mandatory Worktree** for this Major Module. Mitigation:
> - Git `stash` taken before development begins
> - Naming convention preserved: feature group `llm-frontend-backend`
> - Closure still uses `finishing-a-development-branch` skill with full `pytest` gate

---

## §2. Architecture — Approach B (Dual-Engine Independent Modules)

Chosen over (A) shared engine with mode-branching and (C) plugin architecture. Rationale: clean separation of concerns, isolated state lifecycles, independent prompt evolution, clear ownership boundary for two parallel agents.

```
┌─────────────────────────────────────────────────────────┐
│                 FRONTEND (Frontend Agent)                │
│  ┌────────────────┐ ┌─────────────┐ ┌─────────────────┐ │
│  │ LLM Proposals  │ │  AI Co-Pilot│ │ Strategy Config │ │
│  │  Queue Panel   │ │  Chat Panel │ │     Modal       │ │
│  └───────┬────────┘ └──────┬──────┘ └────────┬────────┘ │
│  ┌───────▼─────────────────▼─────────────────▼────────┐ │
│  │   api.js (extended)  ─  proposals.js / copilot.js  │ │
│  │                          strategy_config.js        │ │
│  └────────────────────────────┬───────────────────────┘ │
└───────────────────────────────┼─────────────────────────┘
                                │ HTTP/JSON + SSE
┌───────────────────────────────▼─────────────────────────┐
│                  BACKEND (Backend Agent)                 │
│  ┌────────────────────────────────────────────────────┐ │
│  │           server.py  (+9 new endpoints)             │ │
│  └────────┬───────────────────────────┬───────────────┘ │
│           │                           │                 │
│  ┌────────▼─────────┐         ┌───────▼──────────────┐  │
│  │ ProposalQueue    │         │ CoPilotEngine (NEW)  │  │
│  │ (existing)       │         │ multi-turn + context │  │
│  └────────┬─────────┘         └───────┬──────────────┘  │
│  ┌────────▼─────────────┐             │                 │
│  │ LLMStrategyEngine    │             │                 │
│  │ + position mgmt ext  │             │                 │
│  └────────┬─────────────┘             │                 │
│  ┌────────▼───────────────────────────▼──────────────┐  │
│  │     AnthropicClient (shared, MiniMax endpoint)    │  │
│  └────────┬──────────────────────────────────────────┘  │
│           │                                             │
│  ┌────────▼──────────┐    ┌──────────────────────────┐  │
│  │ LLMScheduler (NEW)│    │ Conversation Store (NEW) │  │
│  │ asyncio loop 8h   │    │ SQLite: copilot_sessions │  │
│  └───────────────────┘    │         copilot_messages │  │
│                           └──────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## §3. Backend Detailed Design

### §3.1 New / Modified Files

```
horizon/internal/llm/
  client.py            (NEW) — AsyncAnthropic factory from key.txt
  scheduler.py         (NEW) — asyncio 8h periodic loop
  copilot.py           (NEW) — multi-turn chat engine + context loader
  copilot_prompt.py    (NEW) — conversational system prompt
  engine.py            (MODIFY) — see §3.4 position-mgmt extension
  prompt_builder.py    (MODIFY) — include positions context, ask about close/adjust
  parser.py            (MODIFY) — accept action_type field

horizon/internal/proposals/
  models.py            (MODIFY) — add action_type to TradeProposal

horizon/internal/database/migrations/
  005_copilot.sql      (NEW)  — copilot_sessions, copilot_messages tables

horizon/internal/web/
  server.py            (MODIFY) — +9 endpoints (see §3.6)

horizon/
  main.py              (MODIFY) — wire LLMClient, Engine, Scheduler, CoPilot, expiry scanner

.gitignore             (NEW)  — root-level, ignore key.txt, data/, *.db, .venv/, __pycache__/
```

### §3.2 `llm/client.py` — Shared Anthropic Client

```python
from anthropic import AsyncAnthropic
import json, os, pathlib

def load_llm_credentials() -> tuple[str, str, str]:
    """Returns (auth_token, base_url, default_model). Reads key.txt by preference,
    falls back to env vars (ANTHROPIC_AUTH_TOKEN/_BASE_URL)."""
    # parse key.txt for ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL,
    # ANTHROPIC_DEFAULT_SONNET_MODEL — those exact keys
    # raise RuntimeError if missing

def build_anthropic_client() -> AsyncAnthropic:
    token, base_url, _model = load_llm_credentials()
    return AsyncAnthropic(auth_token=token, base_url=base_url)
```

Notes:
- Parse `key.txt` tolerantly (the file is non-strict JSON; use line-based regex)
- Model name discovered separately by callers (`load_llm_credentials()[2]`)
- No secret logging anywhere
- Env vars override file (12-factor)

### §3.3 `llm/scheduler.py` — 8h Trigger

Simple `asyncio.create_task` loop (no APScheduler dependency added):
```python
class LLMScheduler:
    def __init__(self, engine: LLMStrategyEngine, interval_seconds: int = 28800):
        self._engine = engine
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self): ...  # spawn loop, immediate first run if no run today
    async def stop(self):  ...  # set event + await task
    async def trigger_now(self) -> LLMProposalOutput: ...  # manual button hook
```

### §3.4 `llm/engine.py` Extension — Position Management

Three concrete additions:
1. `_gather_portfolio_context()` already exists — augment its returned dict with explicit `open_positions_with_entry: list[{symbol, side, volume, entry_price_estimate}]` so the LLM can reason about *what's already there*.
2. `PromptBuilder.build()` instruction section adds: *"You may also recommend closing or reducing existing positions. Use `action_type: open|close|reduce`. For close/reduce, `side` must be the closing side (opposite of held)."*
3. `parser.py` accepts `action_type` (default `"open"` if missing); validates `close`/`reduce` only when symbol exists in portfolio.

`ProposalQueue.attempt_auto_execution` already calls `OrderManager` / `PaperSimulator`; no change needed — a "close" proposal is just an order in the opposing direction with reduce semantics. (Paper simulator `close_paper_position` exists; live mode goes through OrderManager normally.)

### §3.5 `llm/copilot.py` — Multi-Turn Co-Pilot Engine

```python
class CoPilotEngine:
    def __init__(self, db, anthropic_client, registry, fetcher,
                 indicator_calculator, portfolio_tracker, model_name): ...

    async def create_session(self, title: str | None = None) -> CoPilotSession
    async def list_sessions(self, limit=20) -> list[CoPilotSession]
    async def get_messages(self, session_id: str) -> list[CoPilotMessage]
    async def send_message(self, session_id: str, user_text: str) -> CoPilotReply
    async def delete_session(self, session_id: str) -> None

@dataclass
class CoPilotReply:
    message_id: str
    assistant_text: str
    trade_suggestion: dict | None  # if present, frontend renders [Pre-fill Order] button
    context_summary: dict          # what data was fed (for transparency)
```

Behavior of `send_message`:
1. Append user message → DB
2. Build prompt: system prompt (conversational style) + cross-session context (last N messages from THIS session only — cross-session means the *same session* persisting; not all sessions) + live market context (tickers + indicators) + portfolio snapshot
3. Call AsyncAnthropic with **structured-output tool** that asks LLM to optionally emit a `trade_suggestion` JSON object alongside its text
4. Persist assistant message + parsed trade_suggestion
5. Return CoPilotReply

Token control: trim oldest messages once total prompt exceeds 100k chars (well within 200k context window of MiniMax-M3).

### §3.6 New REST Endpoints (server.py)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/proposals?status=&limit=` | List proposals |
| GET | `/api/proposals/{id}` | Detail w/ full rationale + market snapshot + tech ctx |
| POST | `/api/proposals/{id}/approve` | body `{approved_by}` → executes via queue |
| POST | `/api/proposals/{id}/reject` | body `{rejected_by, reason}` |
| GET | `/api/proposals/stats` | counts by status |
| GET | `/api/llm/history?limit=` | analysis history |
| POST | `/api/llm/trigger` | manual "Analyze Now" |
| GET | `/api/strategy/config` | (read endpoint; only PUT exists today) |
| POST | `/api/copilot/sessions` | body `{title?}` → new session |
| GET | `/api/copilot/sessions?limit=` | list |
| GET | `/api/copilot/sessions/{id}/messages` | history |
| POST | `/api/copilot/sessions/{id}/messages` | body `{text}` → reply |
| DELETE | `/api/copilot/sessions/{id}` | delete |

All endpoints use existing `CustomJSONResponse` for Decimal handling. Pydantic validation on every body. 404 on missing IDs; 400 on invalid state; 410 on expired proposal.

### §3.7 DB Migration `005_copilot.sql`

```sql
CREATE TABLE IF NOT EXISTS copilot_sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS copilot_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    trade_suggestion TEXT,        -- JSON, nullable
    context_summary TEXT,         -- JSON, nullable
    token_count_input INTEGER,
    token_count_output INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES copilot_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_copilot_messages_session
  ON copilot_messages(session_id, created_at);
```

Plus an additive column on `proposals`:
```sql
ALTER TABLE proposals ADD COLUMN action_type TEXT NOT NULL DEFAULT 'open'
  CHECK(action_type IN ('open','close','reduce'));
```

### §3.8 `main.py` Wiring

After existing `strategy_config` load, add:
```python
# 5.5 LLM stack
from horizon.internal.llm.client import build_anthropic_client, load_llm_credentials
_, _, llm_model = load_llm_credentials()
anthropic_client = build_anthropic_client()

indicator_calculator = TechnicalIndicatorCalculator(db)
llm_engine = LLMStrategyEngine(
    db=db, registry=registry, proposal_queue=proposal_queue,
    anthropic_client=anthropic_client, strategy_config=strategy_config_dict,
    indicator_calculator=indicator_calculator, fetcher=fetcher,
)
llm_scheduler = LLMScheduler(llm_engine, interval_seconds=8 * 3600)
copilot = CoPilotEngine(db=db, anthropic_client=anthropic_client,
                         registry=registry, fetcher=fetcher,
                         indicator_calculator=indicator_calculator,
                         portfolio_tracker=portfolio_tracker,
                         model_name=llm_model)

application.state.llm_engine = llm_engine
application.state.llm_scheduler = llm_scheduler
application.state.copilot = copilot

# Start expiry scanner (was missing!)
await proposal_queue.start_expiry_scanner()
await llm_scheduler.start()
```

Shutdown: stop scheduler before closing DB.

---

## §4. Frontend Detailed Design

### §4.1 New / Modified Files

```
horizon/internal/web/static/
  js/proposals.js          (NEW)
  js/copilot.js            (NEW)
  js/strategy_config.js    (NEW)
  js/api.js                (MODIFY: +14 export funcs)
  js/app.js                (MODIFY: init new modules)
  index.html               (MODIFY: +3 panel containers)
  css/styles.css           (MODIFY: +panel styles)
```

### §4.2 Panel 1 — LLM Proposals Queue

Placement: full-width row beneath existing Guardrail Events panel.

Layout: table — `ID(8) | Symbol | Action | Side | Volume | Confidence | Risk | Status | Created | [Actions]`

- Status colors: `proposed` blue · `auto_executed` green · `executed` green · `rejected` gray · `expired` orange
- Action buttons (only when `status='proposed'`): `[Approve]` `[Reject]`
- Header controls: `[Run Analysis Now]` button (POST /api/llm/trigger), proposal count badge
- Auto-refresh every 10s via existing setInterval pattern
- Approve modal: shows full rationale, technical_context (RSI/MACD/EMA), market_snapshot prices, proposed_price vs current_price drift %, guardrail_result if present
- Reject modal: optional reason textarea
- Action-type icon: 🟢 open / 🔴 close / 🟡 reduce

### §4.3 Panel 2 — AI Co-Pilot Chat

Placement: collapsible floating panel, default minimized to bottom-right corner. Click to expand.

Components:
- Session selector dropdown (last 10 sessions) + `[+ New]` button
- Message list (scroll, auto-scroll to bottom on new)
  - User messages: right-aligned, accent color
  - Assistant messages: left-aligned, with optional `[📋 Pre-fill Order Ticket]` button when `trade_suggestion` present
- Context-fed badge under each assistant message: `📊 prices · 📈 indicators · 💼 positions · 🕐 history`
- Input area: textarea + Send (Enter to send, Shift+Enter newline)
- Per-message latency + token count footer (small text)

`[Pre-fill Order Ticket]` behavior:
- Reads `trade_suggestion` `{exchange, symbol, side, order_type, price?, volume}`
- Calls existing `orders.js` `setSymbol()` and populates form via DOM
- Scrolls to Order Ticket panel
- Does NOT auto-submit

### §4.4 Panel 3 — Strategy Configuration Modal

Trigger: gear icon (⚙) in header next to mode badge.

Form fields (matches PUT /api/strategy/config Pydantic model):
- System prompt (large textarea, monospace)
- Asset whitelist (tag input, comma separated)
- min_confidence_threshold (slider 0-100)
- max_risk_tier (select: low/medium/high)
- analysis_interval_hours (number)
- Guardrail thresholds (group): max_position_pct, max_daily_loss_pct, max_exchange_exposure_pct, cooldown_seconds
- order_min_notional / order_max_notional

Buttons: `[Save]` `[Cancel]` `[Reset to Defaults]`

### §4.5 `api.js` Extensions

```javascript
// Proposals
export async function getProposals(status, limit=50) { ... }
export async function getProposal(id) { ... }
export async function approveProposal(id, approved_by='dashboard_user') { ... }
export async function rejectProposal(id, rejected_by='dashboard_user', reason='') { ... }
export async function getProposalStats() { ... }

// LLM history & trigger
export async function getLLMHistory(limit=50) { ... }
export async function triggerLLMAnalysis() { ... }

// Strategy config (GET counterpart of existing PUT)
export async function getStrategyConfig() { ... }

// Co-Pilot
export async function createCoPilotSession(title=null) { ... }
export async function listCoPilotSessions(limit=20) { ... }
export async function getCoPilotMessages(sessionId) { ... }
export async function sendCoPilotMessage(sessionId, text) { ... }
export async function deleteCoPilotSession(sessionId) { ... }
```

---

## §5. Data Flow Diagrams

**Autonomous Flow:**
```
LLMScheduler.start()──┐ (every 8h, or POST /api/llm/trigger)
                      ▼
   LLMStrategyEngine.analyze_and_propose()
        │
        ├──► _gather_market_context (tickers + indicators)
        ├──► _gather_portfolio_context (positions + cash)
        ├──► PromptBuilder.build  (now includes positions
        │                          and asks about close/reduce)
        ├──► AsyncAnthropic.messages.create
        ├──► LLMResponseParser.parse (action_type aware)
        ├──► ProposalQueue.enqueue (status=PROPOSED)
        └──► llm_analysis_history INSERT
                      │
                      ▼
   AutoExecScanner (every 30s, existing)
        │ if autonomy_enabled and pass guardrails
        ▼
   PaperSimulator OR OrderManager
        │
        ▼
   proposals.status = AUTO_EXECUTED
        │
        ▼
   Frontend polls /api/proposals (10s) → table updates
```

**Co-Pilot Flow:**
```
User opens panel ──► (loads /api/copilot/sessions, last 10)
User clicks [+ New]──► POST /api/copilot/sessions → session_id
User types message + Send
        │
        ▼ POST /api/copilot/sessions/{id}/messages {text}
   CoPilotEngine.send_message
        ├──► load last N messages for session
        ├──► gather context (prices, indicators, positions)
        ├──► build conversational prompt (system + history + ctx + user_text)
        ├──► call Claude with tool definition: optional emit_trade_suggestion
        ├──► persist user + assistant messages to copilot_messages
        └──► return {assistant_text, trade_suggestion?, context_summary}
   Frontend renders bubble
        │ if trade_suggestion present
        ▼
   [📋 Pre-fill Order Ticket] button appears
        │ user clicks
        ▼
   orders.js form pre-filled (NOT submitted)
        │ user reviews + manually clicks Submit
        ▼
   Existing POST /api/orders flow
```

---

## §6. API Contract Highlights

**`POST /api/copilot/sessions/{id}/messages` request:**
```json
{ "text": "BTC 最近跌了不少，我感觉可以加点仓" }
```

**Response:**
```json
{
  "message_id": "uuid",
  "assistant_text": "Looking at current data: BTC at $61,450 (RSI 38, near oversold) ...",
  "trade_suggestion": {
    "exchange": "hyperliquid",
    "symbol": "BTCUSDT",
    "side": "buy",
    "order_type": "limit",
    "price": "61200",
    "volume": "0.05",
    "rationale": "Oversold RSI + 4h support level + you're at 22% BTC allocation (room to add)"
  },
  "context_summary": {
    "prices_fed": ["BTCUSDT", "ETHUSDT"],
    "indicators_fed": ["RSI", "MACD", "EMA20"],
    "positions_fed": true,
    "history_messages": 6
  },
  "token_count_input": 1842,
  "token_count_output": 487,
  "latency_ms": 2103
}
```

`trade_suggestion` is `null` when LLM doesn't propose a trade.

---

## §7. Testing Strategy

**Backend (added to `horizon/tests/`):**
- `test_llm_client.py` — credential loading from key.txt and env precedence
- `test_llm_scheduler.py` — `trigger_now()`, start/stop, no concurrent execution
- `test_copilot_engine.py` — create/list/send_message with mocked Anthropic, conversation persistence, trade_suggestion extraction
- `test_proposals_api.py` — all 5 new proposal endpoints
- `test_copilot_api.py` — all 5 new copilot endpoints
- `test_action_type_parser.py` — parser accepts open/close/reduce, validates close requires existing position
- `test_main_llm_wiring.py` — lifespan starts/stops LLM scheduler cleanly

**Frontend:** manual verification per CLAUDE.md (no JS test framework configured in repo).

**Integration verification (manual, post-merge):**
1. POST /api/llm/trigger → see proposal row in /api/proposals
2. Approve via UI → check `orders` or `paper_trades` table
3. Open Co-Pilot, ask "should I buy ETH?" → response includes context_summary
4. If LLM suggests trade → [Pre-fill] button works → Order Ticket populated
5. Toggle Auto-Execution → high-confidence/low-risk proposal becomes AUTO_EXECUTED within 30s
6. Server shutdown is clean (no hung tasks)

---

## §8. Cleanup & Risk Items

### Hygiene gaps found during audit
1. **No root `.gitignore`** — added in this delivery, includes `key.txt`, `data/`, `*.db`, `.venv/`, `__pycache__/`, `*.pyc`, `node_modules/`, `.idea/`, `.vscode/`
2. **`key.txt` is tracked in git history** — secrets exposed. *Out of scope for this delivery*, but flagged: should be force-removed from history in a separate hotfix and credentials rotated.
3. **`config.yaml` contains dead LLM config** (`llm.api_key`, `llm.base_url`, `llm.model` for GLM provider) — removed in this delivery; new config reads from key.txt env.

### Implementation risks
1. **Cross-agent API contract drift** — mitigation: both agents reference §3.6 & §6 of this spec; backend defines Pydantic models first, frontend uses matching shapes
2. **Co-Pilot conversation tokens may grow unbounded** — mitigation: 100k-char trim before send, configurable
3. **Position-management proposals** for live trading need to verify the "close" side maps correctly to exchange `reduceOnly` semantics — to be tested in paper first
4. **LLM provider rate limits** — MiniMax endpoint limits unknown; add 60s timeout + retry with exponential backoff
5. **Concurrent scheduler+manual trigger** — `LLMScheduler.trigger_now` must not collide with periodic run; use `asyncio.Lock`

### Out-of-scope (deliberate)
- News/social external data feed (deferred per brainstorming)
- Per-user Co-Pilot isolation (single-operator system)
- Co-Pilot suggesting close/reduce directly (only Engine emits close/reduce; Co-Pilot suggests opens — same as user typing)

---

## §9. Definition of Done

A single integrated commit on `dev` (no per-task micro-commits) that:
- [ ] Passes `pytest horizon/tests/ -q` with all new + existing tests green
- [ ] `python -m horizon.main` boots cleanly, `/api/proposals/stats` reachable
- [ ] Hitting `/api/llm/trigger` creates ≥1 `llm_analysis_history` row (even if 0 proposals)
- [ ] Co-Pilot session created → message sent → response received → message persisted
- [ ] Manual visual verification: 3 new panels render, no console errors
- [ ] `.gitignore` present at repo root, `key.txt` not in staging
- [ ] Commit message follows `<type>(<scope>): <description>` (likely `feat(llm): wire dual-mode LLM trading — autonomous + co-pilot UI`)

---

*End of design document. Next: invoke `writing-plans` skill to derive task-by-task implementation plan.*
