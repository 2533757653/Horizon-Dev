// Position cards renderer

import { formatPrice, formatVolume, formatPnL } from './utils.js';

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
    // Returns: [{ orderId, symbol, side, price, volume, filled }]
    // For now: build from open orders via getOpenOrders
    // Placeholder implementation — returns empty array (no positions yet)
    return [];
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