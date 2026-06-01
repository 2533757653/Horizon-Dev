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

// Guardrail APIs
export async function getGuardrailStatus() {
    return request('/guardrails/status');
}

export async function getGuardrailEvents() {
    return request('/guardrails/events');
}

export async function getPaperSummary() {
    return request('/paper/summary');
}

export async function getPaperPositions() {
    return request('/paper/positions');
}

export async function postStrategyMode(mode) {
    return request('/strategy/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
    });
}

export async function putStrategyConfig(config) {
    return request('/strategy/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
    });
}