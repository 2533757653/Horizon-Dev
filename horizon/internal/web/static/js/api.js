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
    const response = await request('/paper/positions');
    return response.positions;  // Extract array from wrapper
}

export async function getPaperTrades() {
    const response = await request('/paper/trades');
    return response.trades;  // Extract array from wrapper
}

export async function getPaperTradeStats() {
    return request('/paper/trades/stats');
}

export async function getPortfolioAllocation() {
    return request('/portfolio/allocation');
}

export async function getPortfolioEquityCurve() {
    return request('/portfolio/equity-curve');
}

export async function getPortfolioDailyChange() {
    return request('/portfolio/daily-change');
}

export async function postStrategyMode(mode) {
    return request('/strategy/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
    });
}

export async function getStrategyMode() {
    return request('/strategy/mode');
}

export async function putStrategyConfig(config) {
    return request('/strategy/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
    });
}

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