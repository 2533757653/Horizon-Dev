// AI Co-Pilot — multi-turn chat with optional [Pre-fill Order Ticket] hook
import {
    createCoPilotSession, listCoPilotSessions, getCoPilotMessages,
    sendCoPilotMessage, deleteCoPilotSession,
} from './api.js';
import { setSymbol } from './orders.js';

let panelEl = null;
let toggleBtnEl = null;
let messagesEl = null;
let inputEl = null;
let sendBtnEl = null;
let sessionSelectEl = null;
let newBtnEl = null;
let currentSessionId = null;

export async function initCoPilot(els) {
    panelEl = els.panel;
    toggleBtnEl = els.toggleBtn;
    messagesEl = els.messages;
    inputEl = els.input;
    sendBtnEl = els.sendBtn;
    sessionSelectEl = els.sessionSelect;
    newBtnEl = els.newBtn;

    if (toggleBtnEl) toggleBtnEl.addEventListener('click', togglePanel);
    if (sendBtnEl) sendBtnEl.addEventListener('click', onSend);
    if (newBtnEl) newBtnEl.addEventListener('click', onNewSession);
    if (sessionSelectEl) sessionSelectEl.addEventListener('change', onSessionChange);
    if (inputEl) {
        inputEl.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
        });
    }
    await loadSessions();
}

function togglePanel() {
    panelEl.classList.toggle('collapsed');
}

async function loadSessions() {
    try {
        const data = await listCoPilotSessions(20);
        const sessions = data.sessions || [];
        sessionSelectEl.innerHTML = '<option value="">-- Select a session --</option>' +
            sessions.map(s => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.title || s.id.substring(0,8))} (${s.message_count})</option>`).join('');
        if (sessions.length > 0) {
            sessionSelectEl.value = sessions[0].id;
            await loadMessages(sessions[0].id);
        }
    } catch (e) {
        renderError('Failed to load sessions: ' + e.message);
    }
}

async function onNewSession() {
    const title = prompt('Session title (optional):') || null;
    try {
        const s = await createCoPilotSession(title);
        await loadSessions();
        sessionSelectEl.value = s.id;
        await loadMessages(s.id);
    } catch (e) {
        renderError(e.message);
    }
}

async function onSessionChange() {
    const id = sessionSelectEl.value;
    if (id) await loadMessages(id);
}

async function loadMessages(sessionId) {
    currentSessionId = sessionId;
    try {
        const data = await getCoPilotMessages(sessionId);
        messagesEl.innerHTML = (data.messages || []).map(renderMessage).join('');
        attachSuggestionHandlers();
        scrollToBottom();
    } catch (e) {
        renderError(e.message);
    }
}

async function onSend() {
    if (!currentSessionId) {
        alert('Please create or select a session first.');
        return;
    }
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    sendBtnEl.disabled = true;

    // Optimistic user bubble
    messagesEl.insertAdjacentHTML('beforeend', renderMessage({
        role: 'user', content: text, created_at: new Date().toISOString(),
    }));
    scrollToBottom();

    try {
        const reply = await sendCoPilotMessage(currentSessionId, text);
        messagesEl.insertAdjacentHTML('beforeend', renderMessage({
            role: 'assistant',
            content: reply.assistant_text,
            trade_suggestion: reply.trade_suggestion,
            context_summary: reply.context_summary,
            token_count_input: reply.token_count_input,
            token_count_output: reply.token_count_output,
            latency_ms: reply.latency_ms,
            created_at: new Date().toISOString(),
        }));
        attachSuggestionHandlers();
        scrollToBottom();
    } catch (e) {
        renderError(e.message);
    } finally {
        sendBtnEl.disabled = false;
        inputEl.focus();
    }
}

function renderMessage(m) {
    const sideClass = m.role === 'user' ? 'msg-user' : 'msg-assistant';
    const escaped = escapeHtml(m.content || '');
    let suggestion = '';
    if (m.trade_suggestion) {
        const t = m.trade_suggestion;
        const data = encodeURIComponent(JSON.stringify(t));
        suggestion = `
          <div class="trade-suggestion">
            <b>Suggested trade:</b> ${escapeHtml(t.symbol || '')} ${escapeHtml(t.side || '')} ${escapeHtml(String(t.volume ?? ''))} @ ${escapeHtml(t.price != null ? String(t.price) : 'market')} (${escapeHtml(t.exchange || '')})
            <div class="suggestion-rationale">${escapeHtml(t.rationale || '')}</div>
            <button class="btn-prefill" data-trade="${escapeHtml(data)}">Pre-fill Order Ticket</button>
          </div>`;
    }
    let footer = '';
    if (m.role === 'assistant' && m.context_summary) {
        const cs = m.context_summary;
        const badges = [];
        if (cs.prices_fed && cs.prices_fed.length) badges.push('prices');
        if (cs.indicators_fed && cs.indicators_fed.length) badges.push('indicators');
        if (cs.positions_fed) badges.push('positions');
        if (cs.history_messages) badges.push(`${cs.history_messages} prior msgs`);
        footer = `<div class="msg-footer">${badges.join(' · ')}${
            m.latency_ms ? ` · ${m.latency_ms}ms · ${m.token_count_input || 0}+${m.token_count_output || 0} tok` : ''
        }</div>`;
    }
    return `
      <div class="msg-bubble ${sideClass}">
        <div class="msg-content">${escaped}</div>
        ${suggestion}
        ${footer}
      </div>`;
}

function attachSuggestionHandlers() {
    messagesEl.querySelectorAll('.btn-prefill').forEach(btn => {
        btn.addEventListener('click', () => {
            try {
                const trade = JSON.parse(decodeURIComponent(btn.dataset.trade));
                prefillOrderTicket(trade);
            } catch (e) {
                alert('Failed to read trade suggestion: ' + e.message);
            }
        });
    });
}

function prefillOrderTicket(t) {
    // Use existing orders.js machinery
    if (typeof setSymbol === 'function') setSymbol(t.symbol);
    const sel = document.getElementById('order-exchange');
    if (sel && t.exchange) sel.value = t.exchange;
    const typeSel = document.getElementById('order-type');
    if (typeSel) {
        typeSel.value = t.order_type || 'market';
        typeSel.dispatchEvent(new Event('change'));
    }
    // Side toggle
    document.querySelectorAll('.side-toggle button').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.side === t.side) b.classList.add('active', t.side);
    });
    const priceEl = document.getElementById('order-price');
    if (priceEl && t.price) priceEl.value = t.price;
    const volEl = document.getElementById('order-volume');
    if (volEl && t.volume) volEl.value = t.volume;

    // Scroll to order panel
    document.querySelector('.order-panel')?.scrollIntoView({ behavior: 'smooth' });
    window.showToast?.('info', 'Order ticket pre-filled -- review then Submit');
}

function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderError(msg) {
    messagesEl.insertAdjacentHTML('beforeend',
        `<div class="msg-error">${escapeHtml(msg)}</div>`);
    scrollToBottom();
}

function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
