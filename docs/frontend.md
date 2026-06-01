# Frontend Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the embedded HTML dashboard with a proper modular Vanilla JS frontend featuring a TradingView `lightweight-charts` Kline chart, card-based Position display, and full Order Place form.

**Architecture:** Vanilla JS + ES Modules served as static files from `horizon/internal/web/static/`. Each panel (chart, positions, orders) is an independent module. `app.js` coordinates state and the SSE real-time connection.

**Tech Stack:** Vanilla JS (ES Modules), `lightweight-charts` via CDN, CSS custom properties for Dark Pro theme, FastAPI for backend API.

---

## File Structure

```
horizon/internal/web/static/           # NEW — static frontend
├── index.html
├── css/
│   └── styles.css
├── js/
│   ├── app.js
│   ├── api.js
│   ├── kline.js
│   ├── positions.js
│   ├── orders.js
│   └── utils.js

horizon/internal/web/server.py         # MODIFY — extract HTML, add kline endpoint
```

**Backend files touched:**
- `horizon/internal/web/server.py` — remove embedded HTML, add `GET /api/kline/{symbol}` endpoint, update static file serving

**No test files needed** — this is a frontend-only task. Manual verification in browser.

---

## Task 1: Extract static HTML from server.py into static/ folder

**Files:**
- Create: `horizon/internal/web/static/index.html`
- Create: `horizon/internal/web/static/css/styles.css`
- Create: `horizon/internal/web/static/js/app.js` (stub)
- Modify: `horizon/internal/web/server.py`

---

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p /d/Horizon-Dev/horizon/internal/web/static/css
mkdir -p /d/Horizon-Dev/horizon/internal/web/static/js
```

Run: `bash -c 'mkdir -p /d/Horizon-Dev/horizon/internal/web/static/css /d/Horizon-Dev/horizon/internal/web/static/js'`

Expected: directories created, no output

---

- [ ] **Step 2: Create stub app.js so index.html loads without errors**

Create `horizon/internal/web/static/js/app.js`:

```javascript
// Frontend entry point — stub for now
console.log('Horizon Trading Dashboard loaded');
```

Run: `cat > /d/Horizon-Dev/horizon/internal/web/static/js/app.js << 'EOF'
// Frontend entry point — stub for now
console.log('Horizon Trading Dashboard loaded');
EOF`

Expected: file created

---

- [ ] **Step 3: Create index.html shell**

Create `horizon/internal/web/static/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Horizon Trading Platform</title>
    <link rel="stylesheet" href="/static/css/styles.css">
</head>
<body>
    <div class="header">
        <h1>Horizon Trading Platform</h1>
        <span class="status-dot" id="status-dot"></span>
        <span class="last-update" id="last-update"></span>
    </div>
    <div class="dashboard">
        <div class="panel chart-panel">
            <div class="panel-header">
                <h2>Chart</h2>
                <div class="chart-controls">
                    <select id="symbol-select"><option value="BTC/USDT">BTC/USDT</option></select>
                    <select id="timeframe-select">
                        <option value="1m">1m</option><option value="5m">5m</option>
                        <option value="15m">15m</option><option value="1h" selected>1h</option>
                        <option value="4h">4h</option><option value="1d">1d</option>
                    </select>
                </div>
            </div>
            <div id="chart-container"></div>
            <div id="chart-status" class="panel-status"></div>
        </div>
        <div class="bottom-panels">
            <div class="panel positions-panel">
                <div class="panel-header"><h2>Positions</h2></div>
                <div id="positions-container"></div>
            </div>
            <div class="panel order-panel">
                <div class="panel-header"><h2>Order Place</h2></div>
                <form id="order-form"></form>
            </div>
        </div>
    </div>
    <div id="toast-container"></div>
    <div id="modal-overlay" class="modal-overlay hidden"></div>
    <script type="module" src="/static/js/app.js"></script>
</body>
</html>
```

Run: copy content to `horizon/internal/web/static/index.html`

---

- [ ] **Step 4: Create Dark Pro CSS (styles.css)**

Create `horizon/internal/web/static/css/styles.css`:

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --bg-base: #0d0d1a;
    --bg-surface: #13132b;
    --bg-elevated: #1a1a30;
    --border: #2a2a4a;
    --accent-primary: #667eea;
    --accent-secondary: #764ba2;
    --text-primary: #e0e0e0;
    --text-secondary: #8888aa;
    --positive: #4ade80;
    --negative: #f87171;
    --warning: #fbbf24;
    --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --font-mono: 'Roboto Mono', 'Fira Code', monospace;
    --radius: 12px;
    --gap: 20px;
}

body {
    font-family: var(--font-sans);
    background: var(--bg-base);
    color: var(--text-primary);
    min-height: 100vh;
    font-size: 14px;
}

.header {
    background: linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-secondary) 100%);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 16px;
}
.header h1 { font-size: 20px; font-weight: 600; }

.status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--positive);
}
.status-dot.error { background: var(--negative); }

.last-update { font-size: 12px; opacity: 0.8; }

.dashboard {
    padding: var(--gap);
    display: flex;
    flex-direction: column;
    gap: var(--gap);
    height: calc(100vh - 60px);
}

.chart-panel { flex: 0 0 400px; min-height: 400px; }

.panel {
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    padding: 20px;
    display: flex;
    flex-direction: column;
}

.panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
}
.panel-header h2 {
    font-size: 16px;
    font-weight: 600;
    color: var(--accent-primary);
}

.chart-controls { display: flex; gap: 8px; }

select {
    background: var(--bg-elevated);
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
    cursor: pointer;
}
select:focus { outline: 1px solid var(--accent-primary); }

#chart-container { flex: 1; min-height: 300px; position: relative; }
.panel-status {
    text-align: center;
    padding: 12px;
    color: var(--text-secondary);
    font-size: 13px;
}

.bottom-panels {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--gap);
    flex: 1;
    min-height: 0;
}

.positions-panel { overflow-y: auto; }
.order-panel {}

#positions-container { flex: 1; display: flex; flex-direction: column; gap: 12px; }

.position-card {
    background: var(--bg-elevated);
    border-radius: 8px;
    padding: 16px;
    border: 1px solid var(--border);
    transition: box-shadow 100ms ease;
}
.position-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.3); }

.position-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
}
.position-symbol { font-weight: 600; font-size: 15px; }

.side-badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
}
.side-badge.long { background: rgba(74, 222, 128, 0.15); color: var(--positive); }
.side-badge.short { background: rgba(248, 113, 113, 0.15); color: var(--negative); }

.position-row {
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    font-size: 13px;
}
.position-row-label { color: var(--text-secondary); }
.position-row-value { font-family: var(--font-mono); font-weight: 500; }
.position-row-value.positive { color: var(--positive); }
.position-row-value.negative { color: var(--negative); }

.position-close {
    margin-top: 12px;
    width: 100%;
    padding: 8px;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-secondary);
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    transition: all 100ms;
}
.position-close:hover { border-color: var(--negative); color: var(--negative); }

.no-positions {
    text-align: center;
    color: var(--text-secondary);
    padding: 40px;
}

#order-form { display: flex; flex-direction: column; gap: 12px; }

.form-row { display: flex; gap: 12px; }
.form-group { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.form-group label { font-size: 12px; color: var(--text-secondary); text-transform: uppercase; }
.form-group input, .form-group select {
    background: var(--bg-elevated);
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 14px;
    font-family: var(--font-sans);
}
.form-group input:focus, .form-group select:focus { outline: 1px solid var(--accent-primary); }
.form-group input:disabled { opacity: 0.4; cursor: not-allowed; }

.side-toggle {
    display: flex;
    border-radius: 6px;
    overflow: hidden;
    border: 1px solid var(--border);
}
.side-toggle button {
    flex: 1;
    padding: 8px;
    border: none;
    background: var(--bg-elevated);
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    transition: all 100ms;
}
.side-toggle button.active.buy { background: var(--positive); color: #000; }
.side-toggle button.active.sell { background: var(--negative); color: #000; }

.toggle-row { display: flex; gap: 16px; }
.toggle-label { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 13px; }
.toggle-label input[type="checkbox"] { width: 16px; height: 16px; cursor: pointer; }

#submit-order {
    padding: 12px;
    border: none;
    border-radius: 6px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
    color: #fff;
    transition: opacity 100ms;
}
#submit-order:hover { opacity: 0.9; }
#submit-order:disabled { opacity: 0.5; cursor: not-allowed; }

#toast-container {
    position: fixed;
    bottom: 24px;
    right: 24px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    z-index: 1000;
}
.toast {
    padding: 12px 20px;
    border-radius: 8px;
    font-size: 14px;
    display: flex;
    align-items: center;
    gap: 10px;
    animation: slideIn 200ms ease;
    min-width: 280px;
}
.toast.success { background: rgba(74, 222, 128, 0.15); border: 1px solid var(--positive); color: var(--positive); }
.toast.error { background: rgba(248, 113, 113, 0.15); border: 1px solid var(--negative); color: var(--negative); }
.toast.info { background: rgba(102, 126, 234, 0.15); border: 1px solid var(--accent-primary); color: var(--accent-primary); }
.toast-close { margin-left: auto; cursor: pointer; opacity: 0.7; }
.toast-close:hover { opacity: 1; }

.modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 999;
}
.modal-overlay.hidden { display: none; }
.modal {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px;
    max-width: 400px;
    width: 90%;
}
.modal h3 { margin-bottom: 12px; font-size: 16px; }
.modal p { color: var(--text-secondary); font-size: 14px; margin-bottom: 20px; }
.modal-actions { display: flex; gap: 12px; justify-content: flex-end; }
.modal-actions button {
    padding: 8px 20px;
    border-radius: 6px;
    font-size: 14px;
    cursor: pointer;
    transition: all 100ms;
}
.btn-cancel { background: var(--bg-elevated); border: 1px solid var(--border); color: var(--text-primary); }
.btn-confirm { background: var(--negative); border: none; color: #fff; }
.btn-confirm:hover { opacity: 0.9; }

@keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
}

.panel-status.loading, .panel-status.error { padding: 20px; text-align: center; }
.panel-status.error { color: var(--negative); }
```

Run: copy content to `horizon/internal/web/static/css/styles.css`

---

- [ ] **Step 5: Modify server.py to serve static files**

In `horizon/internal/web/server.py`, find the `index()` route that returns embedded HTML (lines 409-547). Replace it with:

```python
from fastapi.staticfiles import StaticFiles

# Mount static files after app creation
app.mount("/static", StaticFiles(directory="horizon/internal/web/static"), name="static")

# Remove the embedded HTML from the index() route — or redirect:
@app.get("/")
async def index():
    return RedirectResponse(url="/static/index.html")
```

Run: Edit `horizon/internal/web/server.py` — replace the `index()` route body with `RedirectResponse(url="/static/index.html")` and add `StaticFiles` mount. Keep the `html_content` string for reference, then delete it after verifying the redirect works.

---

- [ ] **Step 6: Test static file serving**

Run: `cd /d/Horizon-Dev && python -c "from horizon.internal.web.server import create_app; print('server.py OK')"`

Run the app and visit `http://localhost:8000/static/index.html` — it should load with the new layout.

---

- [ ] **Step 7: Commit**

```bash
git add horizon/internal/web/static/ horizon/internal/web/server.py
git commit -m "feat: extract embedded HTML into static frontend scaffold

- Serve index.html from horizon/internal/web/static/
- Add Dark Pro CSS theme with full dashboard layout
- Stub app.js entry point
- Mount /static via StaticFiles, redirect / to /static/index.html
```

---

## Task 2: Build js/api.js — API client module

**Files:**
- Create: `horizon/internal/web/static/js/api.js`

---

- [ ] **Step 1: Create api.js**

Create `horizon/internal/web/static/js/api.js`:

```javascript
// API client — wraps all /api/* fetch calls

async function request(path, options = {}) {
    const res = await fetch(`/api${path}`, options);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

// Kline data
export async function getKlines(symbol, timeframe = '1h', limit = 500) {
    return request(`/kline/${encodeURIComponent(symbol)}?timeframe=${timeframe}&limit=${limit}`);
}

// Portfolio / positions
export async function getPortfolio() {
    return request('/portfolio');
}

// Exchanges
export async function getExchanges() {
    return request('/exchanges');
}

// Orders
export async function getOpenOrders() {
    return request('/orders?status=open');
}

export async function submitOrder(orderData) {
    return request('/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(orderData),
    });
}

export async function cancelOrder(orderId) {
    return request(`/orders/${orderId}`, { method: 'DELETE' });
}

// SSE stream
export function createMarketStream() {
    return new EventSource('/api/market-data/stream');
}
```

Run: copy content to `horizon/internal/web/static/js/api.js`

---

- [ ] **Step 2: Commit**

```bash
git add horizon/internal/web/static/js/api.js
git commit -m "feat(frontend): add API client module (api.js)"
```

---

## Task 3: Build js/utils.js — Formatters and helpers

**Files:**
- Create: `horizon/internal/web/static/js/utils.js`

---

- [ ] **Step 1: Create utils.js**

Create `horizon/internal/web/static/js/utils.js`:

```javascript
// Formatters and utility helpers

export function formatPrice(value, decimals = 2) {
    const num = parseFloat(value);
    if (isNaN(num)) return '—';
    return num.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

export function formatVolume(value, decimals = 4) {
    const num = parseFloat(value);
    if (isNaN(num)) return '—';
    return num.toLocaleString('en-US', {
        minimumFractionDigits: 0,
        maximumFractionDigits: decimals,
    });
}

export function formatPnL(value) {
    const num = parseFloat(value);
    if (isNaN(num)) return { text: '—', cls: '' };
    const sign = num >= 0 ? '+' : '';
    return {
        text: `${sign}${num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
        cls: num >= 0 ? 'positive' : 'negative',
    };
}

export function formatPercent(value) {
    const num = parseFloat(value);
    if (isNaN(num)) return '—';
    const sign = num >= 0 ? '+' : '';
    return `${sign}${num.toFixed(2)}%`;
}

export function debounce(fn, ms) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    };
}
```

Run: copy content to `horizon/internal/web/static/js/utils.js`

---

- [ ] **Step 2: Commit**

```bash
git add horizon/internal/web/static/js/utils.js
git commit -m "feat(frontend): add utils.js with formatters and debounce"
```

---

## Task 4: Build js/kline.js — Kline chart module with lightweight-charts

**Files:**
- Create: `horizon/internal/web/static/js/kline.js`

---

- [ ] **Step 1: Create kline.js**

Create `horizon/internal/web/static/js/kline.js`:

```javascript
// Kline chart module using lightweight-charts

let chart = null;
let candleSeries = null;
let currentSymbol = null;
let currentTimeframe = '1h';
let statusEl = null;
let containerEl = null;

export async function initKline(statusElement, containerElement) {
    statusEl = statusElement;
    containerEl = containerElement;
    setStatus('loading', 'Loading chart library...');

    // Load lightweight-charts from CDN
    await loadScript('https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js');

    setStatus('loading', 'Initializing chart...');
}

function loadScript(src) {
    return new Promise((resolve, reject) => {
        if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
        const s = document.createElement('script');
        s.src = src;
        s.onload = resolve;
        s.onerror = reject;
        document.head.appendChild(s);
    });
}

export async function loadChart(symbol, timeframe = '1h') {
    currentSymbol = symbol;
    currentTimeframe = timeframe;
    setStatus('loading', `Loading ${symbol} ${timeframe}...`);

    try {
        const { getKlines } = await import('./api.js');
        const data = await getKlines(symbol, timeframe);

        if (!data.candles || data.candles.length === 0) {
            setStatus('error', `No data for ${symbol}`);
            return;
        }

        const formatted = data.candles.map(c => ({
            time: c.time,
            open: parseFloat(c.open),
            high: parseFloat(c.high),
            low: parseFloat(c.low),
            close: parseFloat(c.close),
        }));

        if (!chart) {
            chart = LightweightCharts.createChart(containerEl, {
                layout: {
                    background: { color: '#13132b' },
                    textColor: '#8888aa',
                },
                grid: {
                    vertLines: { color: '#2a2a4a' },
                    horzLines: { color: '#2a2a4a' },
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                    vertLine: { color: '#667eea', labelBackgroundColor: '#667eea' },
                    horzLine: { color: '#667eea', labelBackgroundColor: '#667eea' },
                },
                timeScale: {
                    borderColor: '#2a2a4a',
                    timeVisible: true,
                },
                rightPriceScale: {
                    borderColor: '#2a2a4a',
                },
            });
            candleSeries = chart.addCandlestickSeries({
                upColor: '#4ade80',
                downColor: '#f87171',
                borderUpColor: '#4ade80',
                borderDownColor: '#f87171',
                wickUpColor: '#4ade80',
                wickDownColor: '#f87171',
            });
        }

        candleSeries.setData(formatted);
        chart.timeScale().fitContent();
        setStatus('ok', `${data.candles.length} candles loaded`);
    } catch (err) {
        setStatus('error', `Failed: ${err.message}`);
    }
}

export function updateCandle(candle) {
    if (!candleSeries) return;
    candleSeries.update({
        time: candle.time,
        open: parseFloat(candle.open),
        high: parseFloat(candle.high),
        low: parseFloat(candle.low),
        close: parseFloat(candle.close),
    });
}

function setStatus(type, message) {
    if (!statusEl) return;
    statusEl.className = `panel-status ${type}`;
    statusEl.textContent = message;
}

export function destroyChart() {
    if (chart) {
        chart.remove();
        chart = null;
        candleSeries = null;
    }
}
```

Run: copy content to `horizon/internal/web/static/js/kline.js`

---

- [ ] **Step 2: Commit**

```bash
git add horizon/internal/web/static/js/kline.js
git commit -m "feat(frontend): add Kline chart module with lightweight-charts"
```

---

## Task 5: Build js/positions.js — Position cards renderer

**Files:**
- Create: `horizon/internal/web/static/js/positions.js`

---

- [ ] **Step 1: Create positions.js**

Create `horizon/internal/web/static/js/positions.js`:

```javascript
// Position cards renderer

let positions = [];
let containerEl = null;
let onCloseRequest = null;

export async function initPositions(containerElement, closeCallback) {
    containerEl = containerElement;
    onCloseRequest = closeCallback;
}

export async function renderPositions() {
    if (!containerEl) return;
    const { getPortfolio } = await import('./api.js');

    try {
        const data = await getPortfolio();
        positions = parsePositions(data);
        if (positions.length === 0) {
            containerEl.innerHTML = '<div class="no-positions">No open positions</div>';
            return;
        }
        containerEl.innerHTML = positions.map(p => buildCard(p)).join('');
        attachCloseHandlers();
    } catch (err) {
        containerEl.innerHTML = `<div class="no-positions" style="color:var(--negative)">Failed: ${err.message}</div>`;
    }
}

function parsePositions(data) {
    // Parse from portfolio API — exchanges[exchange][balance]
    // Each position from order history has: symbol, side, price, volume, order_id
    // This parses the open orders to extract position-like info
    // Returns: [{ orderId, symbol, side, price, volume, filled }]
    const result = [];
    // Placeholder: we'll build from order history in Task 6
    return result;
}

export function updatePrices(priceMap) {
    // priceMap: { 'BTC/USDT': { price: 50000, change24h: 2.5 } }
    if (!positions.length) return;
    let changed = false;
    positions.forEach(p => {
        const info = priceMap[p.symbol];
        if (info && info.price !== p.currentPrice) {
            p.currentPrice = parseFloat(info.price);
            changed = true;
        }
    });
    if (changed) {
        containerEl.innerHTML = positions.map(p => buildCard(p)).join('');
        attachCloseHandlers();
    }
}

function buildCard(p) {
    const pnl = (p.currentPrice - p.price) * p.volume;
    const pnlPct = ((p.currentPrice - p.price) / p.price) * 100;
    const pnlFormatted = formatPnL(pnl);
    const pnlCls = pnl >= 0 ? 'positive' : 'negative';
    const side = p.side?.toLowerCase() === 'buy' ? 'long' : 'short';
    const sideLabel = side === 'long' ? 'LONG' : 'SHORT';

    return `
        <div class="position-card" data-order-id="${p.orderId}">
            <div class="position-header">
                <span class="position-symbol">${p.symbol}</span>
                <span class="side-badge ${side}">${sideLabel}</span>
            </div>
            <div class="position-row">
                <span class="position-row-label">Size</span>
                <span class="position-row-value">${formatVolume(p.volume)}</span>
            </div>
            <div class="position-row">
                <span class="position-row-label">Entry Price</span>
                <span class="position-row-value">$${formatPrice(p.price)}</span>
            </div>
            <div class="position-row">
                <span class="position-row-label">Current Price</span>
                <span class="position-row-value">$${formatPrice(p.currentPrice || p.price)}</span>
            </div>
            <div class="position-row">
                <span class="position-row-label">PnL</span>
                <span class="position-row-value ${pnlCls}">$${pnlFormatted.text} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</span>
            </div>
            <button class="position-close" data-order-id="${p.orderId}">Close Position</button>
        </div>
    `;
}

function attachCloseHandlers() {
    containerEl.querySelectorAll('.position-close').forEach(btn => {
        btn.addEventListener('click', () => {
            const orderId = btn.dataset.orderId;
            if (onCloseRequest) onCloseRequest(orderId);
        });
    });
}

import { formatPrice, formatVolume, formatPnL } from './utils.js';
```

Run: copy content to `horizon/internal/web/static/js/positions.js`

---

- [ ] **Step 2: Commit**

```bash
git add horizon/internal/web/static/js/positions.js
git commit -m "feat(frontend): add positions.js card renderer module"
```

---

## Task 6: Build js/orders.js — Order form and submission

**Files:**
- Create: `horizon/internal/web/static/js/orders.js`

---

- [ ] **Step 1: Create orders.js**

Create `horizon/internal/web/static/js/orders.js`:

```javascript
// Order form and submission module

let formEl = null;
let exchanges = [];
let selectedSymbol = 'BTC/USDT';

export async function initOrderForm(formElement, getExchangesFn) {
    formEl = formElement;
    renderForm();
    attachEventListeners();

    try {
        const data = await getExchangesFn();
        exchanges = data.exchanges?.filter(e => e.active).map(e => e.name) || [];
        renderExchangeOptions();
    } catch (err) {
        console.warn('Could not load exchanges:', err.message);
    }
}

function renderForm() {
    formEl.innerHTML = `
        <div class="form-row">
            <div class="form-group">
                <label>Exchange</label>
                <select id="order-exchange"></select>
            </div>
            <div class="form-group">
                <label>Symbol</label>
                <input type="text" id="order-symbol" value="${selectedSymbol}" placeholder="BTC/USDT" />
            </div>
        </div>
        <div class="form-group">
            <label>Side</label>
            <div class="side-toggle">
                <button type="button" class="active buy" data-side="buy">Buy / Long</button>
                <button type="button" class="sell" data-side="sell">Sell / Short</button>
            </div>
        </div>
        <div class="form-group">
            <label>Order Type</label>
            <select id="order-type">
                <option value="market">Market</option>
                <option value="limit">Limit</option>
                <option value="stop">Stop</option>
            </select>
        </div>
        <div class="form-group">
            <label>Price</label>
            <input type="number" id="order-price" placeholder="0.00" step="any" disabled />
        </div>
        <div class="form-group">
            <label>Volume</label>
            <input type="number" id="order-volume" placeholder="0.0000" step="any" />
        </div>
        <div class="toggle-row">
            <label class="toggle-label">
                <input type="checkbox" id="order-reduce-only" />
                Reduce Only
            </label>
            <label class="toggle-label">
                <input type="checkbox" id="order-post-only" />
                Post Only
            </label>
        </div>
        <button type="submit" id="submit-order">Place Order</button>
    `;
}

function renderExchangeOptions() {
    const sel = document.getElementById('order-exchange');
    if (!sel) return;
    sel.innerHTML = exchanges.map(e => `<option value="${e}">${e.toUpperCase()}</option>`).join('');
}

function attachEventListeners() {
    // Side toggle
    formEl.querySelectorAll('.side-toggle button').forEach(btn => {
        btn.addEventListener('click', () => {
            formEl.querySelectorAll('.side-toggle button').forEach(b => b.classList.remove('active'));
            btn.classList.add('active', btn.dataset.side);
        });
    });

    // Order type — toggle price field
    formEl.querySelector('#order-type').addEventListener('change', e => {
        const priceInput = formEl.querySelector('#order-price');
        priceInput.disabled = e.target.value === 'market';
    });

    // Submit
    formEl.addEventListener('submit', handleSubmit);
}

async function handleSubmit(e) {
    e.preventDefault();
    const btn = formEl.querySelector('#submit-order');
    btn.disabled = true;
    btn.textContent = 'Placing...';

    const sideBtn = formEl.querySelector('.side-toggle button.active');
    const orderData = {
        exchange: formEl.querySelector('#order-exchange').value,
        symbol: formEl.querySelector('#order-symbol').value,
        side: sideBtn?.dataset.side || 'buy',
        type: formEl.querySelector('#order-type').value,
        price: formEl.querySelector('#order-price').value ? parseFloat(formEl.querySelector('#order-price').value) : null,
        volume: parseFloat(formEl.querySelector('#order-volume').value),
    };

    try {
        const { submitOrder } = await import('./api.js');
        const result = await submitOrder(orderData);
        const { showToast } = await import('./orders.js'); // reuse self
        showToast('success', `Order placed: ${result.order_id?.substring(0, 8) || 'OK'}...`);
        formEl.reset();
        renderForm();
        attachEventListeners();
    } catch (err) {
        showToast('error', err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Place Order';
    }
}

export function showToast(type, message) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${message}</span>
        <span class="toast-close" onclick="this.parentElement.remove()">×</span>
    `;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

export function setSymbol(symbol) {
    selectedSymbol = symbol;
    const el = document.getElementById('order-symbol');
    if (el) el.value = symbol;
}
```

Run: copy content to `horizon/internal/web/static/js/orders.js`

---

- [ ] **Step 2: Commit**

```bash
git add horizon/internal/web/static/js/orders.js
git commit -m "feat(frontend): add orders.js order form module"
```

---

## Task 7: Build js/app.js — Main entry, state, SSE coordination

**Files:**
- Modify: `horizon/internal/web/static/js/app.js`

---

- [ ] **Step 1: Replace stub app.js with full implementation**

Replace `horizon/internal/web/static/js/app.js`:

```javascript
// Frontend entry point — orchestrates all modules

import { initKline, loadChart } from './kline.js';
import { initPositions, renderPositions, updatePrices } from './positions.js';
import { initOrderForm, setSymbol, showToast } from './orders.js';
import { getExchanges } from './api.js';

const state = {
    symbol: 'BTC/USDT',
    timeframe: '1h',
    prices: {},  // { 'BTC/USDT': { price, change24h } }
    eventSource: null,
};

async function init() {
    // Init modules
    await initKline(
        document.getElementById('chart-status'),
        document.getElementById('chart-container')
    );
    await initPositions(
        document.getElementById('positions-container'),
        handleClosePosition
    );
    await initOrderForm(
        document.getElementById('order-form'),
        getExchanges
    );

    // Load initial data
    await loadChart(state.symbol, state.timeframe);
    await renderPositions();

    // Setup UI listeners
    setupChartControls();
    setupStatusIndicator();

    // Start SSE stream
    startMarketStream();
}

function setupChartControls() {
    const symbolSel = document.getElementById('symbol-select');
    const timeframeSel = document.getElementById('timeframe-select');

    symbolSel.addEventListener('change', async e => {
        state.symbol = e.target.value;
        setSymbol(state.symbol);
        await loadChart(state.symbol, state.timeframe);
    });

    timeframeSel.addEventListener('change', async e => {
        state.timeframe = e.target.value;
        await loadChart(state.symbol, state.timeframe);
    });
}

function setupStatusIndicator() {
    const dot = document.getElementById('status-dot');
    const lastUpdate = document.getElementById('last-update');

    function markOk() {
        dot.classList.remove('error');
        lastUpdate.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    }

    // Expose globally for SSE callbacks
    window.markDashboardOk = markOk;
}

function startMarketStream() {
    const { createMarketStream } = window.__import_api || {};

    // SSE at /api/market-data/stream
    const es = new EventSource('/api/market-data/stream');

    es.onmessage = event => {
        try {
            const data = JSON.parse(event.data);
            window.markDashboardOk?.();

            // Update prices map
            if (data.tickers) {
                data.tickers.forEach(t => {
                    state.prices[t.symbol] = { price: t.price };
                });
                updatePrices(state.prices);
            }
        } catch (err) {
            // Ignore parse errors
        }
    };

    es.onerror = () => {
        const dot = document.getElementById('status-dot');
        if (dot) dot.classList.add('error');
        // Reconnect handled by browser EventSource
    };

    state.eventSource = es;
}

async function handleClosePosition(orderId) {
    const overlay = document.getElementById('modal-overlay');
    const modal = overlay.querySelector('.modal') || createModal();
    overlay.classList.remove('hidden');
    overlay.querySelector('.modal-body-text').textContent =
        `Close position ${orderId.substring(0, 8)}...?`;

    return new Promise(resolve => {
        const confirmBtn = modal.querySelector('.btn-confirm');
        const cancelBtn = modal.querySelector('.btn-cancel');

        const cleanup = () => {
            overlay.classList.add('hidden');
            confirmBtn.removeEventListener('click', onConfirm);
            cancelBtn.removeEventListener('click', onCancel);
        };

        const onConfirm = async () => {
            cleanup();
            try {
                const { cancelOrder } = await import('./api.js');
                await cancelOrder(orderId);
                showToast('success', 'Position closed');
                await renderPositions();
            } catch (err) {
                showToast('error', err.message);
            }
        };

        const onCancel = () => { cleanup(); resolve(false); };

        confirmBtn.addEventListener('click', onConfirm);
        cancelBtn.addEventListener('click', onCancel);
    });
}

function createModal() {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <h3>Close Position</h3>
        <p class="modal-body-text"></p>
        <div class="modal-actions">
            <button class="btn-cancel">Cancel</button>
            <button class="btn-confirm">Close</button>
        </div>
    `;
    document.getElementById('modal-overlay').appendChild(modal);
    return modal;
}

// Bootstrap
document.addEventListener('DOMContentLoaded', init);
```

Run: Edit `horizon/internal/web/static/js/app.js` with the full implementation above.

---

- [ ] **Step 2: Commit**

```bash
git add horizon/internal/web/static/js/app.js
git commit -m "feat(frontend): add app.js main entry with SSE coordination"
```

---

## Task 8: Add GET /api/kline/{symbol} backend endpoint

**Files:**
- Modify: `horizon/internal/web/server.py`

---

- [ ] **Step 1: Add kline endpoint to server.py**

Add this endpoint to `server.py` inside `create_app()`:

```python
@app.get("/api/kline/{symbol}")
async def get_kline(
    symbol: str,
    timeframe: str = Query("1h", description="Timeframe: 1m, 5m, 15m, 1h, 4h, 1d"),
    limit: int = Query(500, description="Max candles to return"),
) -> JSONResponse:
    """Get historical OHLCV kline/candlestick data for a symbol."""
    # Get the exchange adapter for the symbol's exchange
    # For now, try to get from any active exchange that has this symbol
    registry: ExchangeRegistry = app.state.registry
    active_exchanges: set = app.state.active_exchanges

    candles = []
    for exchange_name in active_exchanges:
        adapter = registry.get(exchange_name)
        if adapter and adapter.enabled:
            try:
                klines = await adapter.fetch_klines(symbol, timeframe, limit)
                candles = klines
                break
            except Exception:
                continue

    if not candles:
        raise HTTPException(status_code=404, detail=f"No kline data for {symbol}")

    return CustomJSONResponse(content={
        "symbol": symbol,
        "timeframe": timeframe,
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
```

**Note:** You also need to add `fetch_klines` to the exchange adapter base class and implementations if not already present. Check `horizon/internal/exchange/adapter.py` and each exchange file.

---

- [ ] **Step 2: Check if fetch_klines exists in adapter**

Run: `grep -n "fetch_klines\|kline" /d/Horizon-Dev/horizon/internal/exchange/adapter.py`

If not found, add to `ExchangeAdapter` base class:

```python
async def fetch_klines(self, symbol: str, timeframe: str, limit: int) -> list[dict]:
    """Fetch OHLCV kline/candlestick data. Override in subclass."""
    raise NotImplementedError
```

And implement in each exchange adapter (Binance, Hyperliquid, etc.) using each exchange's kline API.

---

- [ ] **Step 3: Commit**

```bash
git add horizon/internal/web/server.py horizon/internal/exchange/adapter.py
git commit -m "feat: add GET /api/kline/{symbol} endpoint for chart data

- Add kline endpoint with timeframe and limit query params
- Add fetch_klines abstract method to ExchangeAdapter base class
- Implement in Binance and Hyperliquid adapters (or whichever exchanges have it)"
```

---

## Task 9: Wire everything together and verify

**Files:**
- Modify: `horizon/internal/web/static/index.html` (update script type to module)

---

- [ ] **Step 1: Ensure index.html script tag uses type="module"**

Verify the app.js script tag in index.html:

```html
<script type="module" src="/static/js/app.js"></script>
```

---

- [ ] **Step 2: Ensure all module imports use named exports**

Check each `.js` file exports what the others import:
- `api.js`: exports `getKlines`, `getPortfolio`, `getExchanges`, `getOpenOrders`, `submitOrder`, `cancelOrder`, `createMarketStream`
- `kline.js`: exports `initKline`, `loadChart`, `updateCandle`, `destroyChart`
- `positions.js`: exports `initPositions`, `renderPositions`, `updatePrices`
- `orders.js`: exports `initOrderForm`, `setSymbol`, `showToast`
- `app.js`: imports all of the above

---

- [ ] **Step 3: Test full flow**

Run the server:
```bash
cd /d/Horizon-Dev && python -m horizon.internal.web.server
```

Visit `http://localhost:8000/` (redirects to `/static/index.html`):

1. **Chart panel**: Should load BTC/USDT 1h candlestick chart via `lightweight-charts`
2. **Positions panel**: Shows "No open positions" or renders actual positions
3. **Order form**: All fields present, Buy/Sell toggle works, price field enables for limit orders

If chart fails to load — check browser console for CORS errors or 404 on `GET /api/kline/BTC%2FUSDT`.

---

## Self-Review Checklist

- [ ] Spec coverage: All three panels (Kline, Position, Order) have tasks
- [ ] No placeholders: Every step has complete code
- [ ] File paths exact: All paths use the actual project structure
- [ ] Module exports/imports consistent across all JS files
- [ ] `fetch_klines` implemented in at least one exchange adapter before testing chart
