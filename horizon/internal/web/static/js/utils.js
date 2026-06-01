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