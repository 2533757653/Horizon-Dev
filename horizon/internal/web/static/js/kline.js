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