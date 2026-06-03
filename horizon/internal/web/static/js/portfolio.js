// Portfolio Analytics page — equity curve + asset allocation

import {
    getPortfolioAllocation,
    getPortfolioEquityCurve,
    getPortfolioDailyChange,
    getPaperSummary,
} from './api.js';

const ASSET_COLORS = {
    BTC: '#f7931a',
    ETH: '#627eea',
    SOL: '#14f195',
    USDT: '#26a17b',
    USDC: '#2775ca',
};

function colorForAsset(asset) {
    return ASSET_COLORS[asset?.toUpperCase()] || `hsl(${Math.abs(hash(asset)) % 360}, 65%, 55%)`;
}

function hash(s) {
    let h = 0;
    for (let i = 0; i < (s || '').length; i++) h = ((h << 5) - h) + s.charCodeAt(i);
    return h;
}

async function loadKpis() {
    try {
        const [summary, change] = await Promise.all([
            getPaperSummary(),
            getPortfolioDailyChange(),
        ]);
        const total = summary.total_balance || 0;
        const initial = summary.initial_cash || 0;
        const pnl = total - initial;

        document.getElementById('kpi-total').textContent = `$${total.toFixed(2)}`;
        document.getElementById('kpi-initial').textContent = `$${initial.toFixed(2)}`;

        const el24h = document.getElementById('kpi-24h');
        const c = change.change_pnl || 0;
        el24h.textContent = `${c >= 0 ? '+' : ''}$${c.toFixed(2)} (${(change.change_pct || 0) >= 0 ? '+' : ''}${(change.change_pct || 0).toFixed(2)}%)`;
        el24h.className = `kpi-value ${c > 0 ? 'positive' : c < 0 ? 'negative' : 'neutral'}`;

        const elPnl = document.getElementById('kpi-total-pnl');
        elPnl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${initial > 0 ? ((pnl / initial) * 100).toFixed(2) : '0.00'}%)`;
        elPnl.className = `kpi-value ${pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral'}`;
    } catch (err) {
        console.error('Failed to load KPIs:', err);
    }
}

async function loadEquityCurve() {
    const container = document.getElementById('equity-chart-container');
    const status = document.getElementById('equity-chart-status');
    try {
        const data = await getPortfolioEquityCurve();
        const points = data.points || [];
        if (points.length < 2) {
            status.textContent = 'Not enough data yet — start trading to build your curve.';
            return;
        }
        status.remove();

        const seriesData = points
            .filter(p => p.date !== 'initial' && p.date !== 'today')
            .map(p => ({
                time: p.date,
                value: Number(p.value),
            }));

        const chart = LightweightCharts.createChart(container, {
            layout: {
                background: { type: 'solid', color: 'transparent' },
                textColor: '#8888aa',
            },
            grid: {
                vertLines: { color: 'rgba(255,255,255,0.04)' },
                horzLines: { color: 'rgba(255,255,255,0.04)' },
            },
            rightPriceScale: { borderColor: '#2a2a4a' },
            timeScale: { borderColor: '#2a2a4a', timeVisible: false },
            width: container.clientWidth,
            height: container.clientHeight,
        });

        const area = chart.addAreaSeries({
            lineColor: '#667eea',
            topColor: 'rgba(102, 126, 234, 0.4)',
            bottomColor: 'rgba(102, 126, 234, 0.0)',
            lineWidth: 2,
        });
        area.setData(seriesData);
        chart.timeScale().fitContent();

        // Range summary in header
        const first = seriesData[0]?.value || 0;
        const last = seriesData[seriesData.length - 1]?.value || 0;
        const pct = first > 0 ? ((last - first) / first) * 100 : 0;
        const rangeEl = document.getElementById('equity-range');
        if (rangeEl) {
            rangeEl.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% over ${seriesData.length} day${seriesData.length === 1 ? '' : 's'}`;
            rangeEl.style.color = pct >= 0 ? 'var(--positive)' : 'var(--negative)';
        }
    } catch (err) {
        status.textContent = `Failed to load equity curve: ${err.message}`;
        status.classList.add('error');
    }
}

async function loadAllocation() {
    const donutEl = document.getElementById('allocation-donut');
    const listEl = document.getElementById('allocation-list');
    try {
        const data = await getPortfolioAllocation();
        const allocs = data.allocations || [];
        if (allocs.length === 0) {
            donutEl.innerHTML = '<div class="panel-status">No data</div>';
            listEl.innerHTML = '';
            return;
        }
        renderDonut(donutEl, allocs);
        listEl.innerHTML = allocs.map(a => {
            const color = colorForAsset(a.asset);
            return `
                <div class="allocation-row">
                    <div class="allocation-swatch" style="background:${color}"></div>
                    <div class="allocation-asset">${a.asset}</div>
                    <div class="allocation-bar">
                        <div class="allocation-bar-fill" style="width:${a.pct.toFixed(1)}%; background:${color}"></div>
                    </div>
                    <div class="allocation-value">$${a.value.toFixed(2)}</div>
                    <div class="allocation-pct">${a.pct.toFixed(1)}%</div>
                </div>
            `;
        }).join('');
    } catch (err) {
        console.error('Failed to load allocation:', err);
        listEl.innerHTML = `<div class="panel-status error">${err.message}</div>`;
    }
}

function renderDonut(el, allocs) {
    const size = 220;
    const cx = size / 2;
    const cy = size / 2;
    const r = 80;
    const stroke = 28;
    const circumference = 2 * Math.PI * r;
    let offset = 0;

    const segments = allocs.map(a => {
        const dash = (a.pct / 100) * circumference;
        const seg = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${colorForAsset(a.asset)}" stroke-width="${stroke}" stroke-dasharray="${dash} ${circumference - dash}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})" />`;
        offset += dash;
        return seg;
    }).join('');

    el.innerHTML = `
        <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
            <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="${stroke}" />
            ${segments}
            <text x="${cx}" y="${cy - 6}" text-anchor="middle" font-size="11" fill="#8888aa">ASSETS</text>
            <text x="${cx}" y="${cy + 16}" text-anchor="middle" font-size="20" fill="#e0e0e0" font-weight="700">${allocs.length}</text>
        </svg>
    `;
}

document.addEventListener('DOMContentLoaded', () => {
    loadKpis();
    loadEquityCurve();
    loadAllocation();
    setInterval(() => {
        loadKpis();
        loadEquityCurve();
        loadAllocation();
    }, 30000);
});
