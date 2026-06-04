// Frontend entry point — orchestrates all modules

import { initKline, loadChart } from './kline.js';
import { initPositions, renderPositions, updatePrices } from './positions.js';
import { initOrderForm, setSymbol, showToast } from './orders.js';
import { getExchanges, getGuardrailStatus, getGuardrailEvents, getPaperSummary, getPaperPositions, getPaperTrades, getPaperTradeStats, getPortfolioAllocation, getPortfolioEquityCurve, getPortfolioDailyChange, postStrategyMode, putStrategyConfig, getStrategyMode, getPortfolio } from './api.js';
import { initProposals } from './proposals.js';
import { initCoPilot } from './copilot.js';
import { initStrategyConfig } from './strategy_config.js';

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

    // Load initial data
    await loadChart(state.symbol, state.timeframe);
    await renderPositions();

    // Load guardrail UI
    await loadGuardrailStatus();
    await loadPaperTradingStats();
    await loadBalance();
    await loadGuardrailEvents();
    await loadReleasedTrades();

    // Setup UI listeners
    setupChartControls();
    setupStatusIndicator();
    setupTradeFilterListeners();

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
window.showToast = showToast;

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
        const changeEl = document.getElementById('balance-24h-change');

        if (mode === 'paper') {
            // Show paper balance from /api/paper/summary + allocation breakdown
            const [summary, allocation, dailyChange] = await Promise.all([
                getPaperSummary(),
                getPortfolioAllocation(),
                getPortfolioDailyChange(),
            ]);
            titleEl.textContent = 'Paper Trading Balance';
            hintEl.textContent = '(simulated)';
            const totalBalance = summary.total_balance || 0;
            const initialCash = summary.initial_cash || 0;
            const currentCash = summary.current_cash || 0;
            const positionValue = summary.position_value || 0;
            const unrealizedPnl = summary.total_unrealized_pnl || 0;
            totalEl.textContent = `$${totalBalance.toFixed(2)}`;
            totalEl.className = totalBalance >= initialCash ? 'stat-value-large positive' : 'stat-value-large negative';
            render24hChange(changeEl, dailyChange);
            renderBalanceAssets(assetsEl, allocation, {
                initialCash, currentCash, positionValue, unrealizedPnl,
            });
        } else {
            // Show live balance from /api/portfolio
            const portfolio = await getPortfolio();
            titleEl.textContent = 'Account Balance (Live)';
            hintEl.textContent = '(real exchange balances)';
            const totalUsdt = parseFloat(portfolio.total_usdt_value || '0');
            totalEl.textContent = `$${totalUsdt.toFixed(2)}`;
            totalEl.className = 'stat-value-large';
            changeEl.textContent = '';
            changeEl.className = 'balance-24h-change';
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

function render24hChange(el, change) {
    if (!el || !change) return;
    const pct = change.change_pct || 0;
    const pnl = change.change_pnl || 0;
    if (!pnl) {
        el.textContent = '24h: 0.00%';
        el.className = 'balance-24h-change neutral';
        return;
    }
    const arrow = pnl >= 0 ? '▲' : '▼';
    el.textContent = `24h ${arrow} ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% (${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)})`;
    el.className = `balance-24h-change ${pnl >= 0 ? 'positive' : 'negative'}`;
}

function renderBalanceAssets(el, allocation, paper) {
    if (!el) return;
    const allocList = (allocation && allocation.allocations) || [];
    const allocs = allocList.length
        ? allocList
        : [{ asset: 'USDT', value: paper.currentCash || 0, pct: 100, kind: 'cash' }];

    const rows = allocs.map(a => {
        const asset = (a.asset || 'USDT').toLowerCase();
        const cssClass = ['btc', 'eth', 'sol', 'usdt'].includes(asset) ? asset : 'other';
        return `
            <div class="balance-row">
                <span class="asset-name">${a.asset}</span>
                <span class="asset-value">$${a.value.toFixed(2)} <span style="color:var(--text-secondary)">(${a.pct.toFixed(1)}%)</span></span>
                <div class="allocation-bar">
                    <div class="allocation-bar-fill ${cssClass}" style="width:${a.pct.toFixed(2)}%"></div>
                </div>
            </div>
        `;
    }).join('');

    const paperSummary = `
        <div class="balance-row" style="border-top:1px solid var(--border); margin-top:8px; padding-top:8px;">
            <span class="asset-name" style="color:var(--text-secondary)">Initial Cash</span>
            <span class="asset-value">$${paper.initialCash.toFixed(2)}</span>
        </div>
        <div class="balance-row">
            <span class="asset-name" style="color:var(--text-secondary)">Unrealized P&L</span>
            <span class="asset-value ${paper.unrealizedPnl >= 0 ? 'positive' : 'negative'}">
                ${paper.unrealizedPnl >= 0 ? '+' : ''}$${paper.unrealizedPnl.toFixed(2)}
            </span>
        </div>
    `;

    el.innerHTML = rows + paperSummary;
}

// Refresh balance every 30s
setInterval(loadBalance, 30000);

// ===== Released Trades =====
let releasedTradesCache = [];

async function loadReleasedTrades() {
    try {
        const [trades, stats] = await Promise.all([
            getPaperTrades(),
            getPaperTradeStats(),
        ]);
        releasedTradesCache = trades || [];
        renderTradeStatsCards(stats || {});
        populateExchangeFilter(releasedTradesCache);
        renderReleasedTrades();
    } catch (err) {
        console.error('Failed to load released trades:', err);
    }
}

function renderTradeStatsCards(stats) {
    const fmtPnl = (n) => `${n >= 0 ? '+' : ''}$${(n || 0).toFixed(2)}`;
    const pnlClass = (n) => (n >= 0 ? 'positive' : 'negative');

    const totalEl = document.getElementById('rt-total');
    const totalPnlEl = document.getElementById('rt-total-pnl');
    const winRateEl = document.getElementById('rt-win-rate');
    const avgHoldEl = document.getElementById('rt-avg-holding');
    const avgPnlEl = document.getElementById('rt-avg-pnl');
    const winLossEl = document.getElementById('rt-win-loss');

    if (totalEl) totalEl.textContent = stats.total_trades || 0;
    if (totalPnlEl) {
        totalPnlEl.textContent = fmtPnl(stats.total_pnl);
        totalPnlEl.className = `stat-value ${pnlClass(stats.total_pnl)}`;
    }
    if (winRateEl) winRateEl.textContent = `${(stats.win_rate_pct || 0).toFixed(1)}%`;
    if (avgHoldEl) avgHoldEl.textContent = formatHoldingSeconds(stats.avg_holding_seconds || 0);
    if (avgPnlEl) {
        avgPnlEl.textContent = fmtPnl(stats.avg_pnl);
        avgPnlEl.className = `stat-value ${pnlClass(stats.avg_pnl)}`;
    }
    if (winLossEl) {
        winLossEl.textContent = `${stats.wins || 0} / ${stats.losses || 0}`;
        winLossEl.className = `stat-value ${(stats.wins || 0) >= (stats.losses || 0) ? 'positive' : 'negative'}`;
    }
}

function formatHoldingSeconds(seconds) {
    if (!seconds) return '--';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
    return `${(seconds / 86400).toFixed(1)}d`;
}

function populateExchangeFilter(trades) {
    const sel = document.getElementById('rt-filter-exchange');
    if (!sel) return;
    const exchanges = [...new Set(trades.map(t => t.exchange).filter(Boolean))].sort();
    const current = sel.value;
    sel.innerHTML = '<option value="">All</option>' +
        exchanges.map(e => `<option value="${e}">${e}</option>`).join('');
    sel.value = current;
}

function getActiveTradeFilters() {
    return {
        exchange: document.getElementById('rt-filter-exchange')?.value || '',
        side: document.getElementById('rt-filter-side')?.value || '',
        status: document.getElementById('rt-filter-status')?.value || '',
    };
}

function applyTradeFilters(trades, filters) {
    return trades.filter(t => {
        if (filters.exchange && t.exchange !== filters.exchange) return false;
        if (filters.side && t.side !== filters.side) return false;
        if (filters.status === 'open' && t.closed_by_side) return false;
        if (filters.status === 'closed' && !t.closed_by_side) return false;
        return true;
    });
}

function renderReleasedTrades() {
    const container = document.getElementById('released-trades-body');
    if (!container) return;
    const filters = getActiveTradeFilters();
    const filtered = applyTradeFilters(releasedTradesCache, filters);

    if (filtered.length === 0) {
        container.innerHTML = '<div class="table-row" style="grid-column:1 / -1; text-align:center; color:var(--text-secondary);">No released trades</div>';
        return;
    }

    container.innerHTML = filtered.map(t => {
        const isClosed = !!t.closed_by_side;
        const pnl = t.paper_pnl || 0;
        const pnlClass = pnl >= 0 ? 'positive' : 'negative';
        const sideClass = t.side === 'buy' ? 'long' : 'short';
        const sideLabel = t.side === 'buy' ? 'LONG' : 'SHORT';
        const entry = t.price != null ? `$${t.price.toFixed(2)}` : '--';
        const exit = isClosed && t.closed_at
            ? (() => {
                // close price is approximated by t.paper_pnl / volume + entry for the side
                if (!t.volume) return '--';
                const direction = t.side === 'buy' ? 1 : -1;
                const closePrice = (t.price || 0) + direction * (pnl / t.volume);
                return `$${closePrice.toFixed(2)}`;
            })()
            : '--';
        const holding = (() => {
            if (!isClosed || !t.filled_at || !t.closed_at) return '--';
            const filledMs = new Date(t.filled_at).getTime();
            const closedMs = new Date(t.closed_at).getTime();
            if (isNaN(filledMs) || isNaN(closedMs)) return '--';
            return formatHoldingSeconds((closedMs - filledMs) / 1000);
        })();
        const time = t.created_at
            ? new Date(t.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
            : '--';
        return `
            <div class="table-row">
                <span>${time}</span>
                <span style="font-family:var(--font-mono)">${t.symbol || '--'}</span>
                <span class="col-side ${sideClass}">${sideLabel}</span>
                <span>${(t.volume || 0).toFixed(4)}</span>
                <span style="font-family:var(--font-mono)">${entry}</span>
                <span style="font-family:var(--font-mono)">${exit}</span>
                <span>${t.exchange || '--'}</span>
                <span>${t.order_type || '--'}</span>
                <span class="rt-status-${isClosed ? 'closed' : 'open'}">${isClosed ? 'CLOSED' : 'OPEN'}</span>
                <span class="rt-pnl ${pnlClass}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</span>
                <span>${holding}</span>
            </div>
        `;
    }).join('');
}

function setupTradeFilterListeners() {
    ['rt-filter-exchange', 'rt-filter-side', 'rt-filter-status'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', renderReleasedTrades);
    });
}

// Refresh released trades every 15s
setInterval(loadReleasedTrades, 15000);
