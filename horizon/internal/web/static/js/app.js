// Frontend entry point — orchestrates all modules

import { initKline, loadChart } from './kline.js';
import { initPositions, renderPositions, updatePrices } from './positions.js';
import { initOrderForm, setSymbol, showToast } from './orders.js';
import { getExchanges, getGuardrailStatus, getGuardrailEvents, getPaperSummary, getPaperPositions, postStrategyMode, putStrategyConfig, getStrategyMode } from './api.js';

const state = {
    symbol: 'BTCUSDT',
    timeframe: '1h',
    prices: {},  // { 'BTC/USDT': { price, change24h } }
    eventSource: null,
    guardrailCountdownTimer: null,
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

    // Load guardrail UI
    await loadGuardrailStatus();
    await loadPaperTradingStats();
    await loadBalance();
    await loadGuardrailEvents();

    // Setup UI listeners
    setupChartControls();
    setupStatusIndicator();

    // Start SSE stream
    startMarketStream();

    // Auto-refresh guardrail events every 10s
    setInterval(loadGuardrailEvents, 10000);
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

// Guardrail UI Functions
async function loadGuardrailStatus() {
    try {
        const status = await getGuardrailStatus();
        updateModeIndicator(status.mode);
        updateCooldownBadge(status.cooldowns);
        updateCollaborativeCountdown(status.downgrade_expires_at);
        document.getElementById('guardrail-count-badge').textContent = status.guardrail_events_today || 0;
    } catch (err) {
        console.error('Failed to load guardrail status:', err);
    }
}

function updateModeIndicator(mode) {
    const badge = document.getElementById('mode-badge');
    badge.className = 'mode-badge';
    if (mode === 'LIVE') {
        badge.classList.add('mode-live');
        badge.textContent = 'LIVE';
        document.getElementById('paper-banner').classList.add('hidden');
        document.getElementById('collab-banner').classList.add('hidden');
    } else if (mode === 'PAPER') {
        badge.classList.add('mode-paper');
        badge.textContent = 'PAPER';
        document.getElementById('paper-banner').classList.remove('hidden');
        document.getElementById('collab-banner').classList.add('hidden');
    } else if (mode === 'COLLABORATIVE') {
        badge.classList.add('mode-collaborative');
        badge.textContent = 'COLLABORATIVE';
        document.getElementById('paper-banner').classList.add('hidden');
        document.getElementById('collab-banner').classList.remove('hidden');
    }
}

function updateCooldownBadge(cooldowns) {
    const badge = document.getElementById('cooldown-badge');
    const countEl = document.getElementById('cooldown-count');
    if (cooldowns && cooldowns.length > 0) {
        badge.classList.remove('hidden');
        countEl.textContent = cooldowns.length;
    } else {
        badge.classList.add('hidden');
    }
}

function updateCollaborativeCountdown(expiresAt) {
    const container = document.getElementById('collaborative-countdown');
    const timer = document.getElementById('countdown-timer');

    if (!expiresAt) {
        container.classList.add('hidden');
        if (state.guardrailCountdownTimer) {
            clearInterval(state.guardrailCountdownTimer);
            state.guardrailCountdownTimer = null;
        }
        return;
    }

    container.classList.remove('hidden');

    function updateTimer() {
        const now = Date.now();
        const expiry = new Date(expiresAt).getTime();
        const diff = expiry - now;

        if (diff <= 0) {
            timer.textContent = 'EXPIRED';
            loadGuardrailStatus();
            return;
        }

        const minutes = Math.floor(diff / 60000);
        const seconds = Math.floor((diff % 60000) / 1000);
        timer.textContent = `${minutes}m ${seconds}s`;
    }

    updateTimer();
    if (state.guardrailCountdownTimer) clearInterval(state.guardrailCountdownTimer);
    state.guardrailCountdownTimer = setInterval(updateTimer, 1000);
}

async function loadGuardrailEvents() {
    try {
        const events = await getGuardrailEvents();
        renderGuardrailEvents(events);
    } catch (err) {
        console.error('Failed to load guardrail events:', err);
    }
}

function renderGuardrailEvents(events) {
    const container = document.getElementById('guardrail-events-body');
    if (!events || events.length === 0) {
        container.innerHTML = '<div class="table-row"><span class="col-time">--</span><span class="col-rule">No events</span><span class="col-action">--</span><span class="col-symbol">--</span><span class="col-details">--</span></div>';
        return;
    }

    container.innerHTML = events.map(e => {
        const actionClass = e.action === 'BLOCK_AND_DOWNGRADE' ? 'action-block_and_downgrade' : 'action-block';
        const time = e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : '--';
        return `
            <div class="table-row">
                <span class="col-time">${time}</span>
                <span class="col-rule">${e.rule || '--'}</span>
                <span class="col-action ${actionClass}">${e.action || '--'}</span>
                <span class="col-symbol">${e.symbol || '--'}</span>
                <span class="col-details">${e.details || '--'}</span>
            </div>
        `;
    }).join('');
}

async function loadPaperTradingStats() {
    try {
        const [summary, positions] = await Promise.all([getPaperSummary(), getPaperPositions()]);

        // Update stats
        const realizedPnl = document.getElementById('realized-pnl');
        const unrealizedPnl = document.getElementById('unrealized-pnl');
        const openPosCount = document.getElementById('open-positions-count');
        const totalTrades = document.getElementById('total-trades');

        const realized = summary.total_realized_pnl || 0;
        const unrealized = summary.total_unrealized_pnl || 0;

        realizedPnl.textContent = `$${realized.toFixed(2)}`;
        realizedPnl.className = `stat-value ${realized >= 0 ? 'positive' : 'negative'}`;

        unrealizedPnl.textContent = `$${unrealized.toFixed(2)}`;
        unrealizedPnl.className = `stat-value ${unrealized >= 0 ? 'positive' : 'negative'}`;

        openPosCount.textContent = positions.length || 0;
        totalTrades.textContent = summary.total_trades || 0;

        // Update positions table
        renderPaperPositions(positions);
    } catch (err) {
        console.error('Failed to load paper trading stats:', err);
    }
}

function renderPaperPositions(positions) {
    const container = document.getElementById('paper-positions-body');
    if (!positions || positions.length === 0) {
        container.innerHTML = '<div class="table-row"><span class="col-symbol">No positions</span><span class="col-side">--</span><span class="col-volume">--</span><span class="col-entry">--</span><span class="col-price">--</span><span class="col-pnl">--</span></div>';
        return;
    }

    container.innerHTML = positions.map(p => {
        const sideClass = p.side?.toUpperCase() === 'LONG' ? 'long' : 'short';
        const pnl = p.unrealized_pnl || 0;
        const pnlClass = pnl >= 0 ? 'positive' : 'negative';
        return `
            <div class="table-row">
                <span class="col-symbol">${p.symbol || '--'}</span>
                <span class="col-side ${sideClass}">${p.side || '--'}</span>
                <span class="col-volume">${p.volume || 0}</span>
                <span class="col-entry">$${(p.avg_entry_price || 0).toFixed(2)}</span>
                <span class="col-price">$${(p.current_price || 0).toFixed(2)}</span>
                <span class="col-pnl ${pnlClass}">$${pnl.toFixed(2)}</span>
            </div>
        `;
    }).join('');
}

async function switchMode(mode) {
    try {
        await postStrategyMode(mode);
        showToast('success', `Switched to ${mode} mode`);
        await loadGuardrailStatus();
    } catch (err) {
        showToast('error', `Failed to switch mode: ${err.message}`);
    }
}

async function toggleAutonomy(enabled) {
    try {
        await putStrategyConfig({ autonomy_enabled: enabled });
        const status = document.getElementById('autonomy-status');
        status.textContent = enabled ? 'ON' : 'OFF';
        status.style.color = enabled ? 'var(--positive)' : 'var(--text-secondary)';
        showToast('success', `Auto-Execution ${enabled ? 'enabled' : 'disabled'}`);
    } catch (err) {
        showToast('error', `Failed to update config: ${err.message}`);
    }
}

function showModeToggleModal() {
    const overlay = document.getElementById('modal-overlay');
    const modal = overlay.querySelector('.modal') || createModeModal();
    overlay.classList.remove('hidden');
    modal.querySelector('.modal-body-text').textContent = 'Switch to PAPER mode? No real orders will be executed.';
}

function createModeModal() {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.id = 'mode-modal';
    modal.innerHTML = `
        <h3>Change Trading Mode</h3>
        <p class="modal-body-text">Switch to PAPER mode? No real orders will be executed.</p>
        <div class="modal-actions">
            <button class="btn-cancel" onclick="closeModal()">Cancel</button>
            <button class="btn-paper" onclick="confirmSwitchMode('PAPER')">PAPER</button>
            <button class="btn-confirm" onclick="confirmSwitchMode('LIVE')">LIVE</button>
        </div>
    `;
    document.getElementById('modal-overlay').appendChild(modal);
    return modal;
}

function confirmSwitchMode(mode) {
    closeModal();
    switchMode(mode);
}

function closeModal() {
    document.getElementById('modal-overlay').classList.add('hidden');
}

function showCooldownModal(cooldowns) {
    const overlay = document.getElementById('modal-overlay');
    const existing = document.getElementById('cooldown-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.id = 'cooldown-modal';
    modal.innerHTML = `
        <h3>Active Cooldowns</h3>
        <div class="cooldown-modal-content">
            <div class="cooldown-list">
                ${cooldowns && cooldowns.length > 0
                    ? cooldowns.map(c => `
                        <div class="cooldown-item">
                            <span class="cooldown-item-label">${c.rule || 'Unknown'}</span>
                            <span class="cooldown-item-remaining">${c.remaining_seconds || 0}s</span>
                        </div>
                    `).join('')
                    : '<p>No active cooldowns</p>'
                }
            </div>
        </div>
        <div class="modal-actions">
            <button class="btn-confirm" onclick="closeModal()">Close</button>
        </div>
    `;
    overlay.classList.remove('hidden');
    overlay.appendChild(modal);
}

// Expose functions globally for inline handlers
window.showModeToggleModal = showModeToggleModal;
window.confirmSwitchMode = confirmSwitchMode;
window.closeModal = closeModal;
window.toggleAutonomy = toggleAutonomy;
window.showCooldownModal = showCooldownModal;

// Bootstrap
document.addEventListener('DOMContentLoaded', init);
// ===== Balance Display =====
async function loadBalance() {
    try {
        const modeRes = await getStrategyMode();
        const mode = modeRes.mode;
        const titleEl = document.getElementById('balance-title');
        const hintEl = document.getElementById('balance-mode-hint');
        const totalEl = document.getElementById('total-balance');
        const assetsEl = document.getElementById('balance-assets');

        if (mode === 'paper') {
            // Show paper balance from /api/paper/summary
            const summary = await getPaperSummary();
            titleEl.textContent = 'Paper Trading Balance';
            hintEl.textContent = '(simulated)';
            const totalBalance = summary.total_balance || 0;
            const initialCash = summary.initial_cash || 0;
            const currentCash = summary.current_cash || 0;
            const positionValue = summary.position_value || 0;
            const unrealizedPnl = summary.total_unrealized_pnl || 0;
            totalEl.textContent = `$${totalBalance.toFixed(2)}`;
            totalEl.className = totalBalance >= initialCash ? 'stat-value-large positive' : 'stat-value-large negative';
            assetsEl.innerHTML = `
                <div class="balance-row">
                    <span class="asset-name">Initial Cash</span>
                    <span class="asset-value">$${initialCash.toFixed(2)}</span>
                </div>
                <div class="balance-row">
                    <span class="asset-name">Available Cash</span>
                    <span class="asset-value">$${currentCash.toFixed(2)}</span>
                </div>
                <div class="balance-row">
                    <span class="asset-name">Position Value</span>
                    <span class="asset-value">$${positionValue.toFixed(2)}</span>
                </div>
                <div class="balance-row">
                    <span class="asset-name">Unrealized P&L</span>
                    <span class="asset-value ${unrealizedPnl >= 0 ? 'positive' : 'negative'}">
                        ${unrealizedPnl >= 0 ? '+' : ''}$${unrealizedPnl.toFixed(2)}
                    </span>
                </div>
            `;
        } else {
            // Show live balance from /api/portfolio
            const portfolio = await getPortfolio();
            titleEl.textContent = 'Account Balance (Live)';
            hintEl.textContent = '(real exchange balances)';
            const totalUsdt = parseFloat(portfolio.total_usdt_value || '0');
            totalEl.textContent = `$${totalUsdt.toFixed(2)}`;
            totalEl.className = 'stat-value-large';
            const exchanges = portfolio.exchanges || {};
            const rows = [];
            for (const [exchange, balances] of Object.entries(exchanges)) {
                for (const b of balances) {
                    rows.push(`
                        <div class="balance-row">
                            <span class="asset-name">${b.asset}</span>
                            <span class="asset-value">${b.free}</span>
                        </div>
                    `);
                }
            }
            if (rows.length === 0) {
                assetsEl.innerHTML = '<div class="balance-row"><span class="asset-name">No balances yet (fetching...)</span></div>';
            } else {
                assetsEl.innerHTML = rows.join('');
            }
        }
    } catch (e) {
        console.error('Failed to load balance:', e);
    }
}

// Refresh balance every 30s
setInterval(loadBalance, 30000);
