/**
 * chat.js — ShopMind AI chat interface logic.
 */


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


let isWaiting = false;
let pendingAction = null;  // stores the current pending confirmation
let lastOrderId = null;    // stores the last order_id for checkout flow


const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');

function scrollToBottom() {
  if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
}

function formatPrice(amount) {
  if (amount == null || isNaN(amount)) return '₹0';
  return '₹' + Number(amount).toLocaleString('en-IN');
}

function escapeHtml(value) {
  if (value == null) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/**
 * Rich markdown-like formatting: bold, inline code, bullets (*, -, •), numbered lists, newlines.
 */
function formatMessage(text) {
  if (!text) return '';

  let formatted = escapeHtml(text);

  // Bold: **text**
  formatted = formatted.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  // Inline code: `code`
  formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Numbered list items: "1. item", "2. item"
  formatted = formatted.replace(/^[ \t]*(\d+)\.\s+(.+)$/gm, '<li class="num-item" value="$1">$2</li>');

  // Bullet list items: "* item", "- item", "• item"
  formatted = formatted.replace(/^[ \t]*[\*\-•]\s+(.+)$/gm, '<li class="bullet-item">$1</li>');

  // Group numbered list items into <ol>
  formatted = formatted.replace(/((?:<li class="num-item"[^>]*>.*?<\/li>\s*)+)/gs, function(match) {
    return '<ol>' + match.replace(/\s*(<li class="num-item"[^>]*>.*?<\/li>)\s*/gs, '$1') + '</ol>';
  });

  // Group bullet list items into <ul>
  formatted = formatted.replace(/((?:<li class="bullet-item">.*?<\/li>\s*)+)/gs, function(match) {
    return '<ul>' + match.replace(/\s*(<li class="bullet-item">.*?<\/li>)\s*/gs, '$1') + '</ul>';
  });

  // Convert newlines to <br>, avoiding awkward extra breaks directly adjoining lists
  formatted = formatted
    .replace(/(<\/ul>|<\/ol>)\n+/g, '$1')
    .replace(/\n+(<ul>|<ol>)/g, '$1')
    .replace(/\n/g, '<br>')
    .replace(/(<br>){3,}/g, '<br><br>');

  return formatted;
}



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
    // Remove any previously existing confirmation buttons first
    document.querySelectorAll('.confirmation-actions').forEach(el => el.remove());

    const actions = document.createElement('div');
    actions.className = 'confirmation-actions';

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
  if (messagesEl) messagesEl.appendChild(msg);
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



async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || isWaiting) return;

  // Remove any stale confirmation buttons from previous turns
  document.querySelectorAll('.confirmation-actions').forEach(el => el.remove());

  addMessage('user', text);
  inputEl.value = '';
  isWaiting = true;
  sendBtn.disabled = true;
  inputEl.disabled = true;
  addTypingIndicator();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });

    removeTypingIndicator();
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      addMessage('agent', '⚠️ Something went wrong connecting to the server. Please try again in a moment.');
      return;
    }

    if (data.type === 'error') {
      addMessage('agent', data.content || 'I had trouble processing that request. Could you please try again?');
      return;
    }

    if (data.type === 'pending_confirmation') {
      pendingAction = data.pending_action;
      addMessage('agent', data.content, data);
    } else {
      pendingAction = null;
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
    inputEl.disabled = false;
    inputEl.focus();
  }
}

async function handleConfirm(actionId) {
  // Remove all active confirmation buttons immediately to prevent double-clicks
  document.querySelectorAll('.confirmation-actions').forEach(el => el.remove());

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
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      addMessage('agent', '⚠️ Something went wrong processing your confirmation. Please try again.');
      pendingAction = null;
      return;
    }

    if (data.type === 'pending_confirmation') {
      pendingAction = data.pending_action;
      addMessage('agent', data.content, data);
    } else {
      addMessage('agent', data.content);

      if ((pendingAction && pendingAction.tool_name === 'initiate_checkout') || (data.tool_result && data.tool_result.order_id)) {
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
  // Remove confirmation buttons immediately
  document.querySelectorAll('.confirmation-actions').forEach(el => el.remove());

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
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      addMessage('agent', 'Action cancelled.');
    } else {
      addMessage('agent', data.content || 'Action cancelled.');
    }
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



async function refreshCart() {
  try {
    const res = await fetch(`/api/cart?session_id=${sessionId}`);
    if (!res.ok) {
      console.warn('Cart refresh failed with status:', res.status);
      return;
    }
    const data = await res.json();

    const cartItemsEl = document.getElementById('cart-items');
    const cartCountEl = document.getElementById('cart-count');
    const cartTotalEl = document.getElementById('cart-total');
    const checkoutBtn = document.getElementById('checkout-btn');
    const clearCartBtn = document.getElementById('clear-cart-btn');

    if (!cartItemsEl || !cartCountEl || !cartTotalEl) return;

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
            <div class="cart-item-name">${escapeHtml(item.product_name)}</div>
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
    } else {
      console.warn('Could not remove cart item:', await res.text());
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
      addMessage('agent', 'Your cart has been cleared.');
    }
  } catch (err) {
    console.error('Error clearing cart:', err);
  }
}

function requestCheckout() {
  inputEl.value = "I'd like to checkout and pay now.";
  sendMessage();
}



function showEmailModal() {
  const modal = document.getElementById('email-modal');
  const errorEl = document.getElementById('email-modal-error');
  if (errorEl) errorEl.textContent = '';
  if (document.getElementById('email-input')) document.getElementById('email-input').value = '';
  if (modal) modal.style.display = 'flex';
  if (inputEl) inputEl.disabled = true;
  if (sendBtn) sendBtn.disabled = true;
}

function hideEmailModal() {
  const modal = document.getElementById('email-modal');
  if (modal) modal.style.display = 'none';
  if (inputEl) inputEl.disabled = false;
  if (sendBtn) sendBtn.disabled = false;
}

async function submitEmail(e) {
  if (e) e.preventDefault();
  const emailInput = document.getElementById('email-input');
  const errorEl = document.getElementById('email-modal-error');
  const btn = document.getElementById('email-submit-btn');
  const email = emailInput ? emailInput.value.trim() : '';

  if (!email || !email.includes('@')) {
    if (errorEl) {
      errorEl.style.color = 'var(--danger)';
      errorEl.textContent = 'Please enter a valid email address.';
    }
    return;
  }

  if (btn) btn.disabled = true;
  if (errorEl) {
    errorEl.style.color = 'var(--text-muted)';
    errorEl.textContent = 'Loading...';
  }

  await startSessionWithEmail(email, false);
  
  if (btn) btn.disabled = false;
}

async function startSessionWithEmail(email, isReturning) {
  try {
    const res = await fetch('/api/session/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      const errorEl = document.getElementById('email-modal-error');
      if (errorEl) {
        errorEl.style.color = 'var(--danger)';
        errorEl.textContent = data.detail || 'Something went wrong.';
      }
      showEmailModal();
      return;
    }

    setSessionId(data.session_id);
    localStorage.setItem('shopmind_user_email', email);
    
    const displayEl = document.getElementById('user-email-display');
    if (displayEl) displayEl.textContent = email;
    
    const userInfoEl = document.getElementById('user-info');
    if (userInfoEl) userInfoEl.style.display = 'flex';
    
    hideEmailModal();
    await refreshCart();

    if (isReturning && !data.is_new) {
      addMessage('agent', `🎉 Welcome back! I've restored your session and cart. How can I help you today?`);
    } else {
      addMessage('agent', "Hi! I'm **ShopMind AI** 🧠 — your personal laptop shopping assistant.\n\nI can help you find the perfect laptop based on your budget, use case, and preferences. I can also compare models, suggest accessories, and handle checkout.\n\n**What are you looking for today?** Tell me your budget, what you'll use it for (coding, gaming, ML, general use), or any specific requirements!");
    }
    
  } catch (err) {
    console.error('Start session error:', err);
    const errorEl = document.getElementById('email-modal-error');
    if (errorEl) {
      errorEl.style.color = 'var(--danger)';
      errorEl.textContent = 'Connection error. Please try again.';
    }
    showEmailModal();
  }
}

function switchAccount() {
  localStorage.removeItem('shopmind_user_email');
  localStorage.removeItem('shopmind_session_id');
  sessionStorage.removeItem('shopmind_customer_info');
  window.location.reload();
}


if (inputEl) {
  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
}

// Close modals when Escape key is pressed
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (typeof closeCheckoutModal === 'function') closeCheckoutModal();
  }
});


document.addEventListener('DOMContentLoaded', async () => {
  const savedEmail = localStorage.getItem('shopmind_user_email');
  
  if (savedEmail) {
    // Returning user — restore session from email
    await startSessionWithEmail(savedEmail, true);
  } else {
    // New user — show email modal
    showEmailModal();
  }
  
  if (inputEl) inputEl.focus();
});
