/**
 * dashboard.js — Merchant dashboard data fetching and rendering.
 *
 * Loads business metrics, AI decision trace, orders table, and
 * failure handling details from the protected dashboard API endpoints
 * using authFetch.
 */

function formatPrice(amount) {
  if (amount == null || isNaN(amount) || amount === 0) return '₹0';
  return '₹' + Number(amount).toLocaleString('en-IN');
}

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function truncate(str, max = 60) {
  if (!str) return '—';
  return str.length > max ? str.substring(0, max) + '…' : str;
}

// ── Load business metrics ──────────────────────

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

async function loadSummary() {
  try {
    const res = await authFetch('/api/dashboard/summary');
    if (!res.ok) return;
    const data = await res.json();

    const mRev = document.getElementById('m-revenue');
    const mOrders = document.getElementById('m-orders');
    const mAiPct = document.getElementById('m-ai-pct');
    const mAov = document.getElementById('m-aov');
    const mAiRev = document.getElementById('m-ai-revenue');
    const mUpsells = document.getElementById('m-upsells');
    const mUpsellRev = document.getElementById('m-upsell-rev');
    const mFailed = document.getElementById('m-failed');

    if (mRev) mRev.textContent = formatPrice(data.total_revenue || 0);
    if (mOrders) mOrders.textContent = data.total_orders || 0;
    if (mAiPct) mAiPct.textContent = (data.ai_assisted_percentage || 0) + '%';
    if (mAov) mAov.textContent = formatPrice(data.avg_order_value || 0);
    if (mAiRev) mAiRev.textContent = formatPrice(data.ai_assisted_revenue || 0);
    if (mUpsells) mUpsells.textContent = data.upsell_accepted_count || 0;
    if (mUpsellRev) mUpsellRev.textContent = formatPrice(data.upsell_revenue || 0);
    if (mFailed) mFailed.textContent = data.failed_orders_count || 0;

    const mUpsellRate = document.getElementById('m-upsell-rate');
    const mConversion = document.getElementById('m-conversion');
    if (mUpsellRate) mUpsellRate.textContent = (data.upsell_acceptance_rate || 0) + '%';
    if (mConversion) mConversion.textContent = (data.conversion_rate || 0) + '%';
  } catch (err) {
    console.error('Failed to load summary:', err);
  }
}

// ── Load sessions for selector ─────────────────

async function loadSessions() {
  try {
    const res = await authFetch('/api/dashboard/sessions');
    if (!res.ok) return;
    const data = await res.json();
    const select = document.getElementById('session-select');
    if (!select) return;

    const currentVal = select.value;
    select.innerHTML = '<option value="">All Sessions</option>';

    (data.sessions || []).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.session_id;
      const shortId = (s.session_id || '').substring(0, 8);
      opt.textContent = `${shortId}… (${s.action_count || 0} actions)`;
      select.appendChild(opt);
    });

    // Restore selected option if still present
    if (currentVal) select.value = currentVal;
  } catch (err) {
    console.error('Failed to load sessions:', err);
  }
}

// ── Load AI Decision Trace ─────────────────────

async function loadAiActions() {
  try {
    const select = document.getElementById('session-select');
    const sessionId = select ? select.value : '';
    const url = sessionId
      ? `/api/dashboard/ai-actions?session_id=${sessionId}`
      : '/api/dashboard/ai-actions';

    const res = await authFetch(url);
    if (!res.ok) return;
    const data = await res.json();
    const container = document.getElementById('ai-actions-list');
    if (!container) return;

    if (!data.actions || data.actions.length === 0) {
      container.innerHTML = '<div class="cart-empty">No AI actions recorded yet.</div>';
      return;
    }

    container.innerHTML = data.actions.map(action => {
      const statusClass = action.success === false ? 'failure' :
                           action.user_approved === null && (action.tool_name === 'add_to_cart' || action.tool_name === 'initiate_checkout') ? 'pending' :
                           'success';

      const icon = action.success === false ? '❌' :
                   action.user_approved === false ? '🚫' :
                   action.user_approved === true ? '✅' : '🔧';

      const inputSummary = action.input ? truncate(JSON.stringify(action.input), 80) : '—';
      const outputSummary = action.output ? truncate(JSON.stringify(action.output), 80) : '—';

      const approvalBadge = action.user_approved === true ? '<span class="status-badge paid">Approved</span>' :
                             action.user_approved === false ? '<span class="status-badge failed">Declined</span>' :
                             '<span class="status-badge created">Auto</span>';

      return `
        <div class="action-item ${statusClass}">
          <div class="action-icon">${icon}</div>
          <div class="action-details">
            <div class="action-tool">
              ${escapeHtml(action.tool_name || 'unknown')} ${approvalBadge}
              <span class="action-time" style="float:right;">${escapeHtml(formatDate(action.timestamp))}</span>
            </div>
            <div class="action-decision" style="font-size: 0.95rem; font-weight: 500; color: var(--text-primary, #1a1a2e); margin: 6px 0; padding: 6px 10px; background: rgba(99,102,241,0.06); border-left: 3px solid rgba(99,102,241,0.4); border-radius: 0 4px 4px 0;">
              💡 ${escapeHtml(action.decision || '—')}
            </div>
            <details style="margin-top: 4px; font-size: 0.75rem; color: var(--text-muted);">
              <summary style="cursor: pointer;">Input/Output</summary>
              <div style="margin-top: 4px;">
                <strong>Input:</strong> <code>${escapeHtml(inputSummary)}</code><br>
                <strong>Output:</strong> <code>${escapeHtml(outputSummary)}</code>
              </div>
            </details>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to load AI actions:', err);
  }
}

// ── Load Orders Table ──────────────────────────

async function loadOrders() {
  try {
    const res = await authFetch('/api/dashboard/orders');
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.getElementById('orders-tbody');
    if (!tbody) return;

    if (!data.orders || data.orders.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-muted);">No orders yet.</td></tr>';
      loadFailures([]);
      return;
    }

    tbody.innerHTML = data.orders.map(order => {
      const statusClass = order.status || 'created';
      const aiLabel = order.ai_assisted ? '🤖 Yes' : '👤 No';
      const upsellLabel = order.upsell_accepted === true ? '✅ ' + formatPrice(order.upsell_amount) :
                           order.upsell_accepted === false ? '❌ Declined' : '—';

      return `
        <tr>
          <td>#${order.id}</td>
          <td title="${escapeHtml(order.session_id || '')}">${escapeHtml((order.session_id || '').substring(0, 8))}…</td>
          <td>${formatPrice(order.total)}</td>
          <td>${aiLabel}</td>
          <td>${upsellLabel}</td>
          <td><span class="status-badge ${escapeHtml(statusClass)}">${escapeHtml((order.status || '').toUpperCase())}</span></td>
          <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(order.failure_reason || '—')}</td>
          <td>${formatDate(order.created_at)}</td>
        </tr>
      `;
    }).join('');

    // Also populate failures panel
    loadFailures(data.orders);
  } catch (err) {
    console.error('Failed to load orders:', err);
  }
}

// ── Load Failures Panel ────────────────────────

function loadFailures(orders) {
  const container = document.getElementById('failures-list');
  if (!container) return;

  const failedOrders = (orders || []).filter(o => o.status === 'failed');

  if (failedOrders.length === 0) {
    container.innerHTML = '<div class="cart-empty">No failures recorded — the system handles errors gracefully when they occur.</div>';
    return;
  }

  container.innerHTML = failedOrders.map(order => `
    <div class="failure-item">
      <div class="failure-reason">
        ⚠️ Order #${order.id} — ${escapeHtml(order.failure_reason || 'Unknown failure')}
      </div>
      <div class="failure-meta">
        Session: ${escapeHtml((order.session_id || '').substring(0, 8))}… ·
        Amount: ${formatPrice(order.total)} · 
        ${formatDate(order.created_at)}
        ${order.ai_assisted ? ' · 🤖 AI-Assisted' : ''}
      </div>
    </div>
  `).join('');
}

// ── AI vs Human Comparison ─────────────────────

async function loadComparison() {
  try {
    const res = await authFetch('/api/dashboard/comparison');
    if (!res.ok) return;
    const data = await res.json();

    // AI column
    const aiOrders = document.getElementById('cmp-ai-orders');
    const aiRevenue = document.getElementById('cmp-ai-revenue');
    const aiAov = document.getElementById('cmp-ai-aov');
    const aiCrossSell = document.getElementById('cmp-ai-cross-sell');
    if (aiOrders) aiOrders.textContent = data.ai?.orders || 0;
    if (aiRevenue) aiRevenue.textContent = formatPrice(data.ai?.revenue || 0);
    if (aiAov) aiAov.textContent = formatPrice(data.ai?.aov || 0);
    if (aiCrossSell) aiCrossSell.textContent = (data.ai?.cross_sell_rate || 0) + '%';

    // Organic column
    const orgOrders = document.getElementById('cmp-org-orders');
    const orgRevenue = document.getElementById('cmp-org-revenue');
    const orgAov = document.getElementById('cmp-org-aov');
    const orgCrossSell = document.getElementById('cmp-org-cross-sell');
    if (orgOrders) orgOrders.textContent = data.organic?.orders || 0;
    if (orgRevenue) orgRevenue.textContent = formatPrice(data.organic?.revenue || 0);
    if (orgAov) orgAov.textContent = formatPrice(data.organic?.aov || 0);
    if (orgCrossSell) orgCrossSell.textContent = (data.organic?.cross_sell_rate || 0) + '%';
  } catch (err) {
    console.error('Failed to load comparison:', err);
  }
}

// ── Global Dashboard Initializer ──────────────

let _refreshInterval = null;

function initDashboard() {
  loadSummary();
  loadSessions();
  loadAiActions();
  loadOrders();
  loadCampaignHistory();
  loadComparison();

  if (_refreshInterval) clearInterval(_refreshInterval);
  _refreshInterval = setInterval(() => {
    // Only refresh if dashboard is currently visible
    const token = sessionStorage.getItem('admin_token');
    if (token) {
      loadSummary();
      loadAiActions();
      loadOrders();
      loadCampaignHistory();
      loadComparison();
    }
  }, 30000);
}

// ── Campaign Orchestrator ─────────────────────

async function loadCampaignHistory() {
  try {
    const res = await authFetch('/api/campaigns/history');
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.getElementById('campaign-tbody');

    // Update stats
    if (data.stats) {
      document.getElementById('campaign-nudges').textContent = data.stats.nudges_sent || 0;
      document.getElementById('campaign-skipped').textContent = data.stats.carts_skipped || 0;
    }

    if (!data.actions || data.actions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">No campaign actions yet. Click "Run Campaign Scan Now" to start.</td></tr>';
      return;
    }

    tbody.innerHTML = data.actions.map(action => {
      const actionBadge = action.action_taken === 'reminder'
        ? '<span class="status-badge paid">📧 Reminder</span>'
        : action.action_taken === 'discount_offer'
          ? '<span class="status-badge" style="background: rgba(245,158,11,0.12); color: #b45309;">🏷️ Discount</span>'
          : '<span class="status-badge created">⏸️ No Action</span>';

      const discount = action.discount_percent
        ? action.discount_percent + '%'
        : '—';

      const channel = action.simulated_channel
        ? (action.simulated_channel === 'email' ? '📧 Email' : '📱 SMS')
        : '—';

      const customerEmail = action.customer_email || '—';

      const decisionText = action.decision || '—';
      const shortDecision = decisionText.length > 120
        ? decisionText.substring(0, 120) + '…'
        : decisionText;

      return `
        <tr>
          <td title="${escapeHtml(action.session_id)}">${escapeHtml((action.session_id || '').substring(0, 8))}…</td>
          <td>${escapeHtml(customerEmail)}</td>
          <td>${formatPrice(action.cart_value)}</td>
          <td>${action.cart_age_minutes || 0} min</td>
          <td>${actionBadge}</td>
          <td>${discount}</td>
          <td>${channel}</td>
          <td style="max-width: 300px; font-size: 0.82rem; line-height: 1.4;">
            <details>
              <summary style="cursor: pointer; color: var(--primary); font-weight: 500;">💡 View Reasoning</summary>
              <div style="margin-top: 6px; padding: 8px 10px; background: rgba(99,102,241,0.06); border-left: 3px solid rgba(99,102,241,0.4); border-radius: 0 4px 4px 0; white-space: pre-wrap;">
                ${escapeHtml(decisionText)}
              </div>
            </details>
          </td>
          <td>${formatDate(action.created_at)}</td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to load campaign history:', err);
  }
}

async function runCampaignScan() {
  const btn = document.getElementById('campaign-run-btn');
  const statusEl = document.getElementById('campaign-status');

  // Disable button during scan
  btn.disabled = true;
  btn.textContent = '⏳ Scanning…';
  btn.style.opacity = '0.6';

  statusEl.style.display = 'block';
  statusEl.style.background = 'rgba(59,130,246,0.08)';
  statusEl.style.color = 'var(--primary)';
  statusEl.textContent = '🔍 Running campaign scan — the AI is evaluating abandoned carts…';

  try {
    const res = await authFetch('/api/campaigns/run', { method: 'POST' });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error (${res.status})`);
    }

    const data = await res.json();

    // Show success status
    statusEl.style.background = 'rgba(34,197,94,0.08)';
    statusEl.style.color = 'var(--success)';
    statusEl.textContent = `✅ ${data.message || 'Scan complete.'} — ${data.nudges_sent || 0} nudge(s) sent, ${data.carts_skipped || 0} skipped.`;

    // Refresh the history table
    await loadCampaignHistory();

  } catch (err) {
    statusEl.style.background = 'rgba(239,68,68,0.08)';
    statusEl.style.color = 'var(--danger)';
    statusEl.textContent = `❌ Scan failed: ${err.message}`;
    console.error('Campaign scan failed:', err);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Run Campaign Scan Now';
    btn.style.opacity = '1';

    // Auto-hide status after 10 seconds
    setTimeout(() => {
      statusEl.style.display = 'none';
    }, 10000);
  }
}
