// LLM Proposals Queue panel — list + approve/reject + trigger
import { getProposals, approveProposal, rejectProposal, triggerLLMAnalysis, getProposal } from './api.js';

let containerEl = null;
let triggerBtnEl = null;
let countBadgeEl = null;
let pollTimer = null;

export async function initProposals(container, triggerBtn, countBadge) {
    containerEl = container;
    triggerBtnEl = triggerBtn;
    countBadgeEl = countBadge;

    if (triggerBtnEl) {
        triggerBtnEl.addEventListener('click', onTriggerClick);
    }
    await renderProposals();
    pollTimer = setInterval(renderProposals, 10000);
}

export function destroyProposals() {
    if (pollTimer) clearInterval(pollTimer);
}

async function renderProposals() {
    if (!containerEl) return;
    try {
        const data = await getProposals(null, 50);
        const props = data.proposals || [];
        if (countBadgeEl) {
            countBadgeEl.textContent = props.filter(p => p.status === 'proposed').length;
        }
        if (props.length === 0) {
            containerEl.innerHTML = '<div class="empty-state">No proposals yet — try [Run Analysis Now]</div>';
            return;
        }
        containerEl.innerHTML = buildHeader() + props.map(buildRow).join('');
        attachRowHandlers();
    } catch (e) {
        containerEl.innerHTML = `<div class="error-state">Failed: ${escapeHtml(e.message)}</div>`;
    }
}

function buildHeader() {
    return `
      <div class="table-header proposals-cols">
        <span>ID</span><span>Symbol</span><span>Action</span><span>Side</span>
        <span>Volume</span><span>Confidence</span><span>Risk</span>
        <span>Status</span><span>Created</span><span>Actions</span>
      </div>`;
}

function buildRow(p) {
    const id8 = (p.id || '').substring(0, 8);
    const actionIcon = { open: '[OPEN]', close: '[CLOSE]', reduce: '[REDUCE]' }[p.action_type] || '[OPEN]';
    const statusClass = `status-${p.status}`;
    const actions = p.status === 'proposed'
        ? `<button class="btn-approve" data-id="${escapeHtml(p.id)}">Approve</button>
           <button class="btn-reject" data-id="${escapeHtml(p.id)}">Reject</button>`
        : '--';
    const created = p.created_at ? new Date(p.created_at).toLocaleString() : '--';
    return `
      <div class="table-row proposals-cols">
        <span style="font-family:var(--font-mono)">${escapeHtml(id8)}</span>
        <span>${escapeHtml(p.symbol || '--')}</span>
        <span>${actionIcon} ${escapeHtml(p.action_type || 'open')}</span>
        <span class="${p.side === 'buy' ? 'long' : 'short'}">${escapeHtml((p.side || '--').toUpperCase())}</span>
        <span>${escapeHtml(String(p.volume ?? '--'))}</span>
        <span>${escapeHtml(String(p.confidence_score ?? '--'))}</span>
        <span>${escapeHtml(p.risk_tier || '--')}</span>
        <span class="${statusClass}">${escapeHtml(p.status || '--')}</span>
        <span>${escapeHtml(created)}</span>
        <span>${actions}</span>
      </div>`;
}

function attachRowHandlers() {
    containerEl.querySelectorAll('.btn-approve').forEach(b => {
        b.addEventListener('click', () => showApproveModal(b.dataset.id));
    });
    containerEl.querySelectorAll('.btn-reject').forEach(b => {
        b.addEventListener('click', () => showRejectModal(b.dataset.id));
    });
}

async function onTriggerClick() {
    triggerBtnEl.disabled = true;
    triggerBtnEl.textContent = 'Running...';
    try {
        const result = await triggerLLMAnalysis();
        const msg = `Analysis complete: ${result.proposals_generated} proposal(s) generated`;
        if (window.showToast) window.showToast('success', msg); else alert(msg);
        await renderProposals();
    } catch (e) {
        if (window.showToast) window.showToast('error', e.message); else alert('Failed: ' + e.message);
    } finally {
        triggerBtnEl.disabled = false;
        triggerBtnEl.textContent = 'Run Analysis Now';
    }
}

async function showApproveModal(id) {
    try {
        const p = await getProposal(id);
        const overlay = document.getElementById('modal-overlay');
        overlay.innerHTML = '';
        const modal = document.createElement('div');
        modal.className = 'modal modal-wide';
        modal.innerHTML = `
          <h3>Approve Proposal ${escapeHtml(id.substring(0,8))}</h3>
          <div class="proposal-detail">
            <div class="row"><b>Action:</b> ${escapeHtml(p.action_type || '')} ${escapeHtml(p.symbol || '')} ${escapeHtml(p.side || '')} ${escapeHtml(String(p.volume ?? ''))} @ ${escapeHtml(p.price != null ? String(p.price) : 'market')}</div>
            <div class="row"><b>Exchange:</b> ${escapeHtml(p.exchange || '--')}</div>
            <div class="row"><b>Confidence:</b> ${escapeHtml(String(p.confidence_score ?? '--'))}/100  &nbsp; <b>Risk:</b> ${escapeHtml(p.risk_tier || '--')}</div>
            <div class="row"><b>Proposed price:</b> ${escapeHtml(String(p.proposed_price ?? '--'))}  &nbsp; <b>Drift threshold:</b> ${escapeHtml(String(p.price_drift_threshold_pct ?? '--'))}%</div>
            <div class="row"><b>Rationale:</b><pre>${escapeHtml(p.llm_rationale || '')}</pre></div>
            <details><summary>Technical context</summary><pre>${escapeHtml(p.technical_context || '{}')}</pre></details>
            <details><summary>Market snapshot</summary><pre>${escapeHtml(p.market_snapshot || '{}')}</pre></details>
          </div>
          <div class="modal-actions">
            <button class="btn-cancel" id="approve-cancel">Cancel</button>
            <button class="btn-confirm" id="approve-confirm">Confirm Approval</button>
          </div>`;
        overlay.appendChild(modal);
        overlay.classList.remove('hidden');
        modal.querySelector('#approve-cancel').onclick = () => overlay.classList.add('hidden');
        modal.querySelector('#approve-confirm').onclick = async () => {
            try {
                await approveProposal(id);
                window.showToast?.('success', 'Approved & executing');
                overlay.classList.add('hidden');
                await renderProposals();
            } catch (e) {
                window.showToast?.('error', e.message);
            }
        };
    } catch (e) {
        alert('Failed to load proposal: ' + e.message);
    }
}

function showRejectModal(id) {
    const overlay = document.getElementById('modal-overlay');
    overlay.innerHTML = '';
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
      <h3>Reject Proposal ${escapeHtml(id.substring(0,8))}</h3>
      <textarea id="reject-reason" placeholder="Optional reason..." rows="3" style="width:100%"></textarea>
      <div class="modal-actions">
        <button class="btn-cancel" id="rej-cancel">Cancel</button>
        <button class="btn-confirm" id="rej-confirm">Confirm Rejection</button>
      </div>`;
    overlay.appendChild(modal);
    overlay.classList.remove('hidden');
    modal.querySelector('#rej-cancel').onclick = () => overlay.classList.add('hidden');
    modal.querySelector('#rej-confirm').onclick = async () => {
        try {
            await rejectProposal(id, 'dashboard_user', modal.querySelector('#reject-reason').value);
            window.showToast?.('success', 'Rejected');
            overlay.classList.add('hidden');
            await renderProposals();
        } catch (e) {
            window.showToast?.('error', e.message);
        }
    };
}

function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
