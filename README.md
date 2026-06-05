# Horizon Trading Platform

> **AI-augmented quantitative cryptocurrency trading.**
> Autonomous LLM-driven market analysis **+** human-initiated multi-turn trading dialogue ("Co-Pilot").
> Multi-exchange · paper & live · multi-layered risk guardrails · self-hosted.

![status](https://img.shields.io/badge/status-alpha-yellow) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![license](https://img.shields.io/badge/license-MIT-green)

---

## ✨ What It Does

| Mode | Initiator | What happens |
|---|---|---|
| **🤖 Autonomous** | System (manual or scheduled) | LLM scans market + portfolio → generates **open / close / reduce** proposals → guardrails check → auto-executes high-confidence / low-risk trades (or queues the rest for human review). |
| **🤝 Co-Pilot (人机协作)** | You | Open the floating chat panel, type an intuition ("BTC feels hot"), get a structured response with **cited indicators (RSI / MACD / EMA / ATR)** and a concrete trade suggestion. The suggestion **pre-fills** the order ticket — you always click Submit yourself. |

The two modes share one LLM pipeline, one proposal queue, one guardrail stack.

---

## 🎯 Features

- **Four exchanges** wired: `Binance` · `HTX` · `Hyperliquid` · `Bitget`
- **K-line charts** with timeframe switching (TradingView `lightweight-charts`)
- **Order ticket** with side-toggle, market/limit types, reduce-only / post-only flags
- **Position cards** with live P&L (unrealized + realized)
- **Paper trading simulator** with weighted-average entry pricing
- **6 risk guardrails** (asset whitelist, cooldown, exchange exposure, position size, order notional, daily loss limit) — block or auto-downgrade to COLLABORATIVE mode
- **Trade history analytics**: P&L chart, win rate, holding time, per-source stats
- **LLM analysis history** with token usage, latency, success/error status
- **Strategy configuration UI** (system prompt, whitelist, thresholds)
- **Auto-Execution toggle** + **PAPER/LIVE mode switch** in the header

---

## 🏛️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  FRONTEND (Vanilla JS)                   │
│  ┌──────────┐  ┌──────────┐  ┌─────────────┐            │
│  │  Charts  │  │ Positions│  │  LLM Co-    │            │
│  │  Orders  │  │  P&L     │  │  Pilot      │            │
│  │  Trades  │  │ Balances │  │  Proposals  │            │
│  └──────────┘  └──────────┘  └─────────────┘            │
│            SSE  +  HTTP/JSON (FastAPI)                  │
└────────────────────────────┬────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────┐
│                BACKEND (Python · FastAPI)                │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ LLM      │  │ Co-Pilot │  │ Proposal │  │ Guardrail│  │
│  │ Engine   │  │ Engine   │  │ Queue    │  │ Evaluator│  │
│  │ (8h cron │  │ (chat)   │  │ (state   │  │ (6 rules │  │
│  │ + manual)│  │          │  │ machine) │  │ + down-  │  │
│  │          │  │          │  │          │  │ grade)   │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Order    │  │ Paper    │  │ Portfolio│  │ Exchange │  │
│  │ Manager  │  │ Trader   │  │ Tracker  │  │ Registry │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│                                                          │
│            Anthropic SDK  ·  aiosqlite  ·  asyncio       │
└──────────────────────────────────────────────────────────┘
```

**Two LLM engines share one `AnthropicClient` and one proposal queue:**

- `LLMStrategyEngine` (autonomous): gathers market context → builds prompt → calls Claude → parses response → enqueues proposals
- `CoPilotEngine` (collaborative): maintains multi-turn session history → loads live context each turn → calls Claude → returns reply + optional `trade_suggestion`

See `docs/superpowers/specs/2026-06-04-llm-feature-design.md` for the full design.

---

## 🚀 Quickstart

### 1. Prerequisites
- Python 3.11+
- Conda (or venv)
- An Anthropic-API-compatible LLM endpoint with a valid key (defaults to the Anthropic SDK shape; tested with Anthropic, ZhipuAI `glm-4.x`, and MiniMax `MiniMax-M3`)

### 2. Install
```bash
git clone https://github.com/2533757653/Horizon-Dev.git
cd Horizon-Dev
conda activate base        # or your preferred env
pip install -e .
```

### 3. Configure secrets
```bash
# (a) Edit horizon/config.example.yaml -> rename to config.yaml
cp horizon/config.example.yaml horizon/config.yaml
# fill in your exchange API keys (or leave blank for paper-only)

# (b) Create key.txt at the repo root with your LLM credentials
cat > key.txt <<'EOF'
{
  "ANTHROPIC_AUTH_TOKEN": "your-token-here",
  "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
  "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-20250514"
}
EOF
```
`key.txt` is gitignored and never committed. Environment variables `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` override `key.txt` if set.

### 4. Run
```bash
python -m horizon.main
```
Open `http://localhost:8080/` in your browser.

### 5. First trades
1. Confirm the **mode badge** reads `PAPER` (default) before going anywhere near real money.
2. Try the **Co-Pilot** panel (bottom-right) — type "BTC 现在能买吗?" and see cited RSI/MACD analysis.
3. When the model suggests a trade, click **📋 Pre-fill Order Ticket** to populate the form.
4. Click **[Run Analysis Now]** in the LLM Proposals panel to trigger autonomous analysis on demand.
5. Switch to **LIVE** only after extensive paper validation.

---

## 🛡️ Risk Guardrails

Every order — autonomous or manual — passes through these 6 rules before exchange submission:

| Rule | Action | What it does |
|---|---|---|
| `asset_whitelist` | BLOCK_AND_DOWNGRADE | Reject symbols not in the strategy whitelist |
| `cooldown` | BLOCK_AND_DOWNGRADE | Reject orders for `(exchange, symbol)` within `cooldown_seconds` of last trade |
| `exchange_exposure` | BLOCK_AND_DOWNGRADE | Total USDT on a single exchange ≤ `max_exchange_exposure_pct` |
| `position_size` | BLOCK_AND_DOWNGRADE | Position value ≤ `max_position_pct` of portfolio |
| `order_notional` | BLOCK | Notional between `order_min_notional` and `order_max_notional` |
| `daily_loss_limit` | BLOCK_AND_DOWNGRADE | (autonomous only) Today's P&L ≥ −`max_daily_loss_pct` of portfolio |

When any `BLOCK_AND_DOWNGRADE` rule fires, the system enters **COLLABORATIVE** mode for 30 minutes — no autonomous execution can occur; all proposals require manual approval.

---

## ⚙️ Configuration

| Knob | Where | Default | Effect |
|---|---|---|---|
| `min_confidence_threshold` | DB / Strategy Config UI | 75 | LLM confidence below this stays as `PROPOSED` |
| `max_risk_tier` | DB / UI | `low` | `medium` / `high` risk proposals won't auto-execute |
| `analysis_interval_hours` | DB / UI | 8 | Autonomous LLM scan cadence (manual trigger also available) |
| `asset_whitelist` | DB / UI | `[BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT]` | Symbols the LLM may suggest |
| `paper_initial_cash_usdt` | `config.yaml` | 20000 | Starting paper-trading balance |
| `cooldown_seconds` | DB / UI | 300 | Per-symbol post-trade cooldown |
| `autonomy_enabled` | header toggle | OFF | When OFF, every LLM proposal needs manual approval |

Edit any of these from the **Strategy Configuration** modal (⚙ in the header) or `PUT /api/strategy/config`.

---

## 🧪 Tests

```bash
pytest horizon/tests/ -q
```

313 tests cover the LLM engine, Co-Pilot engine, proposal queue, guardrails, paper simulator, REST endpoints, and the dataclass/dict migration in queue config access.

---

## 📁 Project Layout

```
horizon/
├── internal/
│   ├── llm/                 # engine.py · copilot.py · client.py · scheduler.py · prompt_builder.py · parser.py
│   ├── proposals/           # queue.py · models.py — state machine for trade proposals
│   ├── guardrails/          # rules.py · evaluator.py · cooldown_tracker.py — 6 risk rules
│   ├── paper/               # simulator.py · stats.py — paper trading engine
│   ├── exchange/            # adapter base + 4 exchange implementations
│   ├── marketdata/          # fetcher.py — SSE-streamed market data
│   ├── portfolio/           # tracker.py — balances + equity curve
│   ├── ordermanager/        # manager.py — order lifecycle + guardrail integration
│   ├── indicators/          # calculator.py — RSI · MACD · EMA · Bollinger · ATR
│   ├── web/                 # server.py (FastAPI) + static/ (Vanilla JS dashboard)
│   ├── database/            # db.py + migrations/ (SQLite schema versions 001-005)
│   └── config/              # settings.py — env-var + YAML loader
├── tests/                   # 313 pytest tests
└── main.py                  # lifespan, registry, wiring
```

---

## 🛑 Important Disclaimers

- **Real-money trading is dangerous.** The LLM is statistical, not a predictor. You can lose more than your deposit on leveraged markets.
- **Paper trade extensively first.** Switch to LIVE only after observing weeks of stable behavior.
- **You are responsible for your keys.** `key.txt` and `config.yaml` are gitignored locally; do not paste them into issues, chat logs, or screenshots.
- **The codebase was seeded with real API keys for development** and those keys have been removed from the tracked `config.yaml` before public release. If you cloned an earlier revision that still contains them, **rotate the keys immediately** and run `git filter-repo` to scrub history.

---

## 📜 License

MIT — see `LICENSE`.

## 🤝 Contributing

This is currently a personal quantitative-trading research project. Issues and PRs are welcome for bugs, not for "make the LLM more bullish" suggestions.

---

## 📚 Documentation

- **Design spec**: `docs/superpowers/specs/2026-06-04-llm-feature-design.md`
- **Implementation plan**: `docs/superpowers/plans/2026-06-04-llm-feature-impl-plan.md`
- **Workflow rules**: `CLAUDE.md` (in Chinese — autonomous-dev protocol)
- **Chinese version**: `docs/CLAUDE_CHINESE.md`
