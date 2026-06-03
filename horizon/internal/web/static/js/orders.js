// Order form and submission module

let formEl = null;
let exchanges = [];
let selectedSymbol = 'BTCUSDT';

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
            <label>Volume (USD)</label>
            <input type="number" id="order-volume" placeholder="0.00" step="any" min="0" />
            <small class="form-hint">Order size in USD — server converts to base units at current market price</small>
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
        volume_usd: parseFloat(formEl.querySelector('#order-volume').value),
    };

    try {
        const { submitOrder } = await import('./api.js');
        const result = await submitOrder(orderData);
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