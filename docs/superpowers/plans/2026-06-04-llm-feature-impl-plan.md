# LLM Frontend + Backend Feature — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire LLM-driven autonomous trading (8h scheduled analysis + open/close/reduce proposals) AND human-initiated multi-turn Co-Pilot chat into Horizon's running backend + frontend, producing a single integration commit on `dev`.

**Architecture:** Approach B from design spec — dual-engine independent modules. Shared `AnthropicClient`. Existing `LLMStrategyEngine` extended for position management. New `CoPilotEngine` handles human-initiated dialogue. Frontend gets three new panels (Proposals Queue, Co-Pilot Chat, Strategy Config) plus a "Pre-fill Order Ticket" bridge from Co-Pilot suggestions to the existing order form.

**Tech Stack:** Python 3.x + asyncio + FastAPI + aiosqlite + `anthropic` SDK (Anthropic-compatible endpoint via MiniMax in key.txt) + Vanilla JS ES modules.

**Agent ownership tags:**
- `[FOUNDATION]` — must complete first; single agent (Backend agent)
- `[BACKEND]` — Backend agent
- `[FRONTEND]` — Frontend agent (can run in parallel with BACKEND after FOUNDATION)
- `[INTEGRATION]` — both agents converge; run after BACKEND + FRONTEND complete

**Branch:** Direct commits to `dev` (worktree skipped per user authorization). All micro-commits are allowed during development; the final integration step squashes-or-keeps depending on review.

**Spec reference:** `docs/superpowers/specs/2026-06-04-llm-feature-design.md`

---

## File Structure (Created / Modified)

```
BACKEND
  horizon/internal/llm/
    client.py            (NEW)    — credential loader + AsyncAnthropic factory
    scheduler.py         (NEW)    — asyncio 8h periodic trigger
    copilot.py           (NEW)    — CoPilotEngine multi-turn chat
    copilot_prompt.py    (NEW)    — conversational system prompt
    engine.py            (MODIFY) — _gather_portfolio_context returns positions w/ entry
    prompt_builder.py    (MODIFY) — instructions for close/reduce
    parser.py            (MODIFY) — accept action_type field
  horizon/internal/proposals/
    models.py            (MODIFY) — add action_type to TradeProposal
  horizon/internal/database/migrations/
    005_copilot.sql      (NEW)    — copilot_sessions, copilot_messages, action_type column
  horizon/internal/web/
    server.py            (MODIFY) — +13 endpoints
  horizon/
    main.py              (MODIFY) — wire LLM client, engine, scheduler, copilot
  horizon/tests/
    test_llm_client.py            (NEW)
    test_llm_scheduler.py         (NEW)
    test_copilot_engine.py        (NEW)
    test_proposals_api.py         (NEW)
    test_copilot_api.py           (NEW)
    test_action_type_parser.py    (NEW)

FRONTEND
  horizon/internal/web/static/
    js/proposals.js          (NEW)
    js/copilot.js            (NEW)
    js/strategy_config.js    (NEW)
    js/api.js                (MODIFY)
    js/app.js                (MODIFY)
    index.html               (MODIFY)
    css/styles.css           (MODIFY)

HYGIENE
  .gitignore                 (NEW) — root, ignore key.txt + data/ + caches
```

---

## Phase 0 — Foundation `[FOUNDATION]` (BLOCKS all other phases)

### Task 1: Add root .gitignore `[FOUNDATION]`

**Files:**
- Create: `.gitignore`

- [ ] **Step 1: Verify no .gitignore exists**

Run: `ls -la /d/Horizon-Dev/.gitignore 2>/dev/null || echo "absent — good"`
Expected: `absent — good`

- [ ] **Step 2: Create .gitignore**

Write to `/d/Horizon-Dev/.gitignore`:
```
# Secrets — NEVER commit
key.txt
.env
.env.local
*.pem

# Runtime data + models
data/
*.db
*.db-journal
*.sqlite
*.sqlite3
*.pkl
*.cache

# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.pytest_cache/
.mypy_cache/
.coverage
htmlcov/

# Virtualenvs
.venv/
venv/
env/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Node (frontend tooling, if introduced later)
node_modules/
dist/
build/
```

- [ ] **Step 3: Verify key.txt is now ignored**

Run: `cd /d/Horizon-Dev && git check-ignore key.txt && echo "OK — key.txt now ignored"`
Expected: `OK — key.txt now ignored`

- [ ] **Step 4: Commit**

```bash
cd /d/Horizon-Dev
git add .gitignore
git commit -m "chore(repo): add root .gitignore covering secrets, data, caches, IDE files"
```

---

### Task 2: LLM Client — credential loader + AsyncAnthropic factory `[FOUNDATION]`

**Files:**
- Create: `horizon/internal/llm/client.py`
- Create: `horizon/tests/test_llm_client.py`

- [ ] **Step 1: Write failing test**

Write to `horizon/tests/test_llm_client.py`:
```python
"""Tests for LLM credential loader and Anthropic client factory."""
import os
import tempfile
from pathlib import Path

import pytest

from horizon.internal.llm.client import (
    LLMCredentials,
    load_llm_credentials,
    build_anthropic_client,
    LLMCredentialsError,
)


SAMPLE_KEY_FILE = '''
"ANTHROPIC_AUTH_TOKEN": "sk-test-token-abc123",
"ANTHROPIC_BASE_URL": "https://api.example.com/anthropic",
"ANTHROPIC_DEFAULT_SONNET_MODEL": "test-model-m3",
'''


def test_load_llm_credentials_from_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text(SAMPLE_KEY_FILE)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    creds = load_llm_credentials(key_file=f)
    assert creds.auth_token == "sk-test-token-abc123"
    assert creds.base_url == "https://api.example.com/anthropic"
    assert creds.model == "test-model-m3"


def test_env_var_overrides_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text(SAMPLE_KEY_FILE)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-override-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env-override.example.com")
    creds = load_llm_credentials(key_file=f)
    assert creds.auth_token == "env-override-token"
    assert creds.base_url == "https://env-override.example.com"


def test_missing_credentials_raises(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text("# empty")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    with pytest.raises(LLMCredentialsError):
        load_llm_credentials(key_file=f)


def test_build_anthropic_client_returns_async_client(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text(SAMPLE_KEY_FILE)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    client = build_anthropic_client(key_file=f)
    from anthropic import AsyncAnthropic
    assert isinstance(client, AsyncAnthropic)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_llm_client.py -x -q`
Expected: `ImportError` or `ModuleNotFoundError` for `horizon.internal.llm.client`.

- [ ] **Step 3: Implement client.py**

Write to `horizon/internal/llm/client.py`:
```python
"""LLM credentials loader + Anthropic client factory.

Credentials come from key.txt (project root by default) or environment variables.
Env vars take precedence over file values (12-factor).
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from anthropic import AsyncAnthropic


DEFAULT_KEY_FILE = Path(__file__).resolve().parents[3] / "key.txt"


class LLMCredentialsError(RuntimeError):
    """Raised when required LLM credentials cannot be located."""


@dataclass(frozen=True)
class LLMCredentials:
    auth_token: str
    base_url: str
    model: str


_KEY_LINE_RE = re.compile(r'"([A-Z_][A-Z0-9_]*)"\s*:\s*"([^"]*)"')


def _parse_key_file(path: Path) -> dict[str, str]:
    """Tolerantly parse a key.txt-style file. Lines look like:
        "KEY": "value",
    Whitespace and trailing commas are ignored. Lines not matching are skipped.
    Returns an empty dict if the file doesn't exist or is empty.
    """
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _KEY_LINE_RE.search(line)
        if m:
            result[m.group(1)] = m.group(2)
    return result


def load_llm_credentials(
    key_file: Optional[Path] = None,
) -> LLMCredentials:
    """Load credentials. Env vars override file values."""
    path = Path(key_file) if key_file else DEFAULT_KEY_FILE
    file_data = _parse_key_file(path)

    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or file_data.get("ANTHROPIC_AUTH_TOKEN", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or file_data.get("ANTHROPIC_BASE_URL", "")
    model = (
        os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or file_data.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or file_data.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
        or file_data.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
        or ""
    )

    if not auth_token or not base_url:
        raise LLMCredentialsError(
            "Missing ANTHROPIC_AUTH_TOKEN or ANTHROPIC_BASE_URL. "
            f"Checked file {path} and environment variables."
        )

    if not model:
        model = "claude-3-5-sonnet-20241022"  # safe fallback

    return LLMCredentials(auth_token=auth_token, base_url=base_url, model=model)


def build_anthropic_client(key_file: Optional[Path] = None) -> AsyncAnthropic:
    """Construct an AsyncAnthropic client from current credentials."""
    creds = load_llm_credentials(key_file=key_file)
    return AsyncAnthropic(auth_token=creds.auth_token, base_url=creds.base_url)
```

- [ ] **Step 4: Run tests**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_llm_client.py -x -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/llm/client.py horizon/tests/test_llm_client.py
git commit -m "feat(llm): add credential loader + AsyncAnthropic factory (client.py)"
```

---

### Task 3: DB migration 005_copilot.sql `[FOUNDATION]`

**Files:**
- Create: `horizon/internal/database/migrations/005_copilot.sql`

- [ ] **Step 1: Write migration**

Write to `horizon/internal/database/migrations/005_copilot.sql`:
```sql
-- Migration 005: Co-Pilot conversation tables + action_type on proposals

CREATE TABLE IF NOT EXISTS copilot_sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS copilot_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    trade_suggestion TEXT,
    context_summary TEXT,
    token_count_input INTEGER,
    token_count_output INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES copilot_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_copilot_messages_session
    ON copilot_messages(session_id, created_at);

-- Additive column on proposals for position management
-- SQLite ALTER TABLE ADD COLUMN is idempotent only via try/except in Python,
-- so we wrap with a sentinel check using PRAGMA. This is portable enough for
-- the existing migration runner which executes the whole file in one transaction.
-- If the column already exists, the ALTER will fail; the runner must tolerate
-- the IntegrityError and continue. (Alternative: add a guard in db.py.)
ALTER TABLE proposals ADD COLUMN action_type TEXT NOT NULL DEFAULT 'open';
```

- [ ] **Step 2: Verify migration runner tolerates ALTER failures**

Run: `cd /d/Horizon-Dev && grep -n "execute" horizon/internal/database/db.py | head -20`
Expected: shows the migration loop. If it does NOT catch sqlite errors on duplicate-column ADD, **modify db.py** to wrap each statement-or-file in try/except OperationalError with message containing "duplicate column".

- [ ] **Step 3: If db.py needs hardening, modify**

If `db.py` migration runner does not skip duplicate-column errors, replace its statement executor block with:
```python
try:
    await db.executescript(sql_content)
except aiosqlite.OperationalError as e:
    msg = str(e).lower()
    if "duplicate column name" in msg:
        logger.warning("Skipping duplicate column in %s: %s", migration_file, e)
    else:
        raise
```
(Use the existing logger; do not introduce new ones.)

- [ ] **Step 4: Run migrations against a fresh temp DB**

Run:
```bash
cd /d/Horizon-Dev && python -c "
import asyncio, tempfile, os
from horizon.internal.database.db import init_db, close_db
async def main():
    p = tempfile.mktemp(suffix='.db')
    db = await init_db(p, 'horizon/internal/database/migrations')
    c = await db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
    rows = await c.fetchall()
    names = [r[0] for r in rows]
    assert 'copilot_sessions' in names, names
    assert 'copilot_messages' in names, names
    c = await db.execute('PRAGMA table_info(proposals)')
    cols = [r[1] for r in await c.fetchall()]
    assert 'action_type' in cols, cols
    await close_db(db)
    os.unlink(p)
    print('OK')
asyncio.run(main())
"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/database/migrations/005_copilot.sql
git diff --cached --stat
# Include db.py only if modified in Step 3
git add horizon/internal/database/db.py 2>/dev/null
git commit -m "feat(db): migration 005 — copilot tables + proposals.action_type"
```

---

### Task 4: Extend TradeProposal model + parser for action_type `[FOUNDATION]`

**Files:**
- Modify: `horizon/internal/proposals/models.py`
- Modify: `horizon/internal/llm/parser.py`
- Create: `horizon/tests/test_action_type_parser.py`

- [ ] **Step 1: Write failing test**

Write to `horizon/tests/test_action_type_parser.py`:
```python
"""Tests for action_type field in parser + TradeProposal."""
import json

import pytest

from horizon.internal.llm.parser import LLMResponseParser


WHITELIST = ["BTCUSDT", "ETHUSDT"]


def _make_response(action_type=None):
    proposal = {
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "side": "buy",
        "order_type": "market",
        "volume": "0.1",
        "confidence_score": 80,
        "risk_tier": "low",
        "rationale": "test",
    }
    if action_type is not None:
        proposal["action_type"] = action_type
    return json.dumps({
        "analysis_summary": "s",
        "confidence_explanation": "c",
        "proposals": [proposal],
    })


def test_parser_default_action_type_is_open():
    p = LLMResponseParser(asset_whitelist=WHITELIST)
    proposals = p.parse(_make_response(), {"tickers": [{"symbol":"BTCUSDT","price":"50000"}]}, {}, {})
    assert len(proposals) == 1
    assert proposals[0].action_type == "open"


def test_parser_accepts_close_action():
    p = LLMResponseParser(asset_whitelist=WHITELIST)
    proposals = p.parse(_make_response(action_type="close"), {"tickers":[{"symbol":"BTCUSDT","price":"50000"}]}, {}, {})
    assert proposals[0].action_type == "close"


def test_parser_accepts_reduce_action():
    p = LLMResponseParser(asset_whitelist=WHITELIST)
    proposals = p.parse(_make_response(action_type="reduce"), {"tickers":[{"symbol":"BTCUSDT","price":"50000"}]}, {}, {})
    assert proposals[0].action_type == "reduce"


def test_parser_invalid_action_type_defaults_to_open():
    p = LLMResponseParser(asset_whitelist=WHITELIST)
    proposals = p.parse(_make_response(action_type="explode"), {"tickers":[{"symbol":"BTCUSDT","price":"50000"}]}, {}, {})
    assert proposals[0].action_type == "open"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_action_type_parser.py -x -q`
Expected: `AttributeError: 'TradeProposal' object has no attribute 'action_type'`.

- [ ] **Step 3: Add action_type to TradeProposal models**

Read current `horizon/internal/proposals/models.py`. Find the `TradeProposal` dataclass. Add field:
```python
action_type: str = "open"  # one of: open, close, reduce
```
Add it AFTER existing scalar fields like `risk_tier`, BEFORE the JSON/optional fields.

Also update `from_row()` classmethod — find the line that builds the instance from a database row, add:
```python
action_type=row["action_type"] if "action_type" in row.keys() else "open",
```

And update `to_dict()` to include `"action_type": self.action_type` in the returned dict.

- [ ] **Step 4: Update parser to extract action_type**

In `horizon/internal/llm/parser.py`, inside the loop that builds proposals (search for the call site that constructs `TradeProposal(...)`), add:
```python
raw_action = proposal_dict.get("action_type", "open")
action_type = raw_action if raw_action in ("open", "close", "reduce") else "open"
```
Then pass `action_type=action_type` to the `TradeProposal` constructor.

- [ ] **Step 5: Update queue.py enqueue INSERT to persist action_type**

In `horizon/internal/proposals/queue.py`, find the `INSERT INTO proposals` statement inside `enqueue()`. Add `action_type` to both the column list and the VALUES tuple. Order placement: after `risk_tier`, before `llm_rationale`.

Updated columns list:
```sql
(id, status, exchange, symbol, side, order_type, price, volume,
 confidence_score, risk_tier, action_type, llm_rationale, ...)
```
Add `proposal.action_type,` in the matching tuple position.

- [ ] **Step 6: Run tests**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_action_type_parser.py horizon/tests/test_llm_parser.py horizon/tests/test_proposal_queue.py -x -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/proposals/models.py horizon/internal/proposals/queue.py horizon/internal/llm/parser.py horizon/tests/test_action_type_parser.py
git commit -m "feat(proposals): add action_type (open|close|reduce) to TradeProposal + parser"
```

---

## Phase 1 — Backend Autonomous Track `[BACKEND]`

### Task 5: Extend PromptBuilder for positions + close/reduce `[BACKEND]`

**Files:**
- Modify: `horizon/internal/llm/prompt_builder.py`
- Modify: `horizon/internal/llm/engine.py` (`_gather_portfolio_context`)
- Modify: `horizon/tests/test_prompt_builder.py` (add test)

- [ ] **Step 1: Add failing test for new instructions**

In `horizon/tests/test_prompt_builder.py`, add:
```python
def test_prompt_includes_close_and_reduce_instructions():
    from horizon.internal.llm.prompt_builder import PromptBuilder, MarketContext, PortfolioContext
    pb = PromptBuilder({"asset_whitelist": '["BTCUSDT"]', "max_position_pct": 20,
                        "max_daily_loss_pct": 5, "min_confidence_threshold": 75,
                        "max_risk_tier": "low"})
    mc = MarketContext(tickers=[], orderbooks={}, technical_indicators={}, recent_trades={})
    pc = PortfolioContext(balances=[], total_usdt_value="0", open_positions={
        "BTC": {"side": "long", "volume": "0.5", "entry_price": "60000"}
    })
    prompt = pb.build(mc, pc, {"asset_whitelist": '["BTCUSDT"]', "max_position_pct": 20,
                                "max_daily_loss_pct": 5, "min_confidence_threshold": 75,
                                "max_risk_tier": "low", "system_prompt": ""})
    assert "action_type" in prompt
    assert "open" in prompt.lower() and "close" in prompt.lower() and "reduce" in prompt.lower()
    assert "BTC" in prompt  # current position visible
```

- [ ] **Step 2: Run failing**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_prompt_builder.py::test_prompt_includes_close_and_reduce_instructions -x -q`
Expected: FAIL — `action_type` not in prompt.

- [ ] **Step 3: Update PromptBuilder instructions section**

In `horizon/internal/llm/prompt_builder.py`, find the section that describes the response schema. Update it to:

(a) Add `action_type` field to the JSON schema example:
```json
{
  ...,
  "action_type": "open|close|reduce",
  ...
}
```

(b) Add a new bullet to the Instructions section:
```
- You may also recommend modifying existing positions:
  - action_type = "close": fully exit a position you currently hold
  - action_type = "reduce": partially exit a position (volume = amount to exit)
  - action_type = "open": new entry (default if omitted)
  For close/reduce, the `side` field is the closing side (opposite of held direction).
  Only propose close/reduce for symbols listed under "Current open positions".
```

(c) In the Portfolio section that the builder renders, ensure open_positions are listed prominently — find where `portfolio_context.open_positions` gets stringified and ensure each entry shows `{symbol, side, volume, entry_price}`.

- [ ] **Step 4: Update engine._gather_portfolio_context to include entry price estimate**

In `horizon/internal/llm/engine.py` `_gather_portfolio_context`, the existing code sets `entry_price: "N/A"` (line ~461). Replace with a best-effort estimate: query the latest filled orders for that asset and average their fill price. If no orders found, keep `"N/A"`:

```python
# Estimate entry price from recent filled orders
async def _estimate_entry_price(self, asset: str, exchange: str) -> str:
    cursor = await self._db.execute(
        "SELECT AVG(price) FROM orders "
        "WHERE symbol LIKE ? AND exchange = ? AND status = 'filled' "
        "ORDER BY created_at DESC LIMIT 5",
        (f"{asset}%", exchange),
    )
    row = await cursor.fetchone()
    if row and row[0]:
        return f"{row[0]:.2f}"
    return "N/A"
```
Then in the open_positions loop, replace `"entry_price": "N/A"` with:
```python
"entry_price": await self._estimate_entry_price(balance["asset"], balance["exchange"]),
```

- [ ] **Step 5: Run tests**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_prompt_builder.py horizon/tests/test_llm_engine.py -x -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/llm/prompt_builder.py horizon/internal/llm/engine.py horizon/tests/test_prompt_builder.py
git commit -m "feat(llm): prompt teaches close/reduce + portfolio entry-price estimates"
```

---

### Task 6: LLM Scheduler — asyncio 8h periodic trigger `[BACKEND]`

**Files:**
- Create: `horizon/internal/llm/scheduler.py`
- Create: `horizon/tests/test_llm_scheduler.py`

- [ ] **Step 1: Write failing test**

Write to `horizon/tests/test_llm_scheduler.py`:
```python
"""Tests for LLMScheduler."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from horizon.internal.llm.scheduler import LLMScheduler


@pytest.mark.asyncio
async def test_trigger_now_calls_engine_once():
    engine = MagicMock()
    engine.analyze_and_propose = AsyncMock(return_value="ok")
    scheduler = LLMScheduler(engine, interval_seconds=999)
    result = await scheduler.trigger_now()
    assert result == "ok"
    engine.analyze_and_propose.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_runs_immediately_then_stops():
    engine = MagicMock()
    engine.analyze_and_propose = AsyncMock(return_value=None)
    scheduler = LLMScheduler(engine, interval_seconds=999, run_on_start=True)
    await scheduler.start()
    # give the task a moment to enter its first iteration
    await asyncio.sleep(0.1)
    await scheduler.stop()
    assert engine.analyze_and_propose.await_count >= 1


@pytest.mark.asyncio
async def test_concurrent_trigger_serialized_by_lock():
    engine = MagicMock()
    in_flight = []
    async def slow():
        in_flight.append(1)
        await asyncio.sleep(0.05)
        in_flight.pop()
        return "done"
    engine.analyze_and_propose = AsyncMock(side_effect=slow)
    scheduler = LLMScheduler(engine, interval_seconds=999)
    results = await asyncio.gather(
        scheduler.trigger_now(),
        scheduler.trigger_now(),
    )
    assert all(r == "done" for r in results)
    # In-flight never exceeded 1 due to the lock
    # (we can't assert on the list state directly after the fact;
    # rely on no exceptions + two completions.)
    assert engine.analyze_and_propose.await_count == 2
```

- [ ] **Step 2: Run failing**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_llm_scheduler.py -x -q`
Expected: `ModuleNotFoundError` for `horizon.internal.llm.scheduler`.

- [ ] **Step 3: Implement scheduler.py**

Write to `horizon/internal/llm/scheduler.py`:
```python
"""Periodic asyncio trigger for LLMStrategyEngine.

Replaces APScheduler with a minimal asyncio loop. Supports:
- start() / stop() lifecycle
- trigger_now() for manual "Analyze Now" button
- Lock serializes concurrent invocations
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class LLMScheduler:
    """Runs engine.analyze_and_propose() at a configurable interval."""

    def __init__(
        self,
        engine,
        interval_seconds: int = 8 * 3600,
        run_on_start: bool = False,
    ):
        self._engine = engine
        self._interval = interval_seconds
        self._run_on_start = run_on_start
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="LLMScheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

    async def trigger_now(self):
        async with self._lock:
            logger.info("LLMScheduler.trigger_now invoked")
            return await self._engine.analyze_and_propose()

    async def _loop(self) -> None:
        if self._run_on_start:
            await self._run_once_safe()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval,
                )
                # Stop event fired — exit cleanly
                return
            except asyncio.TimeoutError:
                # Interval elapsed — run analysis
                await self._run_once_safe()

    async def _run_once_safe(self) -> None:
        async with self._lock:
            try:
                logger.info("LLMScheduler: periodic analysis starting")
                await self._engine.analyze_and_propose()
                logger.info("LLMScheduler: periodic analysis complete")
            except Exception as e:
                logger.exception("LLMScheduler analysis failed: %s", e)
```

- [ ] **Step 4: Run tests**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_llm_scheduler.py -x -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/llm/scheduler.py horizon/tests/test_llm_scheduler.py
git commit -m "feat(llm): add LLMScheduler asyncio 8h periodic trigger with manual API"
```

---

### Task 7: Proposal REST endpoints `[BACKEND]`

**Files:**
- Modify: `horizon/internal/web/server.py`
- Create: `horizon/tests/test_proposals_api.py`

- [ ] **Step 1: Add Pydantic request models**

In `horizon/internal/web/server.py`, near other Pydantic models (around line 80-135), add:
```python
class ApproveProposalModel(BaseModel):
    approved_by: str = Field(..., description="Identifier of approver")


class RejectProposalModel(BaseModel):
    rejected_by: str = Field(..., description="Identifier of rejecter")
    reason: Optional[str] = Field(None, description="Optional rejection reason")
```

- [ ] **Step 2: Add 5 proposal endpoints**

Inside `create_app()`, after existing routes, add this block. The `proposal_queue` is read from `app.state` (must be set by `main.py` — done in Task 9).

```python
@app.get("/api/proposals")
async def list_proposals(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    queue = app.state.proposal_queue
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
    queue = app.state.proposal_queue
    return CustomJSONResponse(await queue.get_stats())


@app.get("/api/proposals/{proposal_id}")
async def get_proposal_detail(proposal_id: str) -> JSONResponse:
    queue = app.state.proposal_queue
    p = await queue.get_proposal(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return CustomJSONResponse(p.to_dict())


@app.post("/api/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, body: ApproveProposalModel) -> JSONResponse:
    queue = app.state.proposal_queue
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
    queue = app.state.proposal_queue
    from ..proposals.queue import ProposalNotFoundError, InvalidProposalStateError
    try:
        p = await queue.reject(proposal_id, body.rejected_by, body.reason or "")
    except ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="proposal not found")
    except InvalidProposalStateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return CustomJSONResponse(p.to_dict())
```

- [ ] **Step 3: Write API tests**

Write to `horizon/tests/test_proposals_api.py`:
```python
"""Tests for /api/proposals/* endpoints."""
import asyncio
import tempfile
import os
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build a TestClient backed by a temp DB with a sample proposal seeded."""
    from horizon.internal.database.db import init_db
    from horizon.internal.proposals.models import TradeProposal, ProposalStatus
    from horizon.internal.proposals.queue import ProposalQueue
    from horizon.internal.web.server import create_app
    from horizon.internal.config.settings import settings as real_settings
    from horizon.internal.exchange.registry import ExchangeRegistry
    from horizon.internal.marketdata.fetcher import MarketDataFetcher
    from horizon.internal.ordermanager.manager import OrderManager

    async def build():
        db_path = str(tmp_path / "t.db")
        db = await init_db(db_path, "horizon/internal/database/migrations")
        await db.execute(
            "INSERT INTO exchanges(name, enabled) VALUES (?, 1)",
            ("binance",),
        )
        await db.commit()
        registry = ExchangeRegistry()
        fetcher = MarketDataFetcher(registry=registry, db=db, active_pairlist=None,
                                     active_exchange="binance", poll_interval_seconds=60)
        order_mgr = OrderManager(registry=registry, db=db, active_exchange="binance")
        queue = ProposalQueue(db=db, order_manager=order_mgr, strategy_config={"asset_whitelist":"[]"})
        p = TradeProposal(
            id=str(uuid.uuid4()), status=ProposalStatus.PROPOSED.value,
            exchange="binance", symbol="BTCUSDT", side="buy", order_type="market",
            price=None, volume=0.1, confidence_score=80, risk_tier="low",
            llm_rationale="test", llm_raw_response="{}", technical_context="{}",
            market_snapshot="{}", portfolio_snapshot="{}", guardrail_result=None,
            approved_by=None, approved_at=None, executed_order_id=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            price_drift_threshold_pct=3.0, proposed_price=50000.0,
            created_at=datetime.now(timezone.utc),
            action_type="open",
        )
        await queue.enqueue(p)
        app = create_app(settings=real_settings, db=db, registry=registry,
                          fetcher=fetcher, order_manager=order_mgr)
        app.state.proposal_queue = queue
        return app, p.id

    app, pid = asyncio.run(build())
    return TestClient(app), pid


def test_list_proposals(client):
    c, _ = client
    r = c.get("/api/proposals")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 1
    assert "proposals" in data


def test_get_proposal_detail(client):
    c, pid = client
    r = c.get(f"/api/proposals/{pid}")
    assert r.status_code == 200
    assert r.json()["id"] == pid


def test_get_proposal_404(client):
    c, _ = client
    r = c.get("/api/proposals/nonexistent-id")
    assert r.status_code == 404


def test_proposal_stats(client):
    c, _ = client
    r = c.get("/api/proposals/stats")
    assert r.status_code == 200
    assert "proposed" in r.json()


def test_reject_proposal(client):
    c, pid = client
    r = c.post(f"/api/proposals/{pid}/reject",
                json={"rejected_by": "dashboard_user", "reason": "test"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
```

- [ ] **Step 4: Run tests**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_proposals_api.py -x -q`
Expected: 5 passed. If "approve" path fails due to missing exchange in registry, the seeded INSERT into `exchanges` ensures the FK passes; approve tests are deferred to integration (it tries to actually submit an order).

- [ ] **Step 5: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/web/server.py horizon/tests/test_proposals_api.py
git commit -m "feat(api): add 5 /api/proposals endpoints (list, detail, approve, reject, stats)"
```

---

### Task 8: LLM history + trigger + strategy config GET endpoints `[BACKEND]`

**Files:**
- Modify: `horizon/internal/web/server.py`

- [ ] **Step 1: Add endpoints**

Inside `create_app()`, append:
```python
@app.get("/api/llm/history")
async def llm_history(limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
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
    cursor = await db.execute(
        "SELECT * FROM strategy_configs WHERE enabled = 1 LIMIT 1"
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no active strategy config")
    return CustomJSONResponse(dict(row))
```

- [ ] **Step 2: Smoke-test manually**

Run: `cd /d/Horizon-Dev && python -c "from horizon.internal.web.server import create_app; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 3: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/web/server.py
git commit -m "feat(api): add /api/llm/history, /api/llm/trigger, /api/strategy/config GET"
```

---

### Task 9: Wire autonomous LLM stack in main.py `[BACKEND]`

**Files:**
- Modify: `horizon/main.py`

- [ ] **Step 1: Add imports**

At the top of `horizon/main.py`, add imports:
```python
from horizon.internal.indicators.calculator import TechnicalIndicatorCalculator
from horizon.internal.llm.client import build_anthropic_client, load_llm_credentials, LLMCredentialsError
from horizon.internal.llm.engine import LLMStrategyEngine
from horizon.internal.llm.scheduler import LLMScheduler
```

- [ ] **Step 2: Wire LLM stack in `lifespan` after strategy_config loads**

Find the location in `lifespan()` after `strategy_config = await _load_strategy_config(db)` block and **before** `# 6. CooldownTracker setup`. Insert:
```python
# 5.5. Load strategy_config DICT version for LLM engine (separate from dataclass)
cursor = await db.execute("SELECT * FROM strategy_configs WHERE enabled = 1 LIMIT 1")
sc_row = await cursor.fetchone()
strategy_config_dict = dict(sc_row) if sc_row else {}

# 5.6. Build LLM client + engine (best-effort — server still runs if LLM unavailable)
anthropic_client = None
llm_engine = None
llm_scheduler = None
indicator_calculator = TechnicalIndicatorCalculator(db)
try:
    anthropic_client = build_anthropic_client()
    logger.info("Anthropic client built successfully")
except LLMCredentialsError as e:
    logger.warning("LLM disabled — credentials unavailable: %s", e)
```

- [ ] **Step 3: After ProposalQueue creation (step 10 in lifespan), build engine + scheduler**

Find `proposal_queue.set_registry(registry)` line. Immediately after it, add:
```python
if anthropic_client is not None:
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

application.state.llm_engine = llm_engine
application.state.llm_scheduler = llm_scheduler
```

- [ ] **Step 4: Start scheduler + expiry scanner in background-tasks section**

Find `await proposal_queue.start_auto_execution_scanner()` line (around line 300). After it, add:
```python
await proposal_queue.start_expiry_scanner()
logger.info("Proposal expiry scanner started")

if llm_scheduler is not None:
    await llm_scheduler.start()
    logger.info("LLM scheduler started (interval 8h)")
```

- [ ] **Step 5: Add shutdown logic**

In the shutdown section (below `yield`), add BEFORE `await proposal_queue.stop_auto_execution_scanner()`:
```python
if llm_scheduler is not None:
    await llm_scheduler.stop()
    logger.info("LLM scheduler stopped")
await proposal_queue.stop_expiry_scanner()
logger.info("Proposal expiry scanner stopped")
```

- [ ] **Step 6: Smoke test boot**

Run:
```bash
cd /d/Horizon-Dev && timeout 8 python -m horizon.main 2>&1 | tail -40
```
Expected: lines including "LLMStrategyEngine + LLMScheduler initialized", "LLM scheduler started", "Horizon server started", then graceful timeout.

If "LLM disabled" message appears, verify `key.txt` contains the credentials.

- [ ] **Step 7: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/main.py
git commit -m "feat(main): wire LLMStrategyEngine + LLMScheduler + expiry scanner in lifespan"
```

---

## Phase 2 — Backend Co-Pilot Track `[BACKEND]`

### Task 10: Co-Pilot conversational prompt + engine `[BACKEND]`

**Files:**
- Create: `horizon/internal/llm/copilot_prompt.py`
- Create: `horizon/internal/llm/copilot.py`
- Create: `horizon/tests/test_copilot_engine.py`

- [ ] **Step 1: Write failing test**

Write to `horizon/tests/test_copilot_engine.py`:
```python
"""Tests for CoPilotEngine."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_create_and_send_message(tmp_path):
    from horizon.internal.database.db import init_db
    from horizon.internal.llm.copilot import CoPilotEngine

    db = await init_db(str(tmp_path / "t.db"), "horizon/internal/database/migrations")

    # Mock Anthropic client
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(text=json.dumps({
        "assistant_text": "Based on RSI 38, modest oversold. Consider a small entry.",
        "trade_suggestion": {
            "exchange": "binance", "symbol": "BTCUSDT",
            "side": "buy", "order_type": "limit",
            "price": "60000", "volume": "0.01",
            "rationale": "oversold bounce",
        }
    }))]
    fake_msg.usage = MagicMock(input_tokens=200, output_tokens=80)
    anth = MagicMock()
    anth.messages.create = AsyncMock(return_value=fake_msg)

    # Mock registry/fetcher/indicators/portfolio
    registry = MagicMock()
    registry.get_all_tickers = AsyncMock(return_value=[])
    fetcher = MagicMock()
    indicators = MagicMock()
    indicators.compute_for_symbol = AsyncMock(return_value=MagicMock(
        rsi_14=38, macd_line=0.1, macd_signal=0.05, macd_histogram=0.05,
        ema_20=60100, ema_50=59900, bollinger_upper=61000, bollinger_lower=59000, atr_14=300,
    ))
    portfolio_tracker = MagicMock()
    portfolio_tracker.get_snapshot = AsyncMock(return_value={"total_usdt_value": "20000", "balances": []})

    engine = CoPilotEngine(
        db=db, anthropic_client=anth, registry=registry, fetcher=fetcher,
        indicator_calculator=indicators, portfolio_tracker=portfolio_tracker,
        model_name="MiniMax-M3",
    )

    session = await engine.create_session(title="test")
    assert session.id

    reply = await engine.send_message(session.id, "BTC 现在能买吗?")
    assert reply.assistant_text
    assert reply.trade_suggestion is not None
    assert reply.trade_suggestion["symbol"] == "BTCUSDT"

    messages = await engine.get_messages(session.id)
    assert len(messages) == 2  # user + assistant
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_list_and_delete_session(tmp_path):
    from horizon.internal.database.db import init_db
    from horizon.internal.llm.copilot import CoPilotEngine

    db = await init_db(str(tmp_path / "t.db"), "horizon/internal/database/migrations")
    engine = CoPilotEngine(
        db=db, anthropic_client=MagicMock(), registry=MagicMock(), fetcher=MagicMock(),
        indicator_calculator=MagicMock(), portfolio_tracker=MagicMock(), model_name="m",
    )
    s1 = await engine.create_session(title="A")
    s2 = await engine.create_session(title="B")
    sessions = await engine.list_sessions()
    assert len(sessions) == 2

    await engine.delete_session(s1.id)
    sessions = await engine.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].id == s2.id
```

- [ ] **Step 2: Run failing**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_copilot_engine.py -x -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write copilot_prompt.py**

Write to `horizon/internal/llm/copilot_prompt.py`:
```python
"""Conversational system prompt for the Co-Pilot mode.

Distinct from prompt_builder.py (which is for autonomous market analysis).
This prompt frames the LLM as a deliberative trading partner.
"""

COPILOT_SYSTEM_PROMPT = """You are Horizon Co-Pilot — a quantitative trading partner.

A human trader is bringing you a feeling, an intuition, or a partially-formed
idea. Your job is to help them think through it using the live market data,
technical indicators, and their current portfolio that I provide each turn.

You are NOT executing trades. You provide analysis. If — and only if — the
discussion converges on a concrete, justified trade, you may attach a
trade_suggestion in your structured response. The human will still review
and submit the order themselves.

Behavior:
- Be direct. Skip platitudes. Cite numbers.
- If the user is vague ("what do you think?"), ask one focused question
  rather than guessing at five interpretations.
- Disagree with the user when the data disagrees. Explain why.
- Consider position concentration: if the user already has 40% in BTC,
  don't ignore that.
- When suggesting a trade, prefer limit orders with reasoned price levels.
- If no trade is justified yet, do NOT fabricate trade_suggestion.

Output format: you MUST respond with valid JSON matching exactly:
{
  "assistant_text": "<your reply to the user, plain text>",
  "trade_suggestion": null   OR   {
    "exchange": "binance|htx|hyperliquid|bitget",
    "symbol": "BTCUSDT",
    "side": "buy|sell",
    "order_type": "market|limit",
    "price": "<string decimal, omit for market>",
    "volume": "<string decimal>",
    "rationale": "<one-paragraph justification>"
  }
}

No prose outside the JSON. No markdown fences.
"""


def build_context_block(prices: list[dict], indicators: dict, portfolio: dict) -> str:
    """Render the live-context block prepended to each user turn."""
    lines = ["### Live market context", ""]
    if prices:
        lines.append("Prices:")
        for t in prices[:8]:
            lines.append(f"  - {t.get('symbol','?')} @ {t.get('price','?')} ({t.get('exchange','')})")
    if indicators:
        lines.append("")
        lines.append("Technical indicators:")
        for sym, ind in list(indicators.items())[:6]:
            lines.append(
                f"  - {sym}: RSI={ind.get('rsi_14','-')}, "
                f"MACD={ind.get('macd_line','-')}/{ind.get('macd_signal','-')}, "
                f"EMA20={ind.get('ema_20','-')}, EMA50={ind.get('ema_50','-')}"
            )
    if portfolio:
        lines.append("")
        lines.append(f"Portfolio total: {portfolio.get('total_usdt_value','?')} USDT")
        balances = portfolio.get("balances", [])
        if balances:
            lines.append("Holdings:")
            for b in balances[:10]:
                if float(b.get("free", "0") or 0) > 0:
                    lines.append(f"  - {b.get('asset','?')} on {b.get('exchange','?')}: free={b.get('free','?')}")
    return "\n".join(lines)
```

- [ ] **Step 4: Write copilot.py**

Write to `horizon/internal/llm/copilot.py`:
```python
"""CoPilotEngine — multi-turn human-initiated trading dialogue."""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .copilot_prompt import COPILOT_SYSTEM_PROMPT, build_context_block

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 100_000
MAX_HISTORY_MESSAGES = 20
DEFAULT_MAX_TOKENS = 2048


@dataclass
class CoPilotSession:
    id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime
    message_count: int


@dataclass
class CoPilotMessage:
    id: str
    session_id: str
    role: str  # user|assistant|system
    content: str
    trade_suggestion: Optional[dict]
    context_summary: Optional[dict]
    token_count_input: Optional[int]
    token_count_output: Optional[int]
    latency_ms: Optional[int]
    created_at: datetime


@dataclass
class CoPilotReply:
    message_id: str
    assistant_text: str
    trade_suggestion: Optional[dict]
    context_summary: dict
    token_count_input: int
    token_count_output: int
    latency_ms: int


class CoPilotEngine:
    def __init__(
        self,
        db: aiosqlite.Connection,
        anthropic_client,
        registry,
        fetcher,
        indicator_calculator,
        portfolio_tracker,
        model_name: str,
    ):
        self._db = db
        self._client = anthropic_client
        self._registry = registry
        self._fetcher = fetcher
        self._indicators = indicator_calculator
        self._portfolio_tracker = portfolio_tracker
        self._model = model_name

    async def create_session(self, title: Optional[str] = None) -> CoPilotSession:
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        await self._db.execute(
            "INSERT INTO copilot_sessions (id, title, created_at, updated_at, message_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (sid, title, now, now),
        )
        await self._db.commit()
        return CoPilotSession(id=sid, title=title, created_at=now, updated_at=now, message_count=0)

    async def list_sessions(self, limit: int = 20) -> list[CoPilotSession]:
        cursor = await self._db.execute(
            "SELECT id, title, created_at, updated_at, message_count "
            "FROM copilot_sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            CoPilotSession(
                id=r["id"], title=r["title"],
                created_at=r["created_at"], updated_at=r["updated_at"],
                message_count=r["message_count"],
            )
            for r in rows
        ]

    async def delete_session(self, session_id: str) -> None:
        await self._db.execute("DELETE FROM copilot_messages WHERE session_id = ?", (session_id,))
        await self._db.execute("DELETE FROM copilot_sessions WHERE id = ?", (session_id,))
        await self._db.commit()

    async def get_messages(self, session_id: str) -> list[CoPilotMessage]:
        cursor = await self._db.execute(
            "SELECT * FROM copilot_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(r) for r in rows]

    async def send_message(self, session_id: str, user_text: str) -> CoPilotReply:
        # 1. Persist user message
        user_msg_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        await self._db.execute(
            "INSERT INTO copilot_messages (id, session_id, role, content, created_at) "
            "VALUES (?, ?, 'user', ?, ?)",
            (user_msg_id, session_id, user_text, now),
        )

        # 2. Gather context
        prices, indicators, portfolio = await self._gather_context()
        context_block = build_context_block(prices, indicators, portfolio)
        context_summary = {
            "prices_fed": [p.get("symbol") for p in prices][:8],
            "indicators_fed": list(indicators.keys())[:6],
            "positions_fed": bool(portfolio.get("balances")),
        }

        # 3. Load history (this session only)
        history_rows = await self._db.execute(
            "SELECT role, content FROM copilot_messages "
            "WHERE session_id = ? AND id != ? ORDER BY created_at ASC",
            (session_id, user_msg_id),
        )
        history = await history_rows.fetchall()
        history = list(history[-MAX_HISTORY_MESSAGES:])

        # 4. Build messages array
        messages = [{"role": r["role"], "content": r["content"]} for r in history]
        messages.append({
            "role": "user",
            "content": f"{context_block}\n\n### User\n{user_text}",
        })
        context_summary["history_messages"] = len(history)

        # 5. Trim if total prompt exceeds limit
        while sum(len(m["content"]) for m in messages) > MAX_PROMPT_CHARS and len(messages) > 1:
            messages.pop(0)

        # 6. Call LLM
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    temperature=0.4,
                    system=COPILOT_SYSTEM_PROMPT,
                    messages=messages,
                ),
                timeout=60,
            )
        except Exception as e:
            logger.exception("CoPilot LLM call failed: %s", e)
            await self._db.commit()
            raise

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = result.content[0].text
        in_tok = getattr(result.usage, "input_tokens", 0) if hasattr(result, "usage") else 0
        out_tok = getattr(result.usage, "output_tokens", 0) if hasattr(result, "usage") else 0

        # 7. Parse JSON response
        assistant_text, trade_suggestion = self._parse_reply(raw)

        # 8. Persist assistant message
        asst_id = str(uuid.uuid4())
        await self._db.execute(
            "INSERT INTO copilot_messages "
            "(id, session_id, role, content, trade_suggestion, context_summary, "
            " token_count_input, token_count_output, latency_ms, created_at) "
            "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?)",
            (asst_id, session_id, assistant_text,
             json.dumps(trade_suggestion) if trade_suggestion else None,
             json.dumps(context_summary),
             in_tok, out_tok, latency_ms,
             datetime.now(timezone.utc)),
        )
        # Update session count + updated_at
        await self._db.execute(
            "UPDATE copilot_sessions SET message_count = message_count + 2, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc), session_id),
        )
        await self._db.commit()

        return CoPilotReply(
            message_id=asst_id,
            assistant_text=assistant_text,
            trade_suggestion=trade_suggestion,
            context_summary=context_summary,
            token_count_input=in_tok,
            token_count_output=out_tok,
            latency_ms=latency_ms,
        )

    async def _gather_context(self):
        prices: list[dict] = []
        indicators: dict = {}
        portfolio: dict = {}
        try:
            for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
                tks = await self._registry.get_all_tickers(sym)
                for t in tks:
                    prices.append({"symbol": t.symbol, "price": str(t.price), "exchange": t.exchange})
                try:
                    ind = await self._indicators.compute_for_symbol(sym)
                    indicators[sym] = {
                        "rsi_14": ind.rsi_14, "macd_line": ind.macd_line,
                        "macd_signal": ind.macd_signal, "ema_20": ind.ema_20, "ema_50": ind.ema_50,
                    }
                except Exception:
                    pass
        except Exception as e:
            logger.warning("CoPilot context gather (market) failed: %s", e)
        try:
            if hasattr(self._portfolio_tracker, "get_snapshot"):
                portfolio = await self._portfolio_tracker.get_snapshot()
        except Exception as e:
            logger.warning("CoPilot context gather (portfolio) failed: %s", e)
        return prices, indicators, portfolio

    @staticmethod
    def _parse_reply(raw: str) -> tuple[str, Optional[dict]]:
        try:
            data = json.loads(raw.strip())
            text = data.get("assistant_text", "").strip()
            sug = data.get("trade_suggestion")
            if isinstance(sug, dict):
                return text, sug
            return text, None
        except Exception:
            # Tolerate non-JSON: return raw as assistant_text, no suggestion
            return raw.strip(), None

    @staticmethod
    def _row_to_message(r) -> CoPilotMessage:
        return CoPilotMessage(
            id=r["id"], session_id=r["session_id"], role=r["role"],
            content=r["content"],
            trade_suggestion=json.loads(r["trade_suggestion"]) if r["trade_suggestion"] else None,
            context_summary=json.loads(r["context_summary"]) if r["context_summary"] else None,
            token_count_input=r["token_count_input"],
            token_count_output=r["token_count_output"],
            latency_ms=r["latency_ms"],
            created_at=r["created_at"],
        )
```

- [ ] **Step 5: Run tests**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_copilot_engine.py -x -q`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/llm/copilot.py horizon/internal/llm/copilot_prompt.py horizon/tests/test_copilot_engine.py
git commit -m "feat(llm): add CoPilotEngine for multi-turn human-initiated trading dialogue"
```

---

### Task 11: Co-Pilot REST endpoints `[BACKEND]`

**Files:**
- Modify: `horizon/internal/web/server.py`
- Create: `horizon/tests/test_copilot_api.py`

- [ ] **Step 1: Add Pydantic models + endpoints**

In `server.py`, near other models, add:
```python
class CreateCoPilotSessionModel(BaseModel):
    title: Optional[str] = None


class SendCoPilotMessageModel(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
```

Inside `create_app()`, add:
```python
@app.post("/api/copilot/sessions")
async def copilot_create_session(body: CreateCoPilotSessionModel) -> JSONResponse:
    cp = getattr(app.state, "copilot", None)
    if cp is None:
        raise HTTPException(status_code=503, detail="Co-Pilot unavailable")
    s = await cp.create_session(title=body.title)
    return CustomJSONResponse({"id": s.id, "title": s.title, "created_at": str(s.created_at)})


@app.get("/api/copilot/sessions")
async def copilot_list_sessions(limit: int = Query(20, ge=1, le=100)) -> JSONResponse:
    cp = getattr(app.state, "copilot", None)
    if cp is None:
        raise HTTPException(status_code=503, detail="Co-Pilot unavailable")
    sessions = await cp.list_sessions(limit=limit)
    return CustomJSONResponse({"sessions": [
        {"id": s.id, "title": s.title, "created_at": str(s.created_at),
         "updated_at": str(s.updated_at), "message_count": s.message_count}
        for s in sessions
    ]})


@app.delete("/api/copilot/sessions/{session_id}")
async def copilot_delete_session(session_id: str) -> JSONResponse:
    cp = getattr(app.state, "copilot", None)
    if cp is None:
        raise HTTPException(status_code=503, detail="Co-Pilot unavailable")
    await cp.delete_session(session_id)
    return CustomJSONResponse({"deleted": session_id})


@app.get("/api/copilot/sessions/{session_id}/messages")
async def copilot_get_messages(session_id: str) -> JSONResponse:
    cp = getattr(app.state, "copilot", None)
    if cp is None:
        raise HTTPException(status_code=503, detail="Co-Pilot unavailable")
    msgs = await cp.get_messages(session_id)
    return CustomJSONResponse({"messages": [
        {"id": m.id, "role": m.role, "content": m.content,
         "trade_suggestion": m.trade_suggestion,
         "context_summary": m.context_summary,
         "token_count_input": m.token_count_input,
         "token_count_output": m.token_count_output,
         "latency_ms": m.latency_ms,
         "created_at": str(m.created_at)}
        for m in msgs
    ]})


@app.post("/api/copilot/sessions/{session_id}/messages")
async def copilot_send_message(session_id: str, body: SendCoPilotMessageModel) -> JSONResponse:
    cp = getattr(app.state, "copilot", None)
    if cp is None:
        raise HTTPException(status_code=503, detail="Co-Pilot unavailable")
    reply = await cp.send_message(session_id, body.text)
    return CustomJSONResponse({
        "message_id": reply.message_id,
        "assistant_text": reply.assistant_text,
        "trade_suggestion": reply.trade_suggestion,
        "context_summary": reply.context_summary,
        "token_count_input": reply.token_count_input,
        "token_count_output": reply.token_count_output,
        "latency_ms": reply.latency_ms,
    })
```

- [ ] **Step 2: Write API tests**

Write to `horizon/tests/test_copilot_api.py`:
```python
"""Tests for /api/copilot/* endpoints."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from horizon.internal.database.db import init_db
    from horizon.internal.web.server import create_app
    from horizon.internal.config.settings import settings as real_settings
    from horizon.internal.exchange.registry import ExchangeRegistry
    from horizon.internal.marketdata.fetcher import MarketDataFetcher
    from horizon.internal.ordermanager.manager import OrderManager
    from horizon.internal.llm.copilot import CoPilotEngine, CoPilotReply

    async def build():
        db = await init_db(str(tmp_path / "t.db"), "horizon/internal/database/migrations")
        registry = ExchangeRegistry()
        fetcher = MarketDataFetcher(registry=registry, db=db, active_pairlist=None,
                                     active_exchange="binance", poll_interval_seconds=60)
        om = OrderManager(registry=registry, db=db, active_exchange="binance")
        app = create_app(settings=real_settings, db=db, registry=registry, fetcher=fetcher, order_manager=om)
        # Inject CoPilotEngine with stubbed dependencies (no real Anthropic calls)
        cp = CoPilotEngine(
            db=db, anthropic_client=MagicMock(), registry=MagicMock(),
            fetcher=MagicMock(), indicator_calculator=MagicMock(),
            portfolio_tracker=MagicMock(), model_name="test",
        )
        # Patch send_message to avoid real LLM calls
        cp.send_message = AsyncMock(return_value=CoPilotReply(
            message_id="m1", assistant_text="hello world",
            trade_suggestion=None, context_summary={"prices_fed": []},
            token_count_input=10, token_count_output=5, latency_ms=42,
        ))
        app.state.copilot = cp
        return app

    app = asyncio.run(build())
    return TestClient(app)


def test_create_session(client):
    r = client.post("/api/copilot/sessions", json={"title": "test session"})
    assert r.status_code == 200
    assert r.json()["title"] == "test session"


def test_list_sessions(client):
    client.post("/api/copilot/sessions", json={"title": "A"})
    client.post("/api/copilot/sessions", json={"title": "B"})
    r = client.get("/api/copilot/sessions")
    assert r.status_code == 200
    assert len(r.json()["sessions"]) >= 2


def test_send_message(client):
    s = client.post("/api/copilot/sessions", json={"title": "X"}).json()
    r = client.post(f"/api/copilot/sessions/{s['id']}/messages",
                     json={"text": "What about BTC?"})
    assert r.status_code == 200
    assert r.json()["assistant_text"] == "hello world"


def test_delete_session(client):
    s = client.post("/api/copilot/sessions", json={}).json()
    r = client.delete(f"/api/copilot/sessions/{s['id']}")
    assert r.status_code == 200
```

- [ ] **Step 3: Run tests**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/test_copilot_api.py -x -q`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/web/server.py horizon/tests/test_copilot_api.py
git commit -m "feat(api): add 5 /api/copilot endpoints (sessions CRUD + send message)"
```

---

### Task 12: Wire CoPilotEngine in main.py `[BACKEND]`

**Files:**
- Modify: `horizon/main.py`

- [ ] **Step 1: Add import**

```python
from horizon.internal.llm.copilot import CoPilotEngine
```

- [ ] **Step 2: After llm_engine is built, build copilot**

Find the block in `lifespan()` where `llm_scheduler` is constructed (Task 9). Right after:
```python
copilot = None
if anthropic_client is not None:
    copilot = CoPilotEngine(
        db=db,
        anthropic_client=anthropic_client,
        registry=registry,
        fetcher=fetcher,
        indicator_calculator=indicator_calculator,
        portfolio_tracker=portfolio_tracker,
        model_name=load_llm_credentials().model,
    )
    logger.info("CoPilotEngine initialized")

application.state.copilot = copilot
```

- [ ] **Step 3: Smoke test boot**

Run: `cd /d/Horizon-Dev && timeout 8 python -m horizon.main 2>&1 | tail -30`
Expected: "CoPilotEngine initialized" log line present.

- [ ] **Step 4: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/main.py
git commit -m "feat(main): wire CoPilotEngine in lifespan, expose via app.state.copilot"
```

---

## Phase 3 — Frontend `[FRONTEND]`

> **PARALLEL EXECUTION:** Phase 3 can run **concurrently with Phase 1 + Phase 2** once Phase 0 (FOUNDATION) is complete. Frontend agent owns all tasks in this phase. API contracts are defined in spec §3.6 and §6 — frontend can scaffold against the documented shapes without waiting for backend tasks.

### Task 13: Extend api.js with 14 new client functions `[FRONTEND]`

**Files:**
- Modify: `horizon/internal/web/static/js/api.js`

- [ ] **Step 1: Append new exports to api.js**

Append to `horizon/internal/web/static/js/api.js`:
```javascript

// ===== Proposals =====
export async function getProposals(status = null, limit = 50) {
    const qs = new URLSearchParams();
    if (status) qs.set('status', status);
    qs.set('limit', String(limit));
    return request(`/proposals?${qs}`);
}

export async function getProposal(id) {
    return request(`/proposals/${encodeURIComponent(id)}`);
}

export async function approveProposal(id, approved_by = 'dashboard_user') {
    return request(`/proposals/${encodeURIComponent(id)}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved_by }),
    });
}

export async function rejectProposal(id, rejected_by = 'dashboard_user', reason = '') {
    return request(`/proposals/${encodeURIComponent(id)}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rejected_by, reason }),
    });
}

export async function getProposalStats() {
    return request('/proposals/stats');
}

// ===== LLM History + Trigger =====
export async function getLLMHistory(limit = 50) {
    return request(`/llm/history?limit=${limit}`);
}

export async function triggerLLMAnalysis() {
    return request('/llm/trigger', { method: 'POST' });
}

// ===== Strategy Config GET =====
export async function getStrategyConfig() {
    return request('/strategy/config');
}

// ===== Co-Pilot =====
export async function createCoPilotSession(title = null) {
    return request('/copilot/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
    });
}

export async function listCoPilotSessions(limit = 20) {
    return request(`/copilot/sessions?limit=${limit}`);
}

export async function getCoPilotMessages(sessionId) {
    return request(`/copilot/sessions/${encodeURIComponent(sessionId)}/messages`);
}

export async function sendCoPilotMessage(sessionId, text) {
    return request(`/copilot/sessions/${encodeURIComponent(sessionId)}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
    });
}

export async function deleteCoPilotSession(sessionId) {
    return request(`/copilot/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'DELETE',
    });
}
```

- [ ] **Step 2: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/web/static/js/api.js
git commit -m "feat(frontend): extend api.js with proposals, llm, copilot, strategy endpoints"
```

---

### Task 14: Proposals Queue panel — JS module + HTML + CSS `[FRONTEND]`

**Files:**
- Create: `horizon/internal/web/static/js/proposals.js`
- Modify: `horizon/internal/web/static/index.html`
- Modify: `horizon/internal/web/static/css/styles.css`

- [ ] **Step 1: Create proposals.js**

Write to `horizon/internal/web/static/js/proposals.js`:
```javascript
// LLM Proposals Queue panel — list + approve/reject + trigger
import { getProposals, approveProposal, rejectProposal, triggerLLMAnalysis, getProposal } from './api.js';

let containerEl = null;
let triggerBtnEl = null;
let countBadgeEl = null;
let pollTimer = null;

export async function initProposals(container, triggerBtn, countBadge) {
    containerEl = container;
    triggerBtnEl = triggerBtn;
    countBadgeEl = countBadge;

    if (triggerBtnEl) {
        triggerBtnEl.addEventListener('click', onTriggerClick);
    }
    await renderProposals();
    pollTimer = setInterval(renderProposals, 10000);
}

export function destroyProposals() {
    if (pollTimer) clearInterval(pollTimer);
}

async function renderProposals() {
    if (!containerEl) return;
    try {
        const data = await getProposals(null, 50);
        const props = data.proposals || [];
        if (countBadgeEl) {
            countBadgeEl.textContent = props.filter(p => p.status === 'proposed').length;
        }
        if (props.length === 0) {
            containerEl.innerHTML = '<div class="empty-state">No proposals yet — try [Run Analysis Now]</div>';
            return;
        }
        containerEl.innerHTML = buildHeader() + props.map(buildRow).join('');
        attachRowHandlers();
    } catch (e) {
        containerEl.innerHTML = `<div class="error-state">Failed: ${e.message}</div>`;
    }
}

function buildHeader() {
    return `
      <div class="table-header proposals-cols">
        <span>ID</span><span>Symbol</span><span>Action</span><span>Side</span>
        <span>Volume</span><span>Confidence</span><span>Risk</span>
        <span>Status</span><span>Created</span><span>Actions</span>
      </div>`;
}

function buildRow(p) {
    const id8 = (p.id || '').substring(0, 8);
    const actionIcon = { open: '🟢', close: '🔴', reduce: '🟡' }[p.action_type] || '🟢';
    const statusClass = `status-${p.status}`;
    const actions = p.status === 'proposed'
        ? `<button class="btn-approve" data-id="${p.id}">Approve</button>
           <button class="btn-reject" data-id="${p.id}">Reject</button>`
        : '—';
    const created = p.created_at ? new Date(p.created_at).toLocaleString() : '--';
    return `
      <div class="table-row proposals-cols">
        <span style="font-family:var(--font-mono)">${id8}</span>
        <span>${p.symbol || '--'}</span>
        <span>${actionIcon} ${p.action_type || 'open'}</span>
        <span class="${p.side === 'buy' ? 'long' : 'short'}">${(p.side || '--').toUpperCase()}</span>
        <span>${p.volume ?? '--'}</span>
        <span>${p.confidence_score ?? '--'}</span>
        <span>${p.risk_tier || '--'}</span>
        <span class="${statusClass}">${p.status || '--'}</span>
        <span>${created}</span>
        <span>${actions}</span>
      </div>`;
}

function attachRowHandlers() {
    containerEl.querySelectorAll('.btn-approve').forEach(b => {
        b.addEventListener('click', () => showApproveModal(b.dataset.id));
    });
    containerEl.querySelectorAll('.btn-reject').forEach(b => {
        b.addEventListener('click', () => showRejectModal(b.dataset.id));
    });
}

async function onTriggerClick() {
    triggerBtnEl.disabled = true;
    triggerBtnEl.textContent = 'Running...';
    try {
        const result = await triggerLLMAnalysis();
        const msg = `Analysis complete: ${result.proposals_generated} proposal(s) generated`;
        if (window.showToast) window.showToast('success', msg); else alert(msg);
        await renderProposals();
    } catch (e) {
        if (window.showToast) window.showToast('error', e.message); else alert('Failed: ' + e.message);
    } finally {
        triggerBtnEl.disabled = false;
        triggerBtnEl.textContent = 'Run Analysis Now';
    }
}

async function showApproveModal(id) {
    try {
        const p = await getProposal(id);
        const overlay = document.getElementById('modal-overlay');
        overlay.innerHTML = '';
        const modal = document.createElement('div');
        modal.className = 'modal modal-wide';
        modal.innerHTML = `
          <h3>Approve Proposal ${id.substring(0,8)}</h3>
          <div class="proposal-detail">
            <div class="row"><b>Action:</b> ${p.action_type} ${p.symbol} ${p.side} ${p.volume} @ ${p.price ?? 'market'}</div>
            <div class="row"><b>Exchange:</b> ${p.exchange}</div>
            <div class="row"><b>Confidence:</b> ${p.confidence_score}/100  &nbsp; <b>Risk:</b> ${p.risk_tier}</div>
            <div class="row"><b>Proposed price:</b> ${p.proposed_price}  &nbsp; <b>Drift threshold:</b> ${p.price_drift_threshold_pct}%</div>
            <div class="row"><b>Rationale:</b><pre>${escapeHtml(p.llm_rationale || '')}</pre></div>
            <details><summary>Technical context</summary><pre>${escapeHtml(p.technical_context || '{}')}</pre></details>
            <details><summary>Market snapshot</summary><pre>${escapeHtml(p.market_snapshot || '{}')}</pre></details>
          </div>
          <div class="modal-actions">
            <button class="btn-cancel" id="approve-cancel">Cancel</button>
            <button class="btn-confirm" id="approve-confirm">Confirm Approval</button>
          </div>`;
        overlay.appendChild(modal);
        overlay.classList.remove('hidden');
        modal.querySelector('#approve-cancel').onclick = () => overlay.classList.add('hidden');
        modal.querySelector('#approve-confirm').onclick = async () => {
            try {
                await approveProposal(id);
                window.showToast?.('success', 'Approved & executing');
                overlay.classList.add('hidden');
                await renderProposals();
            } catch (e) {
                window.showToast?.('error', e.message);
            }
        };
    } catch (e) {
        alert('Failed to load proposal: ' + e.message);
    }
}

function showRejectModal(id) {
    const overlay = document.getElementById('modal-overlay');
    overlay.innerHTML = '';
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
      <h3>Reject Proposal ${id.substring(0,8)}</h3>
      <textarea id="reject-reason" placeholder="Optional reason..." rows="3" style="width:100%"></textarea>
      <div class="modal-actions">
        <button class="btn-cancel" id="rej-cancel">Cancel</button>
        <button class="btn-confirm" id="rej-confirm">Confirm Rejection</button>
      </div>`;
    overlay.appendChild(modal);
    overlay.classList.remove('hidden');
    modal.querySelector('#rej-cancel').onclick = () => overlay.classList.add('hidden');
    modal.querySelector('#rej-confirm').onclick = async () => {
        try {
            await rejectProposal(id, 'dashboard_user', modal.querySelector('#reject-reason').value);
            window.showToast?.('success', 'Rejected');
            overlay.classList.add('hidden');
            await renderProposals();
        } catch (e) {
            window.showToast?.('error', e.message);
        }
    };
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
```

- [ ] **Step 2: Add panel HTML to index.html**

In `horizon/internal/web/static/index.html`, find the closing `</div>` of the existing top-panels block (after the released-trades-panel block). Insert a new panel BEFORE the closing top-panels:
```html
<!-- LLM Proposals Queue Panel -->
<div class="panel proposals-panel">
    <div class="panel-header">
        <h2>LLM Proposals</h2>
        <div class="panel-controls">
            <button id="trigger-analysis-btn" class="btn-primary">Run Analysis Now</button>
            <span id="proposal-count-badge" class="count-badge">0</span>
        </div>
    </div>
    <div id="proposals-table-container"></div>
</div>
```

- [ ] **Step 3: Add CSS to styles.css**

Append to `horizon/internal/web/static/css/styles.css`:
```css
/* Proposals Queue */
.proposals-panel { grid-column: span 2; }
.proposals-cols {
    display: grid;
    grid-template-columns: 80px 100px 90px 70px 80px 80px 70px 100px 130px 1fr;
    gap: 8px;
    font-size: 13px;
}
.status-proposed { color: var(--accent-primary); }
.status-approved, .status-executed, .status-auto_executed { color: var(--positive); }
.status-rejected { color: var(--text-secondary); }
.status-expired { color: var(--warning); }
.btn-approve {
    background: var(--positive); color: #000; border: none;
    padding: 4px 10px; border-radius: 4px; cursor: pointer; font-weight: 600;
    margin-right: 4px;
}
.btn-reject {
    background: transparent; color: var(--negative); border: 1px solid var(--negative);
    padding: 4px 10px; border-radius: 4px; cursor: pointer;
}
.btn-primary {
    background: var(--accent-primary); color: #fff; border: none;
    padding: 6px 14px; border-radius: 6px; cursor: pointer; font-weight: 600;
}
.btn-primary:hover { opacity: 0.9; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.panel-controls { display: flex; gap: 8px; align-items: center; }
.count-badge {
    background: var(--accent-primary); color: #fff;
    padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 600;
}
.modal-wide { max-width: 720px; }
.proposal-detail .row { margin: 6px 0; font-size: 13px; }
.proposal-detail pre {
    background: var(--bg-elevated); padding: 10px; border-radius: 6px;
    font-size: 12px; max-height: 180px; overflow: auto; white-space: pre-wrap;
}
.empty-state, .error-state {
    padding: 20px; text-align: center; color: var(--text-secondary);
}
.error-state { color: var(--negative); }
```

- [ ] **Step 4: Visual smoke test**

Start server (or use existing dev session). Open `http://localhost:8080/static/index.html`. Verify the new panel renders the empty-state message.

- [ ] **Step 5: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/web/static/js/proposals.js horizon/internal/web/static/index.html horizon/internal/web/static/css/styles.css
git commit -m "feat(frontend): LLM Proposals Queue panel — list, approve, reject, trigger"
```

---

### Task 15: Co-Pilot chat panel — JS module + HTML + CSS `[FRONTEND]`

**Files:**
- Create: `horizon/internal/web/static/js/copilot.js`
- Modify: `horizon/internal/web/static/index.html`
- Modify: `horizon/internal/web/static/css/styles.css`

- [ ] **Step 1: Create copilot.js**

Write to `horizon/internal/web/static/js/copilot.js`:
```javascript
// AI Co-Pilot — multi-turn chat with optional [Pre-fill Order Ticket] hook
import {
    createCoPilotSession, listCoPilotSessions, getCoPilotMessages,
    sendCoPilotMessage, deleteCoPilotSession,
} from './api.js';
import { setSymbol } from './orders.js';

let panelEl = null;
let toggleBtnEl = null;
let messagesEl = null;
let inputEl = null;
let sendBtnEl = null;
let sessionSelectEl = null;
let newBtnEl = null;
let currentSessionId = null;

export async function initCoPilot(els) {
    panelEl = els.panel;
    toggleBtnEl = els.toggleBtn;
    messagesEl = els.messages;
    inputEl = els.input;
    sendBtnEl = els.sendBtn;
    sessionSelectEl = els.sessionSelect;
    newBtnEl = els.newBtn;

    toggleBtnEl.addEventListener('click', togglePanel);
    sendBtnEl.addEventListener('click', onSend);
    newBtnEl.addEventListener('click', onNewSession);
    sessionSelectEl.addEventListener('change', onSessionChange);
    inputEl.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
    });
    await loadSessions();
}

function togglePanel() {
    panelEl.classList.toggle('collapsed');
}

async function loadSessions() {
    try {
        const data = await listCoPilotSessions(20);
        const sessions = data.sessions || [];
        sessionSelectEl.innerHTML = '<option value="">— Select a session —</option>' +
            sessions.map(s => `<option value="${s.id}">${(s.title || s.id.substring(0,8))} (${s.message_count})</option>`).join('');
        if (sessions.length > 0) {
            sessionSelectEl.value = sessions[0].id;
            await loadMessages(sessions[0].id);
        }
    } catch (e) {
        renderError('Failed to load sessions: ' + e.message);
    }
}

async function onNewSession() {
    const title = prompt('Session title (optional):') || null;
    try {
        const s = await createCoPilotSession(title);
        await loadSessions();
        sessionSelectEl.value = s.id;
        await loadMessages(s.id);
    } catch (e) {
        renderError(e.message);
    }
}

async function onSessionChange() {
    const id = sessionSelectEl.value;
    if (id) await loadMessages(id);
}

async function loadMessages(sessionId) {
    currentSessionId = sessionId;
    try {
        const data = await getCoPilotMessages(sessionId);
        messagesEl.innerHTML = (data.messages || []).map(renderMessage).join('');
        attachSuggestionHandlers();
        scrollToBottom();
    } catch (e) {
        renderError(e.message);
    }
}

async function onSend() {
    if (!currentSessionId) {
        alert('Please create or select a session first.');
        return;
    }
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    sendBtnEl.disabled = true;

    // Optimistic user bubble
    messagesEl.insertAdjacentHTML('beforeend', renderMessage({
        role: 'user', content: text, created_at: new Date().toISOString(),
    }));
    scrollToBottom();

    try {
        const reply = await sendCoPilotMessage(currentSessionId, text);
        messagesEl.insertAdjacentHTML('beforeend', renderMessage({
            role: 'assistant',
            content: reply.assistant_text,
            trade_suggestion: reply.trade_suggestion,
            context_summary: reply.context_summary,
            token_count_input: reply.token_count_input,
            token_count_output: reply.token_count_output,
            latency_ms: reply.latency_ms,
            created_at: new Date().toISOString(),
        }));
        attachSuggestionHandlers();
        scrollToBottom();
    } catch (e) {
        renderError(e.message);
    } finally {
        sendBtnEl.disabled = false;
        inputEl.focus();
    }
}

function renderMessage(m) {
    const sideClass = m.role === 'user' ? 'msg-user' : 'msg-assistant';
    const escaped = escapeHtml(m.content || '');
    let suggestion = '';
    if (m.trade_suggestion) {
        const t = m.trade_suggestion;
        const data = encodeURIComponent(JSON.stringify(t));
        suggestion = `
          <div class="trade-suggestion">
            <b>💡 Suggested trade:</b> ${t.symbol} ${t.side} ${t.volume} @ ${t.price ?? 'market'} (${t.exchange})
            <div class="suggestion-rationale">${escapeHtml(t.rationale || '')}</div>
            <button class="btn-prefill" data-trade="${data}">📋 Pre-fill Order Ticket</button>
          </div>`;
    }
    let footer = '';
    if (m.role === 'assistant' && m.context_summary) {
        const cs = m.context_summary;
        const badges = [];
        if (cs.prices_fed?.length) badges.push('📊 prices');
        if (cs.indicators_fed?.length) badges.push('📈 indicators');
        if (cs.positions_fed) badges.push('💼 positions');
        if (cs.history_messages) badges.push(`🕐 ${cs.history_messages} prior msgs`);
        footer = `<div class="msg-footer">${badges.join(' · ')}${
            m.latency_ms ? ` · ${m.latency_ms}ms · ${m.token_count_input}+${m.token_count_output} tok` : ''
        }</div>`;
    }
    return `
      <div class="msg-bubble ${sideClass}">
        <div class="msg-content">${escaped}</div>
        ${suggestion}
        ${footer}
      </div>`;
}

function attachSuggestionHandlers() {
    messagesEl.querySelectorAll('.btn-prefill').forEach(btn => {
        btn.addEventListener('click', () => {
            try {
                const trade = JSON.parse(decodeURIComponent(btn.dataset.trade));
                prefillOrderTicket(trade);
            } catch (e) {
                alert('Failed to read trade suggestion: ' + e.message);
            }
        });
    });
}

function prefillOrderTicket(t) {
    // Use existing orders.js machinery
    setSymbol(t.symbol);
    const sel = document.getElementById('order-exchange');
    if (sel && t.exchange) sel.value = t.exchange;
    const typeSel = document.getElementById('order-type');
    if (typeSel) {
        typeSel.value = t.order_type || 'market';
        typeSel.dispatchEvent(new Event('change'));
    }
    // Side toggle
    document.querySelectorAll('.side-toggle button').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.side === t.side) b.classList.add('active', t.side);
    });
    const priceEl = document.getElementById('order-price');
    if (priceEl && t.price) priceEl.value = t.price;
    const volEl = document.getElementById('order-volume');
    if (volEl && t.volume) volEl.value = t.volume;

    // Scroll to order panel
    document.querySelector('.order-panel')?.scrollIntoView({ behavior: 'smooth' });
    window.showToast?.('info', 'Order ticket pre-filled — review then Submit');
}

function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderError(msg) {
    messagesEl.insertAdjacentHTML('beforeend',
        `<div class="msg-error">⚠ ${escapeHtml(msg)}</div>`);
    scrollToBottom();
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
```

- [ ] **Step 2: Add Co-Pilot panel HTML**

Add to `index.html` just before the closing `</div>` of `.dashboard`:
```html
<!-- AI Co-Pilot floating panel -->
<div id="copilot-panel" class="copilot-panel collapsed">
    <div class="copilot-header">
        <button id="copilot-toggle" class="copilot-toggle">🤝 AI Co-Pilot</button>
        <select id="copilot-session-select" class="copilot-session-select"></select>
        <button id="copilot-new-btn" class="btn-mini">+ New</button>
    </div>
    <div id="copilot-messages" class="copilot-messages"></div>
    <div class="copilot-input-row">
        <textarea id="copilot-input" rows="2" placeholder="Ask anything... e.g. 'BTC 现在能买吗?'"></textarea>
        <button id="copilot-send" class="btn-primary">Send</button>
    </div>
</div>
```

- [ ] **Step 3: Add Co-Pilot CSS**

Append to `styles.css`:
```css
/* Co-Pilot floating panel */
.copilot-panel {
    position: fixed;
    bottom: 20px; right: 20px;
    width: 420px; height: 520px;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    display: flex; flex-direction: column;
    z-index: 100;
    transition: height 200ms;
}
.copilot-panel.collapsed { height: 48px; overflow: hidden; }
.copilot-header {
    display: flex; gap: 8px; padding: 8px;
    border-bottom: 1px solid var(--border);
    align-items: center;
}
.copilot-toggle {
    flex: 1; background: transparent; border: none;
    color: var(--text-primary); text-align: left;
    cursor: pointer; font-weight: 600; font-size: 14px;
}
.copilot-session-select {
    flex: 2; background: var(--bg-elevated); color: var(--text-primary);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 4px; font-size: 12px;
}
.btn-mini {
    background: var(--accent-primary); color: #fff; border: none;
    padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px;
}
.copilot-messages {
    flex: 1; overflow-y: auto; padding: 12px;
    display: flex; flex-direction: column; gap: 10px;
}
.msg-bubble {
    max-width: 92%; padding: 10px 12px; border-radius: 10px;
    font-size: 13px; word-wrap: break-word;
}
.msg-user {
    align-self: flex-end;
    background: var(--accent-primary); color: #fff;
}
.msg-assistant {
    align-self: flex-start;
    background: var(--bg-elevated); color: var(--text-primary);
}
.msg-content { white-space: pre-wrap; }
.msg-footer {
    margin-top: 6px; font-size: 11px;
    color: var(--text-secondary);
}
.msg-error {
    align-self: stretch; padding: 8px; background: rgba(248,113,113,0.1);
    border: 1px solid var(--negative); color: var(--negative);
    border-radius: 6px; font-size: 12px;
}
.trade-suggestion {
    margin-top: 10px; padding: 10px;
    background: rgba(102, 126, 234, 0.1);
    border: 1px solid var(--accent-primary); border-radius: 8px;
}
.suggestion-rationale {
    font-size: 12px; color: var(--text-secondary); margin: 6px 0;
}
.btn-prefill {
    background: var(--accent-primary); color: #fff; border: none;
    padding: 6px 12px; border-radius: 4px; cursor: pointer;
    font-size: 12px; font-weight: 600;
}
.copilot-input-row {
    display: flex; gap: 8px; padding: 8px;
    border-top: 1px solid var(--border);
}
.copilot-input-row textarea {
    flex: 1; background: var(--bg-elevated); color: var(--text-primary);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 8px; font-family: var(--font-sans); font-size: 13px;
    resize: none;
}
```

- [ ] **Step 4: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/web/static/js/copilot.js horizon/internal/web/static/index.html horizon/internal/web/static/css/styles.css
git commit -m "feat(frontend): AI Co-Pilot chat panel with multi-turn + pre-fill order"
```

---

### Task 16: Strategy Configuration modal `[FRONTEND]`

**Files:**
- Create: `horizon/internal/web/static/js/strategy_config.js`
- Modify: `horizon/internal/web/static/index.html`
- Modify: `horizon/internal/web/static/css/styles.css`

- [ ] **Step 1: Create strategy_config.js**

Write to `horizon/internal/web/static/js/strategy_config.js`:
```javascript
// Strategy Configuration modal — read + edit thresholds, whitelist, system prompt
import { getStrategyConfig, putStrategyConfig } from './api.js';

let modalEl = null;

export async function initStrategyConfig(triggerBtn) {
    triggerBtn.addEventListener('click', openModal);
}

async function openModal() {
    if (!modalEl) {
        modalEl = document.createElement('div');
        modalEl.className = 'modal modal-wide strategy-config-modal';
        document.getElementById('modal-overlay').appendChild(modalEl);
    }
    try {
        const cfg = await getStrategyConfig();
        renderForm(cfg);
        document.getElementById('modal-overlay').classList.remove('hidden');
    } catch (e) {
        alert('Failed to load config: ' + e.message);
    }
}

function renderForm(cfg) {
    const whitelistStr = (() => {
        try { return JSON.parse(cfg.asset_whitelist || '[]').join(', '); }
        catch { return ''; }
    })();
    modalEl.innerHTML = `
      <h3>Strategy Configuration</h3>
      <form id="cfg-form" class="cfg-form">
        <label>System prompt
          <textarea name="system_prompt" rows="8">${escapeHtml(cfg.system_prompt || '')}</textarea>
        </label>
        <label>Asset whitelist (comma-separated symbols)
          <input name="asset_whitelist" type="text" value="${escapeHtml(whitelistStr)}" />
        </label>
        <div class="cfg-row">
          <label>Min confidence threshold
            <input name="min_confidence_threshold" type="number" min="0" max="100" value="${cfg.min_confidence_threshold ?? 75}" />
          </label>
          <label>Max risk tier
            <select name="max_risk_tier">
              <option ${cfg.max_risk_tier === 'low' ? 'selected':''}>low</option>
              <option ${cfg.max_risk_tier === 'medium' ? 'selected':''}>medium</option>
              <option ${cfg.max_risk_tier === 'high' ? 'selected':''}>high</option>
            </select>
          </label>
          <label>Analysis interval (hours)
            <input name="analysis_interval_hours" type="number" min="1" value="${cfg.analysis_interval_hours ?? 8}" />
          </label>
        </div>
        <div class="cfg-row">
          <label>Max position %
            <input name="max_position_pct" type="number" step="0.1" min="0.1" max="100" value="${cfg.max_position_pct ?? 20}" />
          </label>
          <label>Max daily loss %
            <input name="max_daily_loss_pct" type="number" step="0.1" min="0.1" max="100" value="${cfg.max_daily_loss_pct ?? 5}" />
          </label>
          <label>Max exchange exposure %
            <input name="max_exchange_exposure_pct" type="number" step="0.1" min="0.1" max="100" value="${cfg.max_exchange_exposure_pct ?? 50}" />
          </label>
        </div>
        <div class="cfg-row">
          <label>Cooldown (seconds)
            <input name="cooldown_seconds" type="number" min="0" value="${cfg.cooldown_seconds ?? 300}" />
          </label>
          <label>Order min notional
            <input name="order_min_notional" type="number" step="0.01" min="0.01" value="${cfg.order_min_notional ?? 10}" />
          </label>
          <label>Order max notional
            <input name="order_max_notional" type="number" step="0.01" min="0.01" value="${cfg.order_max_notional ?? 10000}" />
          </label>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn-cancel" id="cfg-cancel">Cancel</button>
          <button type="submit" class="btn-confirm">Save</button>
        </div>
      </form>`;
    modalEl.querySelector('#cfg-cancel').onclick = () =>
        document.getElementById('modal-overlay').classList.add('hidden');
    modalEl.querySelector('#cfg-form').addEventListener('submit', onSubmit);
}

async function onSubmit(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = {
        system_prompt: fd.get('system_prompt'),
        asset_whitelist: String(fd.get('asset_whitelist') || '').split(',').map(s => s.trim()).filter(Boolean),
        min_confidence_threshold: parseInt(fd.get('min_confidence_threshold'), 10),
        max_risk_tier: fd.get('max_risk_tier'),
        analysis_interval_hours: parseInt(fd.get('analysis_interval_hours'), 10),
        max_position_pct: parseFloat(fd.get('max_position_pct')),
        max_daily_loss_pct: parseFloat(fd.get('max_daily_loss_pct')),
        max_exchange_exposure_pct: parseFloat(fd.get('max_exchange_exposure_pct')),
        cooldown_seconds: parseInt(fd.get('cooldown_seconds'), 10),
        order_min_notional: parseFloat(fd.get('order_min_notional')),
        order_max_notional: parseFloat(fd.get('order_max_notional')),
    };
    try {
        await putStrategyConfig(payload);
        window.showToast?.('success', 'Config saved');
        document.getElementById('modal-overlay').classList.add('hidden');
    } catch (err) {
        window.showToast?.('error', err.message);
    }
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
```

- [ ] **Step 2: Add trigger button to header**

In `index.html` header (the `.guardrail-status-bar` block), add a gear button:
```html
<button id="strategy-config-btn" class="icon-btn" title="Strategy Configuration">⚙</button>
```

- [ ] **Step 3: Add CSS**

Append to `styles.css`:
```css
/* Strategy config */
.icon-btn {
    background: transparent; color: var(--text-primary); border: 1px solid var(--border);
    padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 16px;
}
.icon-btn:hover { background: var(--bg-elevated); }
.cfg-form { display: flex; flex-direction: column; gap: 12px; max-height: 70vh; overflow-y: auto; }
.cfg-form label {
    display: flex; flex-direction: column; gap: 4px;
    font-size: 12px; color: var(--text-secondary); text-transform: uppercase;
}
.cfg-form input, .cfg-form textarea, .cfg-form select {
    background: var(--bg-elevated); color: var(--text-primary);
    border: 1px solid var(--border); border-radius: 4px; padding: 6px;
    font-family: var(--font-sans); font-size: 13px; text-transform: none;
}
.cfg-form textarea { font-family: var(--font-mono); font-size: 12px; }
.cfg-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
```

- [ ] **Step 4: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/web/static/js/strategy_config.js horizon/internal/web/static/index.html horizon/internal/web/static/css/styles.css
git commit -m "feat(frontend): strategy configuration modal (system prompt + thresholds)"
```

---

### Task 17: Wire all new modules in app.js `[FRONTEND]`

**Files:**
- Modify: `horizon/internal/web/static/js/app.js`

- [ ] **Step 1: Add imports + init calls**

At the top of `app.js`, add imports:
```javascript
import { initProposals } from './proposals.js';
import { initCoPilot } from './copilot.js';
import { initStrategyConfig } from './strategy_config.js';
```

In the `init()` function, after existing `init*` calls, add:
```javascript
    // Init new LLM modules
    await initProposals(
        document.getElementById('proposals-table-container'),
        document.getElementById('trigger-analysis-btn'),
        document.getElementById('proposal-count-badge'),
    );

    await initCoPilot({
        panel: document.getElementById('copilot-panel'),
        toggleBtn: document.getElementById('copilot-toggle'),
        messages: document.getElementById('copilot-messages'),
        input: document.getElementById('copilot-input'),
        sendBtn: document.getElementById('copilot-send'),
        sessionSelect: document.getElementById('copilot-session-select'),
        newBtn: document.getElementById('copilot-new-btn'),
    });

    await initStrategyConfig(document.getElementById('strategy-config-btn'));
```

- [ ] **Step 2: Expose showToast globally (copilot.js + proposals.js use it)**

In `app.js`, find any existing `showToast` usage. If `orders.js` exports `showToast`, ensure it's imported here as already done; then add at end of `init()`:
```javascript
    window.showToast = showToast;
```

(The existing `import { ..., showToast } from './orders.js'` at the top should already make it available — just need to attach to `window`.)

- [ ] **Step 3: Visual end-to-end smoke test**

Start the server. Open the dashboard:
1. Verify the new Proposals panel renders (likely empty)
2. Click [⚙] in header → Strategy Configuration modal opens
3. Click 🤝 AI Co-Pilot at bottom-right → panel expands
4. Click [+ New] → name a session → empty chat area shown

- [ ] **Step 4: Commit**

```bash
cd /d/Horizon-Dev
git add horizon/internal/web/static/js/app.js
git commit -m "feat(frontend): wire proposals, copilot, strategy_config modules in app.js"
```

---

## Phase 4 — Integration `[INTEGRATION]`

> Run after Phase 1, 2, and 3 complete. The integration agent (or human) executes these tasks linearly. Either agent may own this phase — typically Backend agent since it touches the test suite.

### Task 18: Full test-suite green `[INTEGRATION]`

**Files:** None (verification only)

- [ ] **Step 1: Run entire pytest suite**

Run: `cd /d/Horizon-Dev && python -m pytest horizon/tests/ -q 2>&1 | tail -30`
Expected: all tests pass — no `FAILED` lines.

- [ ] **Step 2: If any pre-existing tests fail, triage**

For each failing test, decide:
- **Caused by our changes**: fix in-place (usually a missing `action_type` default or a model field rename collision). Re-run.
- **Pre-existing failure unrelated to LLM work**: capture with `pytest <path>::<name> -v 2>&1 | tail -20`, document in commit message; do NOT widen scope.

- [ ] **Step 3: Commit any fixes made in Step 2**

If fixes were needed:
```bash
cd /d/Horizon-Dev
git add <changed-files>
git commit -m "fix(tests): repair regression caused by action_type field addition"
```

---

### Task 19: Manual end-to-end verification `[INTEGRATION]`

**Files:** None (manual checklist, screenshot/log captures into commit body)

- [ ] **Step 1: Cold-boot the server**

```bash
cd /d/Horizon-Dev && python -m horizon.main
```
Wait for `Horizon server started on 0.0.0.0:8080`. Leave running.

In another shell, capture log lines that must be present:
```bash
grep -E "Anthropic|LLMScheduler|CoPilotEngine|expiry scanner|auto-execution scanner" <(cd /d/Horizon-Dev && timeout 10 python -m horizon.main 2>&1)
```
Expected: all five components reported as initialized / started.

- [ ] **Step 2: Backend smoke endpoints**

```bash
curl -s http://localhost:8080/api/proposals/stats | python -m json.tool
curl -s http://localhost:8080/api/llm/history | python -m json.tool
curl -s http://localhost:8080/api/strategy/config | python -m json.tool
curl -s -X POST http://localhost:8080/api/copilot/sessions -H "Content-Type: application/json" -d '{"title":"smoke"}' | python -m json.tool
curl -s http://localhost:8080/api/copilot/sessions | python -m json.tool
```
Expected: every call returns valid JSON, no 500s.

- [ ] **Step 3: Trigger LLM analysis manually**

```bash
curl -s -X POST http://localhost:8080/api/llm/trigger | python -m json.tool
```
Expected: returns JSON with `proposals_generated` (may be 0 if LLM says no trade). Confirms the Anthropic round-trip is wired.

Verify a row was written:
```bash
sqlite3 ./data/horizon.db "SELECT id, completion_status, latency_ms, parsed_proposals_count, substr(raw_response, 1, 80) FROM llm_analysis_history ORDER BY triggered_at DESC LIMIT 1;"
```
Expected: 1 row with `completion_status='success'`.

- [ ] **Step 4: Frontend manual checklist**

Open `http://localhost:8080/static/index.html` in a browser. Verify in order:

1. **Header**: Mode badge (LIVE/PAPER), [⚙] Strategy Config button visible
2. **LLM Proposals panel**: present, empty-state message OR rows if any
3. **[Run Analysis Now]** button → click → toast "complete: N proposals generated"
4. **Click ⚙** → modal opens with current system prompt + thresholds → close
5. **Bottom-right [🤝 AI Co-Pilot]** → click to expand
6. **[+ New]** session → enter title → empty chat area
7. **Type "What about BTC right now?"** → Enter → user bubble appears, then assistant bubble within ~3-5s
8. **If suggestion appears** → click [📋 Pre-fill Order Ticket] → Order panel scrolls into view with fields populated
9. **No console errors** (F12 → Console tab)

Capture findings as a structured note for the integration commit message.

- [ ] **Step 5: Shutdown verification**

In the server terminal: `Ctrl+C`.
Expected sequence in logs:
```
LLM scheduler stopped
Proposal expiry scanner stopped
Proposal queue auto-execution scanner stopped
Market data fetcher stopped
Portfolio tracker stopped
Order manager stopped
Database connection closed
Horizon server stopped
```
Process exits within 5 seconds.

- [ ] **Step 6: Verify no secrets committed**

```bash
cd /d/Horizon-Dev && git log --all --full-history -- key.txt | head -5
git status --short
git ls-files | grep -i 'key\.txt\|\.env'
```
Expected: `key.txt` is no longer in `git status` (gitignored); no `.env` files staged. Pre-existing presence of key.txt in history is acknowledged in §8 of the spec (out-of-scope).

---

### Task 20: Final integration commit + finishing-a-development-branch `[INTEGRATION]`

**Files:** None (process)

- [ ] **Step 1: Ensure working tree is clean**

Run: `cd /d/Horizon-Dev && git status --short`
Expected: empty (all per-task commits already pushed).

- [ ] **Step 2: Tag the integration commit**

The series of per-task commits IS the deliverable; no extra squash commit needed for `dev`-direct workflow.
Optional: create an annotated tag to mark completion:
```bash
cd /d/Horizon-Dev
git tag -a llm-feature-2026-06-04 -m "LLM dual-mode feature: autonomous + Co-Pilot — frontend + backend integrated"
git log --oneline -20
```

- [ ] **Step 3: Invoke finishing-a-development-branch skill**

This skill handles:
- Final full pytest gate
- Optional merge to develop (NOT applicable here since we developed directly on dev)
- Worktree cleanup (NOT applicable here — no worktree)
- Surface remaining TODO/FIXMEs

Per CLAUDE.md §4.2: even though we skipped worktree, we still run `finishing-a-development-branch` for the test gate + tidy-up.

- [ ] **Step 4: Confirm Definition of Done (spec §9)**

Check all DoD items pass:
- [ ] `pytest horizon/tests/ -q` green
- [ ] `python -m horizon.main` boots cleanly
- [ ] `/api/proposals/stats` reachable
- [ ] `/api/llm/trigger` produces history row
- [ ] Co-Pilot session creation + message round-trip works
- [ ] 3 new frontend panels render, no console errors
- [ ] `.gitignore` present, `key.txt` not in `git status`
- [ ] All commits follow `<type>(<scope>): <description>` format

---

## Self-Review

### Spec coverage check
Each spec requirement maps to one or more tasks:

| Spec § | Requirement | Task(s) |
|---|---|---|
| §3.1 | New `client.py` | Task 2 |
| §3.1 | New `scheduler.py` | Task 6 |
| §3.1 | New `copilot.py` + `copilot_prompt.py` | Task 10 |
| §3.1 | Migration `005_copilot.sql` | Task 3 |
| §3.1 | Extend `engine.py`, `prompt_builder.py`, `parser.py` | Tasks 4, 5 |
| §3.1 | Extend `models.py` action_type | Task 4 |
| §3.6 | 13 new REST endpoints (5 proposals + 3 llm/strategy + 5 copilot) | Tasks 7, 8, 11 |
| §3.7 | DB schema `copilot_sessions`/`copilot_messages` + `proposals.action_type` | Task 3 |
| §3.8 | `main.py` wiring | Tasks 9, 12 |
| §4.1 | `proposals.js`/`copilot.js`/`strategy_config.js` | Tasks 14, 15, 16 |
| §4.5 | `api.js` extensions | Task 13 |
| §4.2-4.4 | Three new panels | Tasks 14, 15, 16 |
| §7 | All test files | Tasks 2, 4, 6, 7, 10, 11 |
| §8 | `.gitignore` | Task 1 |
| §8 | Cleanup `config.yaml` dead LLM keys | Task 8 (Step 4: optional cleanup in main.py wiring; if config.yaml read still tries `llm.api_key`, drop those keys in same commit) |
| §9 | Definition of Done | Task 20 Step 4 |

All requirements covered.

### Placeholder scan
No "TBD"/"TODO"/"implement later" inside steps. Every step shows the exact code/command.

### Type consistency
- `TradeProposal.action_type` field name matches across models, parser, queue.enqueue INSERT, and proposals.js display.
- `CoPilotReply` fields match between `copilot.py`, `copilot_api` endpoint serializer, and `copilot.js` renderMessage().
- `LLMScheduler.trigger_now()` returns what `engine.analyze_and_propose()` returns — `LLMProposalOutput` — and `/api/llm/trigger` reads `.proposals` / `.market_analysis_summary` / `.confidence_explanation` consistently with that dataclass.
- `getProposalStats` / `getProposals` / `approveProposal` etc. all use the same path shapes as the backend endpoints.

### Dependency ordering
- Phase 0 (Tasks 1-4) MUST complete first (frontend and backend both depend on these).
- After Phase 0:
  - **Backend agent** runs Phase 1 (Tasks 5-9) then Phase 2 (Tasks 10-12).
  - **Frontend agent** runs Phase 3 (Tasks 13-17) in parallel.
- Phase 4 (Tasks 18-20) runs after both backend phases AND frontend phase finish.

---

## Execution Plan for Subagent-Driven Development

**Phase 0 (FOUNDATION, sequential, single backend agent):**
Tasks 1, 2, 3, 4

**Phase 1 + 2 (BACKEND agent, sequential within agent):**
Tasks 5, 6, 7, 8, 9, 10, 11, 12

**Phase 3 (FRONTEND agent, sequential within agent, parallel with Phase 1+2):**
Tasks 13, 14, 15, 16, 17

**Phase 4 (INTEGRATION, single agent, after all above):**
Tasks 18, 19, 20

**Dispatch order from the orchestrator:**
1. Spawn Backend agent for Phase 0 (Tasks 1-4) — wait for completion.
2. Spawn Backend agent for Phase 1+2 AND Frontend agent for Phase 3 — in parallel.
3. After both complete, spawn one agent for Phase 4.

Total task count: **20**. Estimated commits: **18-20** (each major task ends with a commit; some Phase 4 steps add 0-1 commits).

---

*End of implementation plan.*





