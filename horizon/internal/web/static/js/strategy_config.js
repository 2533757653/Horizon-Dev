// Strategy Configuration modal — read + edit thresholds, whitelist, system prompt
import { getStrategyConfig, putStrategyConfig } from './api.js';

let modalEl = null;

export async function initStrategyConfig(triggerBtn) {
    if (!triggerBtn) return;
    triggerBtn.addEventListener('click', openModal);
}

async function openModal() {
    if (!modalEl) {
        modalEl = document.createElement('div');
        modalEl.className = 'modal modal-wide strategy-config-modal';
        const overlay = document.getElementById('modal-overlay');
        if (overlay) overlay.appendChild(modalEl);
    }
    try {
        const cfg = await getStrategyConfig();
        renderForm(cfg);
        const overlay = document.getElementById('modal-overlay');
        if (overlay) overlay.classList.remove('hidden');
    } catch (e) {
        alert('Failed to load config: ' + e.message);
    }
}

function renderForm(cfg) {
    const whitelistStr = (() => {
        try {
            const arr = JSON.parse(cfg.asset_whitelist || '[]');
            return Array.isArray(arr) ? arr.join(', ') : '';
        } catch {
            return '';
        }
    })();
    const tier = cfg.max_risk_tier || 'low';
    modalEl.innerHTML = `
      <h3>Strategy Configuration</h3>
      <form id="cfg-form" class="cfg-form">
        <label>System prompt
          <textarea name="system_prompt" rows="8">${escapeHtml(cfg.system_prompt || '')}</textarea>
        </label>
        <label>Asset whitelist (comma-separated symbols)
          <input name="asset_whitelist" type="text" value="${escapeHtml(whitelistStr)}" />
        </label>
        <div class="cfg-row">
          <label>Min confidence threshold
            <input name="min_confidence_threshold" type="number" min="0" max="100" value="${cfg.min_confidence_threshold ?? 75}" />
          </label>
          <label>Max risk tier
            <select name="max_risk_tier">
              <option ${tier === 'low' ? 'selected':''}>low</option>
              <option ${tier === 'medium' ? 'selected':''}>medium</option>
              <option ${tier === 'high' ? 'selected':''}>high</option>
            </select>
          </label>
          <label>Analysis interval (hours)
            <input name="analysis_interval_hours" type="number" min="1" value="${cfg.analysis_interval_hours ?? 8}" />
          </label>
        </div>
        <div class="cfg-row">
          <label>Max position %
            <input name="max_position_pct" type="number" step="0.1" min="0.1" max="100" value="${cfg.max_position_pct ?? 20}" />
          </label>
          <label>Max daily loss %
            <input name="max_daily_loss_pct" type="number" step="0.1" min="0.1" max="100" value="${cfg.max_daily_loss_pct ?? 5}" />
          </label>
          <label>Max exchange exposure %
            <input name="max_exchange_exposure_pct" type="number" step="0.1" min="0.1" max="100" value="${cfg.max_exchange_exposure_pct ?? 50}" />
          </label>
        </div>
        <div class="cfg-row">
          <label>Cooldown (seconds)
            <input name="cooldown_seconds" type="number" min="0" value="${cfg.cooldown_seconds ?? 300}" />
          </label>
          <label>Order min notional
            <input name="order_min_notional" type="number" step="0.01" min="0.01" value="${cfg.order_min_notional ?? 10}" />
          </label>
          <label>Order max notional
            <input name="order_max_notional" type="number" step="0.01" min="0.01" value="${cfg.order_max_notional ?? 10000}" />
          </label>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn-cancel" id="cfg-cancel">Cancel</button>
          <button type="submit" class="btn-confirm">Save</button>
        </div>
      </form>`;
    const cancelBtn = modalEl.querySelector('#cfg-cancel');
    if (cancelBtn) {
        cancelBtn.onclick = () => {
            const overlay = document.getElementById('modal-overlay');
            if (overlay) overlay.classList.add('hidden');
        };
    }
    const form = modalEl.querySelector('#cfg-form');
    if (form) form.addEventListener('submit', onSubmit);
}

async function onSubmit(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = {
        system_prompt: fd.get('system_prompt') || '',
        asset_whitelist: String(fd.get('asset_whitelist') || '').split(',').map(s => s.trim()).filter(Boolean),
        min_confidence_threshold: parseInt(fd.get('min_confidence_threshold'), 10),
        max_risk_tier: fd.get('max_risk_tier'),
        analysis_interval_hours: parseInt(fd.get('analysis_interval_hours'), 10),
        max_position_pct: parseFloat(fd.get('max_position_pct')),
        max_daily_loss_pct: parseFloat(fd.get('max_daily_loss_pct')),
        max_exchange_exposure_pct: parseFloat(fd.get('max_exchange_exposure_pct')),
        cooldown_seconds: parseInt(fd.get('cooldown_seconds'), 10),
        order_min_notional: parseFloat(fd.get('order_min_notional')),
        order_max_notional: parseFloat(fd.get('order_max_notional')),
    };
    try {
        await putStrategyConfig(payload);
        window.showToast?.('success', 'Config saved');
        const overlay = document.getElementById('modal-overlay');
        if (overlay) overlay.classList.add('hidden');
    } catch (err) {
        window.showToast?.('error', err.message);
    }
}

function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
