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