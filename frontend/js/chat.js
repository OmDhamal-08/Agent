/**
 * chat.js — ShopMind AI chat interface logic.
 *
 * Manages conversation state, sends messages to the backend agent,
 * handles confirmation/cancel flows, customer cart recovery, and cart management.
 */

// ── Session management ─────────────────────────
const SESSION_KEY = 'shopmind_session_id';

function getSessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

let sessionId = getSessionId();

function setSessionId(newId) {
  sessionId = newId;
  localStorage.setItem(SESSION_KEY, newId);
}

// ── State ──────────────────────────────────────
let isWaiting = false;
let pendingAction = null;  // stores the current pending confirmation
let lastOrderId = null;    // stores the last order_id for checkout flow

// ── DOM helpers ────────────────────────────────
const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function formatPrice(amount) {
  return '₹' + Number(amount).toLocaleString('en-IN');
}

/**
 * Simple markdown-like formatting: **bold**, bullet lists, newlines.
 */
function formatMessage(text) {
  if (!text) return '';
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^[-•]\s+(.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
    .replace(/\n/g, '<br>');
}

// ── Message rendering ──────────────────────────

function addMessage(role, content, extra) {
  const msg = document.createElement('div');
  msg.className = `message ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = role === 'agent' ? '🧠' : '👤';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  bubble.innerHTML = formatMessage(content);

  // If this is a confirmation request, add buttons
  if (extra && extra.type === 'pending_confirmation' && extra.pending_action) {
    const actions = document.createElement('div');
    actions.className = 'confirmation-actions';
    actions.id = 'confirm-actions';

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn-confirm';
    confirmBtn.textContent = '✓ Confirm';
    confirmBtn.onclick = () => handleConfirm(extra.pending_action.action_id);

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn-cancel';
    cancelBtn.textContent = '✕ Cancel';
    cancelBtn.onclick = () => handleCancel(extra.pending_action.action_id);

    actions.appendChild(confirmBtn);
    actions.appendChild(cancelBtn);
    bubble.appendChild(actions);
  }

  msg.appendChild(avatar);
  msg.appendChild(bubble);
  messagesEl.appendChild(msg);
  scrollToBottom();
  return msg;
}

function addTypingIndicator() {
  const msg = document.createElement('div');
  msg.className = 'message agent';
  msg.id = 'typing-indicator';

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = '🧠';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  bubble.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';

  msg.appendChild(avatar);
  msg.appendChild(bubble);
  messagesEl.appendChild(msg);
  scrollToBottom();
}

function removeTypingIndicator() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

// ── API calls ──────────────────────────────────

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || isWaiting) return;

  addMessage('user', text);
  inputEl.value = '';
  isWaiting = true;
  sendBtn.disabled = true;
  addTypingIndicator();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });

    removeTypingIndicator();
    const data = await res.json();

    if (data.type === 'pending_confirmation') {
      pendingAction = data.pending_action;
      addMessage('agent', data.content, data);
    } else {
      addMessage('agent', data.content);
    }

    await refreshCart();
  } catch (err) {
    removeTypingIndicator();
    addMessage('agent', 'Sorry, something went wrong connecting to the server. Please try again.');
    console.error('Chat error:', err);
  } finally {
    isWaiting = false;
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

async function handleConfirm(actionId) {
  const actionsEl = document.getElementById('confirm-actions');
  if (actionsEl) actionsEl.remove();

  isWaiting = true;
  sendBtn.disabled = true;
  addTypingIndicator();

  try {
    const res = await fetch('/api/chat/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, action_id: actionId }),
    });

    removeTypingIndicator();
    const data = await res.json();

    if (data.type === 'pending_confirmation') {
      pendingAction = data.pending_action;
      addMessage('agent', data.content, data);
    } else {
      addMessage('agent', data.content);

      if (pendingAction && pendingAction.tool_name === 'initiate_checkout') {
        await handleCheckoutConfirmed(data.tool_result || data);
      }
      pendingAction = null;
    }

    await refreshCart();
  } catch (err) {
    removeTypingIndicator();
    addMessage('agent', 'Something went wrong processing your confirmation. Please try again.');
    console.error('Confirm error:', err);
  } finally {
    isWaiting = false;
    sendBtn.disabled = false;
  }
}

async function handleCancel(actionId) {
  const actionsEl = document.getElementById('confirm-actions');
  if (actionsEl) actionsEl.remove();

  isWaiting = true;
  sendBtn.disabled = true;
  addTypingIndicator();

  try {
    const res = await fetch('/api/chat/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, action_id: actionId }),
    });

    removeTypingIndicator();
    const data = await res.json();
    addMessage('agent', data.content);
    pendingAction = null;
  } catch (err) {
    removeTypingIndicator();
    addMessage('agent', 'No problem, the action has been cancelled.');
    console.error('Cancel error:', err);
  } finally {
    isWaiting = false;
    sendBtn.disabled = false;
  }
}

// ── Cart management & direct actions ───────────

async function refreshCart() {
  try {
    const res = await fetch(`/api/cart?session_id=${sessionId}`);
    const data = await res.json();

    const cartItemsEl = document.getElementById('cart-items');
    const cartCountEl = document.getElementById('cart-count');
    const cartTotalEl = document.getElementById('cart-total');
    const checkoutBtn = document.getElementById('checkout-btn');
    const clearCartBtn = document.getElementById('clear-cart-btn');

    if (!data.items || data.items.length === 0) {
      cartItemsEl.innerHTML = '<div class="cart-empty">Your cart is empty.<br>Ask the AI to recommend laptops!</div>';
      cartCountEl.textContent = '(0 items)';
      cartTotalEl.textContent = '₹0';
      if (checkoutBtn) checkoutBtn.disabled = true;
      if (clearCartBtn) clearCartBtn.style.display = 'none';
      return;
    }

    cartCountEl.textContent = `(${data.item_count} item${data.item_count > 1 ? 's' : ''})`;
    cartTotalEl.textContent = formatPrice(data.total);
    if (checkoutBtn) checkoutBtn.disabled = false;
    if (clearCartBtn) clearCartBtn.style.display = 'block';

    cartItemsEl.innerHTML = data.items.map(item => {
      let sourceClass = 'organic';
      let sourceLabel = 'Added by you';
      if (item.source === 'ai_recommendation') { sourceClass = 'ai'; sourceLabel = 'AI Recommended'; }
      if (item.source === 'ai_upsell') { sourceClass = 'upsell'; sourceLabel = 'AI Upsell'; }

      return `
        <div class="cart-item">
          <div style="flex: 1; min-width: 0;">
            <div class="cart-item-name">${item.product_name}</div>
            <div class="cart-item-meta">
              Qty: ${item.quantity} · <span class="source-badge ${sourceClass}">${sourceLabel}</span>
            </div>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <div class="cart-item-price">${formatPrice(item.subtotal)}</div>
            <button class="cart-remove-btn" onclick="removeCartItem(${item.product_id})" title="Remove item">✕</button>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Cart refresh error:', err);
  }
}

async function removeCartItem(productId) {
  try {
    const res = await fetch('/api/cart/item', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, product_id: productId }),
    });

    if (res.ok) {
      await refreshCart();
    }
  } catch (err) {
    console.error('Error removing item from cart:', err);
  }
}

async function clearCart() {
  if (!confirm('Remove all items from your cart?')) {
    return;
  }

  try {
    const res = await fetch('/api/cart', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });

    if (res.ok) {
      await refreshCart();
    }
  } catch (err) {
    console.error('Error clearing cart:', err);
  }
}

function requestCheckout() {
  inputEl.value = "I'd like to checkout and pay now.";
  sendMessage();
}

// ── Cart Recovery Modals ───────────────────────

function openRecoverModal() {
  const modal = document.getElementById('recover-modal');
  const errorEl = document.getElementById('recover-modal-error');
  if (errorEl) errorEl.textContent = '';
  if (document.getElementById('recover-contact')) document.getElementById('recover-contact').value = '';
  if (modal) modal.style.display = 'flex';
}

function closeRecoverModal() {
  const modal = document.getElementById('recover-modal');
  if (modal) modal.style.display = 'none';
}

async function submitCartRecovery(e) {
  if (e) e.preventDefault();
  const contact = document.getElementById('recover-contact').value.trim();
  const errorEl = document.getElementById('recover-modal-error');
  const btn = document.getElementById('recover-submit-btn');

  if (!contact) {
    if (errorEl) errorEl.textContent = 'Please enter your email or phone number.';
    return;
  }

  if (btn) btn.disabled = true;
  if (errorEl) {
    errorEl.style.color = 'var(--text-muted)';
    errorEl.textContent = 'Looking for your saved cart...';
  }

  const isEmail = contact.includes('@');
  const payload = isEmail ? { email: contact } : { phone: contact };

  try {
    const res = await fetch('/api/session/recover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();

    if (!res.ok) {
      if (errorEl) {
        errorEl.style.color = 'var(--danger)';
        errorEl.textContent = data.detail || 'No saved cart found for that contact.';
      }
      return;
    }

    // Recovered! Update active session_id
    setSessionId(data.session_id);
    closeRecoverModal();
    await refreshCart();

    addMessage('agent', `🎉 Welcome back${data.name ? ' **' + data.name + '**' : ''}! I have restored your saved shopping cart from your previous session.`);
  } catch (err) {
    if (errorEl) {
      errorEl.style.color = 'var(--danger)';
      errorEl.textContent = 'Connection error. Please try again.';
    }
    console.error('Recovery error:', err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Keyboard shortcut ──────────────────────────
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// ── Init ───────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  addMessage('agent', "Hi! I'm **ShopMind AI** 🧠 — your personal laptop shopping assistant.\n\nI can help you find the perfect laptop based on your budget, use case, and preferences. I can also compare models, suggest accessories, and handle checkout.\n\n**What are you looking for today?** Tell me your budget, what you'll use it for (coding, gaming, ML, general use), or any specific requirements!");
  refreshCart();
  inputEl.focus();
});
