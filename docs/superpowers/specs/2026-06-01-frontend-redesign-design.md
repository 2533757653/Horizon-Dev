# Frontend Redesign Design Spec

**Date:** 2026-06-01
**Topic:** Trading Dashboard Redesign (Kline Chart, Position, Order Place)

---

## 1. Concept & Vision

A professional-grade cryptocurrency trading dashboard that gives the user a clear, real-time view of market action and instant control over their positions. The interface prioritizes information density without visual clutter — every pixel serves a purpose. It should feel like a tool built by traders, for traders.

## 2. Design Language

### Aesthetic Direction
**Dark Pro** — Professional trading terminal aesthetic. Inspired by Binance Pro, TradingView, and institutional terminals. Deep, rich dark backgrounds with subtle blue/purple accents. Clean card-based panels with clear visual hierarchy.

### Color Palette
- **Background (base):** `#0d0d1a`
- **Surface (cards):** `#13132b`
- **Surface (elevated):** `#1a1a30`
- **Border:** `#2a2a4a`
- **Primary accent:** `#667eea` (blue-purple gradient feel)
- **Secondary accent:** `#764ba2`
- **Text primary:** `#e0e0e0`
- **Text secondary:** `#8888aa`
- **Positive / Profit:** `#4ade80`
- **Negative / Loss:** `#f87171`
- **Warning:** `#fbbf24`

### Typography
- **Font:** `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
- **Headings:** 18px semibold, accent color
- **Body:** 14px regular, primary text
- **Labels:** 12px uppercase, secondary text
- **Monospace (prices):** `'Roboto Mono', 'Fira Code', monospace`

### Spatial System
- Card padding: 20px
- Card border-radius: 12px
- Section gap: 20px
- Grid gap: 16px

### Motion Philosophy
- Minimal animation — trading dashboards need to feel instant and responsive
- Card hover: subtle `box-shadow` lift (100ms ease)
- Data updates: flash highlight on changed values (green/red, 300ms fade)
- No decorative animations

### Visual Assets
- No external icon libraries — use Unicode symbols or simple CSS shapes
- Charts via `lightweight-charts` (TradingView)

---

## 3. Layout & Structure

### Overall Layout: Top-Down Stack (Option A)

```
┌─────────────────────────────────────────┐
│              Header (logo + status)      │
├─────────────────────────────────────────┤
│                                         │
│           Kline Chart (full width)       │
│         (with symbol selector)           │
│                                         │
├───────────────────┬─────────────────────┤
│                   │                     │
│   Position Cards  │    Order Place     │
│   (scrollable)    │    (full form)     │
│                   │                     │
└───────────────────┴─────────────────────┘
```

### Responsive Strategy
- Minimum supported width: 1024px (trading dashboards are desktop-first)
- Below 1024px: stack Position and Order Place vertically
- Chart maintains 16:9 minimum aspect ratio

### Panel Sizes
- Chart: full width, 400px min height
- Position panel: ~50% width, fills remaining height
- Order panel: ~50% width, fixed form height

---

## 4. Features & Interactions

### 4.1 Kline Chart Panel

**Core Features:**
- Candlestick (OHLC) chart using `lightweight-charts`
- Symbol selector dropdown (top-left of chart)
- Timeframe selector: 1m, 5m, 15m, 1h, 4h, 1d
- Real-time updates via SSE at `/api/market-data/stream`
- Crosshair with price/time tooltip on hover

**Interactions:**
- Symbol change: fetch historical klines via `GET /api/kline/{symbol}?timeframe=1h`, replace chart data
- Timeframe change: refetch with new timeframe parameter
- Mouse drag: pan chart
- Scroll wheel: zoom

**States:**
- Loading: "Loading chart..." centered text
- No data: "No data for {symbol}" with secondary text
- Error: "Failed to load chart" with retry button

### 4.2 Position Panel

**Core Features:**
- Card per position (Option B from design)
- Each card shows:
  - Symbol + side badge (LONG green / SHORT red)
  - Size (volume)
  - Entry price
  - Current price (live via SSE)
  - Unrealized PnL (USD + %)
  - Liquidation price (if applicable)
  - Close position button

**Interactions:**
- Cards scroll vertically if overflow
- Close button: confirmation modal before sending `DELETE /api/orders/{order_id}`
- PnL flashes green/red on price updates

**States:**
- No positions: "No open positions" centered, muted text
- Loading: skeleton card placeholders

### 4.3 Order Place Panel

**Core Features:**
- Full order form (Option B from design)
- Fields:
  - Exchange selector (dropdown)
  - Symbol (text input)
  - Side: Buy / Sell toggle
  - Order type: Market / Limit / Stop dropdown
  - Price (enabled for limit/stop orders)
  - Volume / Quantity
  - Reduce-only toggle
  - Post-only toggle
- Submit button: "Place Order"
- Order result feedback (success/error toast)

**Interactions:**
- Buy/Sell toggle visually highlights selected (green for buy, red for sell)
- Order type change shows/hides price field
- Submit: POST to `/api/orders`, show spinner during submission
- Success: green toast "Order placed: {order_id}", clear form
- Error: red toast with error message from backend

**States:**
- Submitting: button disabled, shows spinner
- Validation error: inline field errors in red
- Success: toast notification, form resets

---

## 5. Component Inventory

### Header
- Logo/title left-aligned
- System status indicator (green dot = healthy)
- Last update timestamp

### ChartPanel
- States: loading, loaded, error, no-data
- Contains: symbol-select, timeframe-select, chart canvas

### PositionCard
- States: default, profit, loss, closing
- Contains: symbol badge, price info, PnL display, close button

### OrderForm
- States: idle, submitting, success, error
- Contains: exchange-select, symbol-input, side-toggle, type-select, price-input, volume-input, toggles, submit-button

### Toast
- Types: success (green), error (red), info (blue)
- Auto-dismiss after 4 seconds
- Manual dismiss via X button

### Modal (for close confirmation)
- Centered overlay with blur backdrop
- Confirm / Cancel buttons

---

## 6. Technical Approach

### Architecture: Vanilla JS + ES Modules

```
horizon/internal/web/static/
├── index.html              # Main dashboard shell
├── css/
│   └── styles.css          # Dark Pro theme, layout
├── js/
│   ├── app.js              # Entry point, state, init
│   ├── api.js              # API client (fetch wrappers)
│   ├── kline.js            # Kline chart module
│   ├── positions.js        # Position cards renderer
│   ├── orders.js           # Order form & submission
│   └── utils.js            # Formatters, helpers
```

### File Responsibilities

**app.js**
- Initializes all modules
- Holds shared `state` object (selectedSymbol, positions)
- Sets up SSE connection for real-time market data
- Coordinates cross-module updates

**api.js**
- Wraps all `/api/*` fetch calls
- Returns parsed JSON (throws on error)
- Functions: `getKlines()`, `getPositions()`, `submitOrder()`, `cancelOrder()`, `getMarketDataStream()`

**kline.js**
- Initializes `lightweight-charts` on canvas element
- `loadChart(symbol, timeframe)` — fetches and renders
- `updateCandle(data)` — real-time update via SSE
- `destroyChart()` — cleanup on symbol change

**positions.js**
- `render(positions[])` — renders all position cards
- `updatePrices(prices)` — called by SSE, updates current prices and PnL
- `showCloseConfirm(orderId)` — opens confirmation modal
- `closePosition(orderId)` — calls API and re-renders

**orders.js**
- `init()` — wires up form submit, buy/sell toggle, type change
- `submitOrder(formData)` — calls API
- `showToast(type, message)` — success/error feedback

**utils.js**
- `formatPrice(value, decimals)`
- `formatVolume(value)`
- `formatPnL(value)` — adds + sign, color class
- `debounce(fn, ms)`

### API Endpoints (Backend Changes)

**New endpoint needed:**
- `GET /api/kline/{symbol}?timeframe=1h&limit=500`
  - Returns: `{ "symbol": "BTC/USDT", "timeframe": "1h", "candles": [{ "time": 1234567890, "open": 50000, "high": 50100, "low": 49900, "close": 50050, "volume": 123.45 }, ...] }`

**Existing endpoints used:**
- `GET /api/portfolio` — for current positions
- `POST /api/orders` — submit order
- `DELETE /api/orders/{order_id}` — close position
- `GET /api/orders?status=open` — list open orders
- `GET /api/market-data/stream` — SSE for real-time prices
- `GET /api/exchanges` — populate exchange dropdown

### Key Implementation Notes

1. **lightweight-charts** is loaded via CDN (`https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js`)
2. SSE connection managed in `app.js`, dispatches events to `kline.js` and `positions.js`
3. Position PnL calculation done client-side using current price from SSE + entry price from position data
4. All monetary values displayed with appropriate precision (8 decimals for crypto, 2 for USD)
5. Form validation: all fields required except price (only for limit/stop orders)

---

## 7. Implementation Order

1. Extract static HTML from `server.py` into `static/` folder structure
2. Create `css/styles.css` with Dark Pro theme
3. Build `js/api.js` API client
4. Build `js/utils.js` formatters
5. Build `js/kline.js` chart module (placeholder div first, then integrate lightweight-charts)
6. Build `js/positions.js` position cards
7. Build `js/orders.js` order form
8. Build `js/app.js` main entry, SSE coordination
9. Add `GET /api/kline/{symbol}` endpoint to backend
10. Wire everything together in `index.html`
11. Test full flow: chart loads → positions display → order places
