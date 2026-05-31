# Step 1: Python Core Foundation + Basic Dashboard

> **For agentic workers:** This is a foundational step. ALL subsequent steps depend on this step being fully implemented, compiled, and passing all acceptance criteria. Do NOT proceed to Step 2 until Step 1 acceptance criteria are verified.

**Goal:** Establish a complete Python runtime foundation for the Horizon Trading Platform. This step delivers multi-exchange connectivity via async aiohttp adapters, SQLite persistence with aiosqlite, order management with event logging, portfolio tracking with USDT valuation, market data fetching with SSE broadcasting, a FastAPI web server with REST endpoints, and an embedded HTML/JS dashboard. The server must start successfully and all acceptance criteria must pass before this step is considered complete.

**Architecture:** Single-process FastAPI application with `asyncio` background tasks. Layered package structure: exchange adapters → market data fetcher → order manager → portfolio tracker → web server. All components communicate through the interface contracts defined in the system architecture specification.

**Tech Stack:** Python 3.12+, FastAPI, uvicorn, aiosqlite, aiohttp, Pydantic, Pydantic-Settings, PyYAML, python-dotenv.

---

## File Structure

```
horizon/
├── pyproject.toml
├── .env.example
├── config.yaml
├── main.py
├── internal/
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── db.py
│   │   └── migrations/
│   │       └── 001_initial.sql
│   ├── exchange/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── adapter.py
│   │   ├── registry.py
│   │   ├── binance.py
│   │   ├── htx.py
│   │   ├── hyperliquid.py
│   │   └── bitget.py
│   ├── marketdata/
│   │   ├── __init__.py
│   │   └── fetcher.py
│   ├── ordermanager/
│   │   ├── __init__.py
│   │   └── manager.py
│   ├── portfolio/
│   │   ├── __init__.py
│   │   └── tracker.py
│   └── web/
│       ├── __init__.py
│       ├── server.py
│       └── static/
│           └── index.html
└── tests/
    └── __init__.py
```

---

### Task 1: Project Scaffold

**Files:**
- Create: `horizon/pyproject.toml`
- Create: `horizon/.env.example`
- Create: `horizon/config.yaml`
- Create: `horizon/main.py`
- Create: `horizon/internal/__init__.py`
- Create: `horizon/tests/__init__.py`

**Dependencies:** None (first task).

**Specification:**

#### `pyproject.toml`
Must declare a Python package named `horizon` using PEP 621 metadata. Must include the following runtime dependencies with minimum version constraints: `fastapi>=0.115.0`, `uvicorn[standard]>=0.32.0`, `aiosqlite>=0.20.0`, `aiohttp>=3.11.0`, `pydantic>=2.9.0`, `pydantic-settings>=2.6.0`, `pyyaml>=6.0.2`, `python-dotenv>=1.0.1`, `apscheduler>=3.11.0`, `anthropic>=0.40.0`. Must declare `httpx>=0.27.0` as a dev dependency for testing. The package must expose a console script `horizon-server = main:main` so that `horizon-server` launches the application.

#### `config.yaml`
Must contain the following top-level sections and keys:
- `app`: with `host` (default `"0.0.0.0"`), `port` (default `8080`), `log_level` (default `"info"`).
- `exchanges`: with subsections `binance`, `htx`, `hyperliquid`, `bitget`. Each subsection must have `enabled` (bool, default `true`), `recv_window_ms` (int, default `5000`), `api_key` (string, default `""`), `api_secret` (string, default `""`). The `hyperliquid` subsection must additionally have `wallet_address` (string, default `""`) and `private_key` (string, default `""`). The `bitget` subsection must additionally have `passphrase` (string, default `""`).
- `trading`: with `market_data_poll_interval_seconds` (int, default `10`), `portfolio_snapshot_interval_seconds` (int, default `60`), `order_sync_interval_seconds` (int, default `30`).
- `database`: with `path` (string, default `"./data/horizon.db"`).
- `llm`: with `enabled` (bool, default `false`), `api_key` (string, default `""`), `model` (string, default `"claude-3-5-sonnet-20241022"`), `analysis_interval_hours` (int, default `8`).
- `market_data`: with `symbols` (list of strings, default `["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]`).

#### `.env.example`
Must list all environment variable overrides supported by Pydantic Settings: `HORIZON_APP_HOST`, `HORIZON_APP_PORT`, `HORIZON_APP_LOG_LEVEL`, `HORIZON_DATABASE_PATH`, `HORIZON_LLM_API_KEY`, `HORIZON_LLM_MODEL`, `HORIZON_LLM_ENABLED`, `HORIZON_BINANCE_API_KEY`, `HORIZON_BINANCE_API_SECRET`, `HORIZON_HTX_API_KEY`, `HORIZON_HTX_API_SECRET`, `HORIZON_HYPERLIQUID_WALLET_ADDRESS`, `HORIZON_HYPERLIQUID_PRIVATE_KEY`, `HORIZON_BITGET_API_KEY`, `HORIZON_BITGET_API_SECRET`, `HORIZON_BITGET_PASSPHRASE`.

#### `main.py`
Must be the application entry point. Must define an async `lifespan` context manager for FastAPI that: (1) on startup, loads configuration, initializes the SQLite database and runs migrations, registers all enabled exchange adapters, initializes the market data fetcher and starts its polling loop, initializes the portfolio tracker and starts its snapshot loop, initializes the order manager and starts its sync loop; (2) on shutdown, gracefully cancels all background tasks, saves a final portfolio snapshot, and closes the database connection. Must define a `main()` function that runs `uvicorn.run()` with the lifespan-enabled FastAPI app.

**Acceptance Criteria for this Task:**
1. Running `pip install -e horizon/` installs all dependencies without errors.
2. `python -c "import horizon; print('ok')"` succeeds.
3. `config.yaml` parses correctly with PyYAML.
4. `main.py` can be imported without runtime errors.

---

### Task 2: Database Layer

**Files:**
- Create: `horizon/internal/database/__init__.py`
- Create: `horizon/internal/database/db.py`
- Create: `horizon/internal/database/migrations/001_initial.sql`

**Dependencies:** Task 1.

**Specification:**

#### `internal/database/db.py`
Must define an async context manager `get_db()` that yields an `aiosqlite.Connection`. The connection must be configured with `row_factory = aiosqlite.Row`. Must define an async function `init_db(db_path: str) -> aiosqlite.Connection` that: ensures the parent directory of `db_path` exists; opens the connection; executes all migration files in `internal/database/migrations/` in lexicographic order; returns the connection. Must define an async function `run_migrations(conn: aiosqlite.Connection, migrations_dir: str)` that reads each `.sql` file in the directory, splits on `;` to handle multiple statements, and executes each statement via `conn.executescript()`. Must define an async function `close_db(conn: aiosqlite.Connection)` that closes the connection.

#### `internal/database/migrations/001_initial.sql`
Must create the following tables with exact schema. All tables must use `IF NOT EXISTS`. All timestamp columns must default to `CURRENT_TIMESTAMP`.

**Table `exchanges`:** `name TEXT PRIMARY KEY`, `enabled INTEGER NOT NULL DEFAULT 1`, `api_key_encrypted TEXT`, `api_secret_encrypted TEXT`, `extra_params TEXT`, `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`.

**Table `orders`:** `id TEXT PRIMARY KEY`, `exchange TEXT NOT NULL`, `symbol TEXT NOT NULL`, `side TEXT NOT NULL CHECK(side IN ('buy','sell'))`, `order_type TEXT NOT NULL CHECK(order_type IN ('market','limit'))`, `price REAL`, `volume REAL NOT NULL`, `filled_volume REAL DEFAULT 0.0`, `status TEXT NOT NULL CHECK(status IN ('pending','submitted','partially_filled','filled','cancelled','rejected','expired'))`, `source TEXT NOT NULL CHECK(source IN ('manual','llm_proposal','autonomous'))`, `proposal_id TEXT`, `exchange_order_id TEXT`, `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`, `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`. Foreign key on `exchange` referencing `exchanges(name)`.

**Table `order_events`:** `id INTEGER PRIMARY KEY AUTOINCREMENT`, `order_id TEXT NOT NULL`, `event_type TEXT NOT NULL CHECK(event_type IN ('created','submitted','filled','partial_fill','cancelled','rejected','guardrail_blocked'))`, `event_data TEXT`, `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`. Foreign key on `order_id` referencing `orders(id)`.

**Table `portfolio_snapshots`:** `id INTEGER PRIMARY KEY AUTOINCREMENT`, `exchange TEXT NOT NULL`, `asset TEXT NOT NULL`, `free_balance REAL NOT NULL`, `locked_balance REAL NOT NULL`, `usdt_value REAL`, `snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP`.

**Table `market_data_cache`:** `id INTEGER PRIMARY KEY AUTOINCREMENT`, `exchange TEXT NOT NULL`, `symbol TEXT NOT NULL`, `last_price REAL NOT NULL`, `bid_price REAL`, `ask_price REAL`, `volume_24h REAL`, `orderbook_bids TEXT`, `orderbook_asks TEXT`, `recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`, `UNIQUE(exchange, symbol)`.

**Acceptance Criteria for this Task:**
1. `python -c "import asyncio; from internal.database.db import init_db; db = asyncio.run(init_db(':memory:')); asyncio.run(db.execute('SELECT name FROM sqlite_master WHERE type=\"table\"')).fetchall()` returns all 5 table names.
2. The `orders` table `CHECK` constraints reject invalid `side` values (`'long'` should raise `IntegrityError`).
3. The `market_data_cache` unique constraint on `(exchange, symbol)` is enforced.

---

### Task 3: Configuration System

**Files:**
- Create: `horizon/internal/config/__init__.py`
- Create: `horizon/internal/config/settings.py`

**Dependencies:** Task 1.

**Specification:**

#### `internal/config/settings.py`
Must define a `Settings` class using `pydantic_settings.BaseSettings`. Must use `SettingsConfigDict` with `yaml_file="config.yaml"`, `env_prefix="HORIZON_"`, and `env_file=".env"`. Must load nested configuration via a custom `yaml_settings_source` or by using `YamlConfigSettingsSource` from pydantic-settings.

The `Settings` class must have the following nested Pydantic models and fields:

- `app: AppSettings` where `AppSettings` has `host: str = "0.0.0.0"`, `port: int = 8080`, `log_level: str = "info"`.
- `exchanges: ExchangesSettings` where `ExchangesSettings` has four fields: `binance: ExchangeConfig`, `htx: ExchangeConfig`, `hyperliquid: ExchangeConfig`, `bitget: ExchangeConfig`. Each `ExchangeConfig` has `enabled: bool = True`, `recv_window_ms: int = 5000`, `api_key: str = ""`, `api_secret: str = ""`. `HyperliquidConfig` (subclass of `ExchangeConfig`) adds `wallet_address: str = ""` and `private_key: str = ""`. `BitgetConfig` (subclass of `ExchangeConfig`) adds `passphrase: str = ""`.
- `trading: TradingSettings` with `market_data_poll_interval_seconds: int = 10`, `portfolio_snapshot_interval_seconds: int = 60`, `order_sync_interval_seconds: int = 30`.
- `database: DatabaseSettings` with `path: str = "./data/horizon.db"`.
- `llm: LLMSettings` with `enabled: bool = False`, `api_key: str = ""`, `model: str = "claude-3-5-sonnet-20241022"`, `analysis_interval_hours: int = 8`.
- `market_data: MarketDataSettings` with `symbols: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]`.

Must also define a `load_keys_from_file(path: str) -> dict` function that reads `key.txt` (expected to be a JSON file mapping exchange names to credential dicts) and returns a nested dictionary. If the file does not exist or is invalid JSON, the function must return an empty dict and log a warning.

The module must expose a singleton `settings: Settings` instance created at import time.

**Acceptance Criteria for this Task:**
1. `from internal.config.settings import settings; print(settings.app.port)` returns `8080`.
2. Setting `HORIZON_APP_PORT=9000` in environment overrides the YAML value.
3. `load_keys_from_file` returns empty dict for non-existent files without raising.
4. All nested models validate correctly with Pydantic.

---

### Task 4: Exchange Types + Adapter ABC + Registry

**Files:**
- Create: `horizon/internal/exchange/__init__.py`
- Create: `horizon/internal/exchange/types.py`
- Create: `horizon/internal/exchange/adapter.py`
- Create: `horizon/internal/exchange/registry.py`

**Dependencies:** Task 1.

**Specification:**

#### `internal/exchange/types.py`
Must define the following `@dataclass(frozen=True)` types using `decimal.Decimal` for all monetary values:

- `Ticker`: fields `symbol: str`, `price: Decimal`, `volume_24h: Decimal`, `exchange: str`, `timestamp_ms: int`.
- `OrderBookEntry`: fields `price: Decimal`, `size: Decimal`.
- `OrderBook`: fields `symbol: str`, `exchange: str`, `bids: list[OrderBookEntry]`, `asks: list[OrderBookEntry]`.
- `Balance`: fields `asset: str`, `free: Decimal`, `locked: Decimal`, `exchange: str`.
- `OrderResult`: fields `order_id: str`, `exchange_order_id: str`, `exchange: str`, `symbol: str`, `side: str`, `order_type: str`, `price: Decimal | None`, `volume: Decimal`, `filled_volume: Decimal`, `status: str`, `created_at_ms: int`, `updated_at_ms: int`.

All `Decimal` fields must be created with `Decimal(str(value))` when parsing from string to avoid float precision issues.

#### `internal/exchange/adapter.py`
Must define an abstract base class `ExchangeAdapter` using `abc.ABC`. Must define the following abstract methods with exact signatures:

- `@property @abstractmethod def name(self) -> str`
- `@property @abstractmethod def enabled(self) -> bool`
- `async def fetch_ticker(self, symbol: str) -> Ticker`
- `async def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook`
- `async def fetch_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]`
- `async def fetch_balance(self, asset: str) -> Balance`
- `async def fetch_all_balances(self) -> list[Balance]`
- `async def place_market_order(self, symbol: str, side: str, volume: Decimal) -> OrderResult`
- `async def place_limit_order(self, symbol: str, side: str, price: Decimal, volume: Decimal) -> OrderResult`
- `async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool`
- `async def fetch_open_orders(self, symbol: str | None = None) -> list[OrderResult]`
- `async def fetch_order(self, exchange_order_id: str, symbol: str) -> OrderResult`

#### `internal/exchange/registry.py`
Must define a class `ExchangeRegistry` with:
- `__init__(self) -> None`: initializes an internal dict `_adapters: dict[str, ExchangeAdapter]`.
- `register(self, adapter: ExchangeAdapter) -> None`: stores adapter under `adapter.name`.
- `get(self, name: str) -> ExchangeAdapter | None`: returns adapter or None.
- `list_all(self) -> list[ExchangeAdapter]`: returns all registered adapters.
- `list_enabled(self) -> list[ExchangeAdapter]`: returns adapters where `enabled` is True.
- `async def get_all_tickers(self, symbol: str) -> list[Ticker]`: concurrently fetches tickers from all enabled adapters using `asyncio.gather` with `return_exceptions=True`; filters out exceptions; returns successful results.

**Acceptance Criteria for this Task:**
1. A mock adapter subclassing `ExchangeAdapter` can be instantiated and registered.
2. `ExchangeRegistry.get_all_tickers` handles adapter failures gracefully (one failing adapter does not crash the others).
3. All abstract methods are enforced (instantiating `ExchangeAdapter` directly raises `TypeError`).

---

### Task 5: Binance Exchange Adapter

**Files:**
- Create: `horizon/internal/exchange/binance.py`

**Dependencies:** Task 4.

**Specification:**

#### `internal/exchange/binance.py`
Must define a class `BinanceAdapter(ExchangeAdapter)`.

Constructor signature: `__init__(self, api_key: str, api_secret: str, recv_window_ms: int = 5000)`.

Properties: `name` returns `"binance"`; `enabled` returns `api_key != "" and api_secret != ""`.

Must use `aiohttp.ClientSession` with a 10-second timeout. The base URL must be `"https://api.binance.com"`.

**Authentication:** All authenticated endpoints must include:
1. Header `X-MBX-APIKEY` with the API key.
2. Query parameters `timestamp` (current UTC milliseconds) and `recvWindow`.
3. HMAC-SHA256 signature of the query string using the API secret, appended as `signature` parameter.

**`fetch_ticker(symbol)`:** GET `/api/v3/ticker/24hr?symbol={symbol}`. Parse `lastPrice` and `quoteVolume` from response. Return `Ticker` with `timestamp_ms = int(time.time() * 1000)`.

**`fetch_orderbook(symbol, depth=5)`:** GET `/api/v3/depth?symbol={symbol}&limit={depth}`. Parse `bids` and `asks` arrays. Each element is `[price_str, qty_str]`. Convert to `Decimal`. Return `OrderBook`.

**`fetch_balance(asset)`:** GET `/api/v3/account` (authenticated). Parse `balances` array. Find matching `asset`. Return `Balance` with parsed `free` and `locked` as `Decimal`. If asset not found, return `Balance(asset=asset, free=Decimal("0"), locked=Decimal("0"), exchange="binance")`.

**`fetch_all_balances()`:** Same endpoint as `fetch_balance` but return all non-zero balances.

**`place_market_order(symbol, side, volume)`:** POST `/api/v3/order` (authenticated, form-encoded body). Parameters: `symbol`, `side` (uppercase), `type=MARKET`, `quantity=str(volume)`. Parse response into `OrderResult`. `status` mapping: `NEW` → `"submitted"`, `PARTIALLY_FILLED` → `"partially_filled"`, `FILLED` → `"filled"`, `CANCELED` → `"cancelled"`, `REJECTED` → `"rejected"`.

**`place_limit_order(symbol, side, price, volume)`:** POST `/api/v3/order`. Parameters include `type=LIMIT`, `timeInForce=GTC`, `price=str(price)`, `quantity=str(volume)`.

**`cancel_order(exchange_order_id, symbol)`:** DELETE `/api/v3/order`. Parameters: `symbol`, `orderId=exchange_order_id`. Return `True` on HTTP 200.

**`fetch_open_orders(symbol=None)`:** GET `/api/v3/openOrders` (authenticated, with optional `symbol`). Return list of `OrderResult`.

**`fetch_order(exchange_order_id, symbol)`:** GET `/api/v3/order` (authenticated). Parameters: `symbol`, `orderId=exchange_order_id`. Return single `OrderResult`.

**Error handling:** On any `aiohttp.ClientError`, raise `ExchangeError` (custom exception) with the original error message. On HTTP 4xx/5xx, parse the Binance error response (`{ "code": int, "msg": str }`) and raise `ExchangeError(f"Binance {code}: {msg}")`.

**Acceptance Criteria for this Task:**
1. `BinanceAdapter` can be instantiated with test credentials.
2. `enabled` returns `False` when credentials are empty strings.
3. HMAC-SHA256 signature generation produces verifiable signatures (test against known values).
4. All methods have correct signatures matching `ExchangeAdapter` ABC.
5. Error responses raise `ExchangeError` with informative messages.

---

### Task 6: HTX Exchange Adapter

**Files:**
- Create: `horizon/internal/exchange/htx.py`

**Dependencies:** Task 4.

**Specification:**

#### `internal/exchange/htx.py`
Must define `HTXAdapter(ExchangeAdapter)`.

Constructor: `__init__(self, api_key: str, api_secret: str, recv_window_ms: int = 5000)`.

Properties: `name` → `"htx"`; `enabled` → `api_key != "" and api_secret != ""`.

Base URL: `"https://api.huobi.pro"`.

**Authentication (API Key authentication v2):**
1. For GET requests: create parameters including `AccessKeyId`, `SignatureMethod="HmacSHA256"`, `SignatureVersion="2"`, `Timestamp` (UTC in ISO 8601: `YYYY-MM-DDTHH:MM:SS`).
2. Build canonical request: `\n` joined: `METHOD`, `host`, `path`, `sorted-query-string`.
3. HMAC-SHA256 the canonical request with base64-encoded secret. Base64-encode the result as `Signature`.
4. Add `Signature` to query parameters.

**`fetch_ticker(symbol)`:** GET `/market/detail/merged?symbol={lower(symbol)}`. HTX uses lowercase symbols (e.g., `btcusdt`). Parse `tick.lastPrice` and `tick.vol`. Return `Ticker`.

**`fetch_orderbook(symbol, depth=5)`:** GET `/market/depth?symbol={lower(symbol)}&type=step0`. Parse `tick.bid` and `tick.ask` arrays.

**`fetch_balance(asset)`:** GET `/v1/account/accounts` (authenticated). Parse and find matching asset. Return `Balance`.

**`fetch_all_balances()`:** GET `/v1/account/accounts` (authenticated). Return all balances.

**`place_market_order(symbol, side, volume)`:** POST `/v1/order/orders/place` (authenticated). Body JSON: `{"account-id": "spot", "symbol": lower(symbol), "type": lower(side) + "-market", "amount": str(volume)}`. Note: HTX side format is `"buy-market"` or `"sell-market"`.

**`place_limit_order(symbol, side, price, volume)`:** Same endpoint. Body: `{"account-id": "spot", "symbol": lower(symbol), "type": lower(side) + "-limit", "amount": str(volume), "price": str(price)}`.

**`cancel_order(exchange_order_id, symbol)`:** POST `/v1/order/orders/{exchange_order_id}/submitcancel`.

**`fetch_open_orders(symbol=None)`:** GET `/v1/order/orders` (authenticated). Query params: `symbol=lower(symbol)`, `states="submitted,partial-filled"`.

**`fetch_order(exchange_order_id, symbol)`:** GET `/v1/order/orders/{exchange_order_id}` (authenticated).

**Error handling:** Same pattern as Binance — raise `ExchangeError` on failures. HTX errors have format `{"status": "error", "err-code": "...", "err-msg": "..."}`.

**Acceptance Criteria for this Task:**
1. `HTXAdapter` implements all `ExchangeAdapter` methods.
2. Symbol normalization converts to lowercase for HTX API calls.
3. Authentication signatures are correctly formatted per Huobi API v2.
4. Order side strings are correctly mapped to HTX format (`buy-market`, `sell-limit`, etc.).
5. Error handling raises `ExchangeError` with HTX `err-msg`.

---

### Task 7: Hyperliquid Exchange Adapter

**Files:**
- Create: `horizon/internal/exchange/hyperliquid.py`

**Dependencies:** Task 4.

**Specification:**

#### `internal/exchange/hyperliquid.py`
Must define `HyperliquidAdapter(ExchangeAdapter)`.

Constructor: `__init__(self, wallet_address: str, private_key: str)`.

Properties: `name` → `"hyperliquid"`; `enabled` → `wallet_address != "" and private_key != ""`.

Base URL: `"https://api.hyperliquid.xyz"`.

**Authentication:** Hyperliquid uses wallet-based authentication (Ethereum-style signing). For the scope of this step, implement a placeholder signing method that stores the wallet address and private key. Full EIP-712 signing will be required for live trading but can be stubbed with a warning log for this foundational step. The `place_market_order` and `place_limit_order` methods must log a warning: "Hyperliquid order signing not fully implemented — order not submitted" and return a synthetic `OrderResult` with status `"rejected"`.

**`fetch_ticker(symbol)`:** POST `/info` with body `{"type": "metaAndAssetCtxs"}`. Parse the response to find the asset context for the given symbol. Extract `markPrice` and `dayNtlVlm`. Return `Ticker`.

**`fetch_orderbook(symbol, depth=5)`:** POST `/info` with body `{"type": "l2Book", "coin": symbol}`. Parse `levels[0]` (bids) and `levels[1]` (asks). Each level is `[px, sz, n]` where `px` is price and `sz` is size. Convert to `Decimal`.

**`fetch_balance(asset)`:** POST `/info` with body `{"type": "spotClearinghouseState", "user": wallet_address}`. Parse `balances` array. Find matching asset. Return `Balance`.

**`fetch_all_balances()`:** Same as `fetch_balance` but return all balances in the response.

**`fetch_recent_trades(symbol, limit=50)`:** POST `/info` with body `{"type": "tradeHistory", "coin": symbol}`. Parse and return list of trade dicts.

**`fetch_open_orders(symbol=None)`:** POST `/info` with body `{"type": "openOrders", "user": wallet_address}`. Parse and return list of `OrderResult`.

**`fetch_order(exchange_order_id, symbol)`:** Return a placeholder that logs a warning. Full implementation deferred to future enhancement.

**`cancel_order(exchange_order_id, symbol)`:** Log warning about unimplemented signing. Return `False`.

**Error handling:** Raise `ExchangeError` on failures.

**Acceptance Criteria for this Task:**
1. `HyperliquidAdapter` implements all `ExchangeAdapter` methods.
2. All read-only methods (`fetch_ticker`, `fetch_orderbook`, `fetch_balance`, `fetch_all_balances`, `fetch_recent_trades`, `fetch_open_orders`) make correct API calls and parse responses.
3. Write methods (`place_market_order`, `place_limit_order`, `cancel_order`) log warnings and return safe fallback values (do NOT attempt unauthorized writes).
4. `enabled` returns `False` when wallet address or private key is empty.

---

### Task 8: Bitget Exchange Adapter

**Files:**
- Create: `horizon/internal/exchange/bitget.py`

**Dependencies:** Task 4.

**Specification:**

#### `internal/exchange/bitget.py`
Must define `BitgetAdapter(ExchangeAdapter)`.

Constructor: `__init__(self, api_key: str, api_secret: str, passphrase: str)`.

Properties: `name` → `"bitget"`; `enabled` → `api_key != "" and api_secret != "" and passphrase != ""`.

Base URL: `"https://api.bitget.com"`.

**Authentication:**
1. Header `ACCESS-KEY`: API key.
2. Header `ACCESS-PASSPHRASE`: passphrase.
3. Header `ACCESS-TIMESTAMP`: current UTC timestamp in milliseconds as string.
4. Header `ACCESS-SIGN`: Base64-encoded HMAC-SHA256 of `timestamp + method + requestPath + body` using the API secret. For GET requests, body is empty string `""`. `requestPath` includes query string.

**`fetch_ticker(symbol)`:** GET `/api/v2/spot/market/ticker?symbol={symbol}`. Parse `data.lastPr` as last price, `data.vol24h` as volume. Return `Ticker`.

**`fetch_orderbook(symbol, depth=5)`:** GET `/api/v2/spot/market/books?symbol={symbol}&limit={depth}`. Parse `data.bids` and `data.asks`.

**`fetch_balance(asset)`:** GET `/api/v2/spot/account/assets` (authenticated). Parse `data` array. Find matching `coinName`. Return `Balance` with `free` and `locked` parsed from `available` and `frozen`.

**`fetch_all_balances()`:** Same endpoint, return all non-zero balances.

**`place_market_order(symbol, side, volume)`:** POST `/api/v2/spot/trade/order` (authenticated). Body JSON: `{"symbol": symbol, "side": side, "orderType": "market", "size": str(volume)}`. Parse response into `OrderResult`.

**`place_limit_order(symbol, side, price, volume)`:** Same endpoint. Body: `{"symbol": symbol, "side": side, "orderType": "limit", "price": str(price), "size": str(volume)}`.

**`cancel_order(exchange_order_id, symbol)`:** POST `/api/v2/spot/trade/cancel-order` (authenticated). Body: `{"symbol": symbol, "orderId": exchange_order_id}`.

**`fetch_open_orders(symbol=None)`:** GET `/api/v2/spot/trade/unfilled-orders` (authenticated). Optional `symbol` query param.

**`fetch_order(exchange_order_id, symbol)`:** GET `/api/v2/spot/trade/orderInfo` (authenticated). Query params: `symbol`, `orderId=exchange_order_id`.

**Error handling:** Bitget errors have format `{"code": "...", "msg": "...", "requestTime": ...}`. Raise `ExchangeError(f"Bitget {code}: {msg}")`.

**Acceptance Criteria for this Task:**
1. `BitgetAdapter` implements all `ExchangeAdapter` methods.
2. Authentication headers are correctly generated per Bitget v2 API documentation.
3. All methods parse responses correctly.
4. Error handling raises `ExchangeError` with Bitget error codes and messages.

---

### Task 9: Market Data Fetcher

**Files:**
- Create: `horizon/internal/marketdata/__init__.py`
- Create: `horizon/internal/marketdata/fetcher.py`

**Dependencies:** Task 4, Task 5–8 (at least one adapter registered).

**Specification:**

#### `internal/marketdata/fetcher.py`
Must define a class `MarketDataFetcher`.

Constructor: `__init__(self, registry: ExchangeRegistry, symbols: list[str], poll_interval_seconds: int = 10)`.

Internal state:
- `_tickers: dict[str, dict[str, Ticker]]` — maps `symbol -> exchange_name -> Ticker`.
- `_orderbooks: dict[str, dict[str, OrderBook]]` — maps `symbol -> exchange_name -> OrderBook`.
- `_subscribers: list[asyncio.Queue[dict]]` — SSE subscriber queues.
- `_lock: asyncio.Lock` for thread-safe state access.

**`async def start(self) -> None`:** Starts the background polling loop as an `asyncio.Task`. Must be idempotent (calling start twice should not create duplicate tasks).

**`async def stop(self) -> None`:** Cancels the background task and waits for it to finish.

**`async def _poll_loop(self) -> None`:** Infinite loop that:
1. For each symbol in `_symbols`, calls `_fetch_symbol(symbol)`.
2. Sleeps for `poll_interval_seconds`.
3. Checks for cancellation via `asyncio.current_task().cancelled()` or an internal `_stop_event: asyncio.Event`.

**`async def _fetch_symbol(self, symbol: str) -> None`:**
1. Gets all enabled adapters from registry.
2. For each adapter, concurrently fetches ticker and orderbook (depth=5) using `asyncio.gather(..., return_exceptions=True)`.
3. On successful ticker fetch: updates `_tickers[symbol][adapter.name]`, creates an update dict `{"type": "ticker", "exchange": adapter.name, "symbol": symbol, "data": ticker_dict}`, and calls `_broadcast(update)`.
4. On successful orderbook fetch: updates `_orderbooks[symbol][adapter.name]`.
5. On failure: logs a warning with adapter name, symbol, and exception.

**`async def _broadcast(self, message: dict) -> None`:** Iterates `_subscribers`. For each queue, uses `queue.put_nowait(message)` if the queue size is under 100; otherwise drops the message (prevents unbounded memory growth).

**`def subscribe(self) -> asyncio.Queue[dict]`:** Creates a new `asyncio.Queue(maxsize=100)`, adds it to `_subscribers`, and returns it.

**`def unsubscribe(self, queue: asyncio.Queue) -> None`:** Removes the queue from `_subscribers`.

**`def get_ticker(self, symbol: str, exchange: str | None = None) -> Ticker | None`:** Returns the latest ticker. If `exchange` is None, returns the ticker from the first available exchange (arbitrary but deterministic order). Returns `None` if no ticker available.

**`def get_all_tickers(self, symbol: str) -> list[Ticker]`:** Returns all exchange tickers for the symbol.

**`async def persist_to_db(self, db: aiosqlite.Connection) -> None`:** Writes all cached tickers to the `market_data_cache` table using `INSERT OR REPLACE`. Should be called periodically (e.g., every 5 minutes) by a background task.

**Acceptance Criteria for this Task:**
1. `MarketDataFetcher.start()` creates exactly one background task.
2. Calling `start()` twice does not create duplicate tasks.
3. Subscribers receive ticker updates via their queues.
4. `get_ticker` returns the latest cached ticker.
5. Failed adapter calls do not crash the polling loop.
6. `stop()` cleanly terminates the polling loop.

---

### Task 10: Order Manager

**Files:**
- Create: `horizon/internal/ordermanager/__init__.py`
- Create: `horizon/internal/ordermanager/manager.py`

**Dependencies:** Task 2, Task 4, Task 9.

**Specification:**

#### `internal/ordermanager/manager.py`
Must define a class `OrderManager`.

Constructor: `__init__(self, registry: ExchangeRegistry, db: aiosqlite.Connection)`.

Internal state:
- `_registry: ExchangeRegistry`
- `_db: aiosqlite.Connection`
- `_open_orders: dict[str, OrderResult]` — maps internal `order_id` to `OrderResult`.
- `_lock: asyncio.Lock`
- `_sync_task: asyncio.Task | None`

Must define an enum or string constants for `OrderSource`: `"manual"`, `"llm_proposal"`, `"autonomous"`.

Must define a dataclass `OrderRequest`:
- `exchange: str`
- `symbol: str`
- `side: str` (`"buy"` or `"sell"`)
- `order_type: str` (`"market"` or `"limit"`)
- `price: Decimal | None`
- `volume: Decimal`
- `source: str` (one of the `OrderSource` values)
- `proposal_id: str | None = None`

**`async def submit_order(self, request: OrderRequest) -> OrderResult`:**
1. Validate that `request.exchange` exists in registry.
2. Validate that the adapter is enabled.
3. Generate a UUID v4 as `internal_order_id`.
4. Insert a row into the `orders` table with status `"pending"`, recording all request fields and the generated `internal_order_id`.
5. Record an `order_events` row with `event_type="created"`.
6. Get the adapter and call the appropriate placement method (`place_market_order` or `place_limit_order`).
7. On success: update the `orders` row with the exchange's `exchange_order_id` and status `"submitted"`. Record `event_type="submitted"`. Add to `_open_orders`.
8. On failure: update the `orders` row status to `"rejected"`. Record `event_type="rejected"` with the error message in `event_data`. Raise `OrderSubmissionError` with the original exception.
9. Return the `OrderResult`.

**`async def cancel_order(self, internal_order_id: str) -> bool`:**
1. Look up the order in `_open_orders`.
2. Get the adapter. Call `adapter.cancel_order(order.exchange_order_id, order.symbol)`.
3. On success: update `orders` status to `"cancelled"`. Record event. Remove from `_open_orders`.
4. Return `True` on success, `False` on failure.

**`async def get_open_orders(self, exchange: str | None = None, symbol: str | None = None) -> list[OrderResult]`:**
1. Return filtered list from `_open_orders`.
2. If `exchange` is provided, filter by `order.exchange == exchange`.
3. If `symbol` is provided, filter by `order.symbol == symbol`.

**`async def get_order_history(self, exchange: str | None = None, symbol: str | None = None, limit: int = 100) -> list[OrderResult]`:**
1. Query the `orders` table from SQLite.
2. Apply filters and `ORDER BY created_at DESC LIMIT ?`.
3. Return list of `OrderResult`.

**`async def start_sync_loop(self) -> None`:**
1. Start a background task `_sync_open_orders_loop()`.
2. The loop runs every `order_sync_interval_seconds` (from config, default 30).
3. For each enabled adapter, fetch open orders via `adapter.fetch_open_orders()`.
4. Update `_open_orders` to match exchange state: add new orders, update status changes, remove filled/cancelled orders.
5. Record `event_type` rows for status transitions.

**`async def record_event(self, order_id: str, event_type: str, event_data: dict | None = None) -> None`:**
1. Insert into `order_events` table.
2. `event_data` must be JSON-serialized if it is a dict.

**`async def stop(self) -> None`:**
1. Cancel the sync task if running.
2. Wait for cancellation.

**Acceptance Criteria for this Task:**
1. `submit_order` inserts into `orders` and `order_events` before exchange submission.
2. On exchange success, the order status is updated to `"submitted"`.
3. On exchange failure, the order status is updated to `"rejected"` and an exception is raised.
4. `get_open_orders` returns only orders with status in `("pending", "submitted", "partially_filled")`.
5. The sync loop updates order statuses from exchange state within 30 seconds.
6. `record_event` correctly serializes dict data to JSON.

---

### Task 11: Portfolio Tracker

**Files:**
- Create: `horizon/internal/portfolio/__init__.py`
- Create: `horizon/internal/portfolio/tracker.py`

**Dependencies:** Task 4, Task 9.

**Specification:**

#### `internal/portfolio/tracker.py`
Must define a class `PortfolioTracker`.

Constructor: `__init__(self, registry: ExchangeRegistry, db: aiosqlite.Connection, fetcher: MarketDataFetcher, snapshot_interval_seconds: int = 60)`.

Internal state:
- `_registry: ExchangeRegistry`
- `_db: aiosqlite.Connection`
- `_fetcher: MarketDataFetcher`
- `_balances: dict[str, dict[str, Balance]]` — maps `exchange -> asset -> Balance`.
- `_snapshot_interval: int`
- `_snapshot_task: asyncio.Task | None`
- `_lock: asyncio.Lock`

Must define a dataclass `PortfolioSnapshot`:
- `exchanges: dict[str, list[Balance]]`
- `total_usdt_value: Decimal`
- `timestamp_ms: int`

**`async def start(self) -> None`:**
1. Immediately call `_refresh_balances()`.
2. Start a background task `_snapshot_loop()`.

**`async def _snapshot_loop(self) -> None`:**
1. Sleep for `_snapshot_interval` seconds.
2. Call `_refresh_balances()`.
3. Call `save_snapshot()`.
4. Repeat until cancelled.

**`async def _refresh_balances(self) -> None`:**
1. For each enabled adapter, call `adapter.fetch_all_balances()` concurrently using `asyncio.gather(..., return_exceptions=True)`.
2. Update `_balances[adapter.name]` with the returned balances.
3. On failure, log a warning.

**`async def get_snapshot(self) -> PortfolioSnapshot`:**
1. Acquire `_lock`.
2. Copy `_balances` into the snapshot's `exchanges` dict.
3. For each balance, compute USDT value:
   - If asset is `"USDT"`, value = `free + locked`.
   - Otherwise, look up ticker via `_fetcher.get_ticker(asset + "USDT")`. If found, value = `(free + locked) * ticker.price`.
   - If no ticker, value = `Decimal("0")`.
4. Sum all USDT values into `total_usdt_value`.
5. Set `timestamp_ms = int(time.time() * 1000)`.
6. Return `PortfolioSnapshot`.

**`async def save_snapshot(self) -> None`:**
1. Get snapshot.
2. For each balance in each exchange, insert into `portfolio_snapshots` table.
3. Use a single transaction (BEGIN/COMMIT) for all inserts.

**`async def get_historical_snapshots(self, exchange: str | None = None, asset: str | None = None, limit: int = 100) -> list[dict]`:**
1. Query `portfolio_snapshots` with optional filters.
2. Return rows as list of dicts.

**`async def stop(self) -> None`:**
1. Cancel snapshot task.
2. Save one final snapshot.

**Acceptance Criteria for this Task:**
1. `get_snapshot` computes USDT values using market data tickers.
2. USDT balances are valued at face value (1 USDT = $1).
3. `save_snapshot` persists all balances in a single transaction.
4. Historical snapshots can be queried with filters.
5. The snapshot loop runs at the configured interval.

---

### Task 12: FastAPI Web Server

**Files:**
- Create: `horizon/internal/web/__init__.py`
- Create: `horizon/internal/web/server.py`

**Dependencies:** Task 2, Task 9, Task 10, Task 11.

**Specification:**

#### `internal/web/server.py`
Must define a function `create_app(settings: Settings, db: aiosqlite.Connection, registry: ExchangeRegistry, fetcher: MarketDataFetcher, order_manager: OrderManager, portfolio_tracker: PortfolioTracker) -> FastAPI`.

The FastAPI app must:
1. Have `title="Horizon Trading Platform"`, `version="1.0.0"`.
2. Include CORS middleware allowing all origins (for local development).
3. Mount static files at `/static` from `internal/web/static/`.

**Endpoint specifications:**

`GET /api/health` — Returns `{"status": "ok", "timestamp": int}`.

`GET /api/portfolio` — Calls `portfolio_tracker.get_snapshot()`. Returns the snapshot as JSON. Decimal values must be serialized as strings (use a custom JSON encoder or Pydantic model).

`GET /api/market-data/{symbol}` — Calls `fetcher.get_all_tickers(symbol)`. Returns `{"symbol": symbol, "tickers": [...]}`.

`GET /api/market-data/stream` — SSE endpoint. Must:
1. Create a queue via `fetcher.subscribe()`.
2. Set response headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`.
3. In a loop, await `queue.get()`. Format as SSE: `data: {json}\n\n`.
4. On client disconnect (catch `asyncio.CancelledError` or check `request.is_disconnected()`), call `fetcher.unsubscribe(queue)` and return.

`POST /api/orders` — Request body: JSON with `exchange`, `symbol`, `side`, `type` (`"market"` or `"limit"`), `price` (optional), `volume`. Must:
1. Validate required fields using a Pydantic model `OrderRequestModel`.
2. Create `OrderRequest` with `source="manual"`.
3. Call `order_manager.submit_order()`.
4. Return the `OrderResult` as JSON.
5. On `OrderSubmissionError`, return HTTP 400 with error detail.

`DELETE /api/orders/{order_id}` — Calls `order_manager.cancel_order(order_id)`. Returns `{"status": "cancelled"}` or HTTP 404 if order not found.

`GET /api/orders` — Query params: `exchange` (optional), `symbol` (optional), `status` (optional, one of `"open"`, `"history"`). If `status="open"`, calls `get_open_orders()`. Otherwise calls `get_order_history()`. Returns list of orders.

`GET /` — Returns `internal/web/static/index.html` content with `Content-Type: text/html`.

**JSON Serialization:** All endpoints returning Decimal values must use a custom JSONResponse class that converts `Decimal` to `str` to avoid float precision loss.

**Acceptance Criteria for this Task:**
1. `GET /api/health` returns `{"status": "ok"}` with HTTP 200.
2. `GET /api/portfolio` returns a valid portfolio snapshot.
3. SSE endpoint at `/api/market-data/stream` delivers JSON events.
4. `POST /api/orders` creates an order and returns the result.
5. `DELETE /api/orders/{id}` cancels an order.
6. Decimal values in all JSON responses are strings, not floats.

---

### Task 13: Dashboard HTML/JS

**Files:**
- Create: `horizon/internal/web/static/index.html`

**Dependencies:** Task 12.

**Specification:**

The dashboard must be a single-page application using vanilla HTML/CSS/JS (no external framework dependencies except via CDN if desired; plain JS is acceptable and preferred for simplicity).

**Required UI sections:**

1. **Header bar:** Display "Horizon Trading Platform" title. Display connection status indicator (green dot when SSE connected, red when disconnected). Display current mode indicator ("LIVE" or "PAPER" — for Step 1, always show "LIVE" as placeholder).

2. **Portfolio Card:** Display a table with columns: Exchange, Asset, Free, Locked, USDT Value. Display total USDT value in large font below the table. Auto-refresh every 10 seconds via `GET /api/portfolio`.

3. **Market Data Card:** Display ticker cards for all configured symbols (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT). Each card shows: symbol name, last price (with `$` prefix), 24h volume (formatted as `$XM`). Prices update via SSE connection.

4. **Order Ticket Card:** Form with dropdowns for Exchange (binance, htx, hyperliquid, bitget), Symbol (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT), Side (buy, sell), Type (market, limit). Price input field (required only for limit, hidden or disabled for market). Volume input field (required, step="0.0001"). Submit button. On submit, POST to `/api/orders` and show alert on success/failure.

5. **Open Orders Card:** Table with columns: Exchange, Symbol, Side, Type, Price, Volume, Filled, Status, Action. Action column contains a "Cancel" button that DELETEs to `/api/orders/{id}`. Auto-refresh every 10 seconds via `GET /api/orders?status=open`.

**Styling:** Dark theme preferred. Use CSS Grid or Flexbox for layout. Must be responsive (readable on desktop; mobile is nice-to-have). Must have proper spacing, readable fonts, and visual hierarchy.

**JavaScript behavior:**
- `API_BASE` constant set to empty string (same-origin).
- SSE connection to `/api/market-data/stream` with reconnection logic (on error, wait 3 seconds and reconnect).
- All fetch calls handle errors with `console.error` and user-visible alerts for submission failures.
- Decimal values displayed with 2 decimal places for prices, 4 for volumes.

**Acceptance Criteria for this Task:**
1. Opening `http://localhost:8080/` displays the dashboard.
2. Portfolio table auto-refreshes every 10 seconds.
3. Market data prices update in real-time via SSE.
4. Order form submits successfully and shows confirmation.
5. Open orders table displays current open orders with cancel buttons.
6. SSE reconnects automatically after disconnection.

---

### Task 14: Application Wiring

**Files:**
- Modify: `horizon/main.py`

**Dependencies:** Task 1–13.

**Specification:**

#### `main.py` (final version)
Must implement the complete application startup sequence:

1. **Configuration loading:** Import `settings` from `internal.config.settings`. Load keys from `key.txt` via `load_keys_from_file()`.

2. **Logging setup:** Configure Python `logging` with level from `settings.app.log_level`. Use structured JSON formatting if `log_level == "info"` or higher.

3. **Database initialization:** Call `init_db(settings.database.path)`. Run migrations.

4. **Exchange registry setup:** Create `ExchangeRegistry`. For each exchange in settings:
   - Binance: if enabled, create `BinanceAdapter(api_key, api_secret, recv_window_ms)` using credentials from settings (overridden by key.txt if present). Register.
   - HTX: same pattern with `HTXAdapter`.
   - Hyperliquid: same pattern with `HyperliquidAdapter` (using wallet_address, private_key).
   - Bitget: same pattern with `BitgetAdapter` (using api_key, api_secret, passphrase).

5. **Market data fetcher:** Create `MarketDataFetcher(registry, settings.market_data.symbols, settings.trading.market_data_poll_interval_seconds)`.

6. **Portfolio tracker:** Create `PortfolioTracker(registry, db, fetcher, settings.trading.portfolio_snapshot_interval_seconds)`.

7. **Order manager:** Create `OrderManager(registry, db)`.

8. **FastAPI app:** Call `create_app(settings, db, registry, fetcher, order_manager, portfolio_tracker)`.

9. **Lifespan context manager:**
   - **Startup:** Start fetcher (`await fetcher.start()`). Start portfolio tracker (`await portfolio_tracker.start()`). Start order manager sync loop (`await order_manager.start_sync_loop()`). Log "Horizon server started".
   - **Shutdown:** Stop fetcher (`await fetcher.stop()`). Stop portfolio tracker (`await portfolio_tracker.stop()`). Stop order manager (`await order_manager.stop()`). Close database (`await close_db(db)`). Log "Horizon server stopped".

10. **Main entry point:** `def main(): uvicorn.run("main:app", host=settings.app.host, port=settings.app.port, log_level=settings.app.log_level)`. The app object must be module-level so uvicorn can import it.

**Acceptance Criteria for this Task:**
1. `python -m horizon` or `horizon-server` starts the application without errors.
2. All components start in the correct order.
3. Graceful shutdown closes all resources cleanly.
4. The server listens on the configured host and port.

---

### Task 15: Build Verification

**Files:** None (verification task).

**Dependencies:** Task 1–14.

**Specification:**

Run the following verification sequence:

1. `cd horizon && pip install -e .`
2. `python -c "import internal; print('import ok')"`
3. Start the server: `python main.py &` (or `horizon-server &`)
4. Wait 5 seconds for startup.
5. Run the acceptance criteria verification commands below.
6. Stop the server with Ctrl+C or `kill`.
7. Verify graceful shutdown message in logs.

**Acceptance Criteria for this Task:**
1. `pip install -e .` completes without errors.
2. All imports succeed.
3. Server starts and logs "Horizon server started".
4. Server stops and logs "Horizon server stopped".

---

## Step 1 Global Acceptance Criteria

The following criteria MUST all pass before Step 1 is considered complete. Run each command and verify the output matches.

| # | Verification Command | Expected Result |
|---|---|---|
| 1 | `curl -s http://localhost:8080/api/health \| python -m json.tool` | `{"status": "ok", "timestamp": <int>}` |
| 2 | `curl -s http://localhost:8080/api/portfolio \| python -m json.tool` | Valid portfolio JSON with `exchanges`, `total_usdt_value`, `timestamp_ms` |
| 3 | `curl -s http://localhost:8080/api/market-data/BTCUSDT \| python -m json.tool` | `{"symbol": "BTCUSDT", "tickers": [...]}` with at least one ticker |
| 4 | `curl -s http://localhost:8080/api/orders?status=open` | `[]` or valid order list |
| 5 | `curl -s -X POST http://localhost:8080/api/orders -H "Content-Type: application/json" -d '{"exchange":"binance","symbol":"BTCUSDT","side":"buy","type":"market","volume":"0.001"}'` | Returns order result with `status` field |
| 6 | Open `http://localhost:8080/` in browser | Dashboard loads with dark theme, all 5 sections visible |
| 7 | Browser DevTools → Network → EventStream | SSE connection to `/api/market-data/stream` receives events |
| 8 | `sqlite3 data/horizon.db "SELECT COUNT(*) FROM orders;"` | Count increases after order submission |
| 9 | `sqlite3 data/horizon.db "SELECT COUNT(*) FROM order_events;"` | Events recorded for each order |
| 10 | Server logs show no unhandled exceptions during 60-second runtime | Clean logs |

---

## Spec Coverage Check

| Spec Requirement | Task(s) |
|---|---|
| Python project scaffold with pyproject.toml | Task 1 |
| SQLite database with aiosqlite and migrations | Task 2 |
| Pydantic Settings configuration system | Task 3 |
| Exchange Adapter ABC and Registry | Task 4 |
| Binance adapter (aiohttp, HMAC-SHA256) | Task 5 |
| HTX adapter (Huobi API v2 auth) | Task 6 |
| Hyperliquid adapter (read-only, wallet auth stubbed) | Task 7 |
| Bitget adapter (v2 auth, all methods) | Task 8 |
| Market Data Fetcher with SSE broadcasting | Task 9 |
| Order Manager with SQLite persistence and event logging | Task 10 |
| Portfolio Tracker with USDT valuation | Task 11 |
| FastAPI web server with REST endpoints | Task 12 |
| Dashboard HTML/JS with SSE, forms, tables | Task 13 |
| Application wiring and lifespan management | Task 14 |
| Build verification and acceptance | Task 15 |

All spec requirements covered. No placeholder gaps.

---

**Plan complete. Proceed to Step 2 only after ALL global acceptance criteria pass.**
