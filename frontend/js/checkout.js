/**
 * checkout.js — Razorpay Checkout integration with Customer Identification.
 *
 * Collects customer contact info, saves customer identity for cart recovery,
 * creates Razorpay orders, opens the Checkout modal, and verifies payments.
 */

const CUSTOMER_INFO_KEY = 'shopmind_customer_info';

let pendingCheckoutOrderId = null;
let pendingCheckoutTotal = null;

/**
 * Called after the agent's initiate_checkout tool succeeds and user confirms.
 */
async function handleCheckoutConfirmed(toolResult) {
  try {
    const orderId = toolResult && toolResult.order_id;
    const total = toolResult && toolResult.total;

    if (!orderId) {
      addMessage('agent', "I couldn't find your order. Let's try the checkout again.");
      return;
    }

    lastOrderId = orderId;
    pendingCheckoutOrderId = orderId;
    pendingCheckoutTotal = total;

    // Check if customer info is already known in this browser session
    const savedInfoStr = sessionStorage.getItem(CUSTOMER_INFO_KEY);
    if (savedInfoStr) {
      try {
        const savedInfo = JSON.parse(savedInfoStr);
        if (savedInfo.name && (savedInfo.email || savedInfo.phone)) {
          await proceedToRazorpay(savedInfo);
          return;
        }
      } catch (e) {}
    }

    // Otherwise prompt for customer contact details
    openCheckoutModal();
  } catch (err) {
    console.error('Checkout error:', err);
    addMessage('agent', "There was an issue preparing your payment. Please try again.");
  }
}

function openCheckoutModal() {
  const modal = document.getElementById('checkout-modal');
  const errorEl = document.getElementById('checkout-modal-error');
  if (errorEl) errorEl.textContent = '';

  const savedInfoStr = sessionStorage.getItem(CUSTOMER_INFO_KEY);
  if (savedInfoStr) {
    try {
      const savedInfo = JSON.parse(savedInfoStr);
      if (document.getElementById('cust-name')) document.getElementById('cust-name').value = savedInfo.name || '';
      if (document.getElementById('cust-email')) document.getElementById('cust-email').value = savedInfo.email || '';
      if (document.getElementById('cust-phone')) document.getElementById('cust-phone').value = savedInfo.phone || '';
    } catch (e) {}
  }

  if (modal) modal.style.display = 'flex';
}

function closeCheckoutModal() {
  const modal = document.getElementById('checkout-modal');
  if (modal) modal.style.display = 'none';
}

async function submitCustomerDetails(e) {
  if (e) e.preventDefault();
  const name = document.getElementById('cust-name').value.trim();
  const email = document.getElementById('cust-email').value.trim();
  const phone = document.getElementById('cust-phone').value.trim();
  const errorEl = document.getElementById('checkout-modal-error');
  const btn = document.getElementById('cust-submit-btn');

  if (!name || (!email && !phone)) {
    if (errorEl) errorEl.textContent = 'Please provide your name and at least an email or phone.';
    return;
  }

  const customerInfo = { name, email, phone };
  sessionStorage.setItem(CUSTOMER_INFO_KEY, JSON.stringify(customerInfo));

  closeCheckoutModal();
  await proceedToRazorpay(customerInfo);
}

/**
 * Saves customer identity to backend and launches Razorpay checkout modal.
 */
async function proceedToRazorpay(customerInfo) {
  const orderId = pendingCheckoutOrderId || lastOrderId;
  const totalAmount = pendingCheckoutTotal;

  // 1. Identify customer identity for cart recovery
  try {
    await fetch('/api/session/identify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        name: customerInfo.name,
        email: customerInfo.email,
        phone: customerInfo.phone,
      }),
    });
  } catch (err) {
    console.warn('Could not save customer identity for recovery:', err);
  }

  // 2. Create Razorpay order on backend
  try {
    const res = await fetch('/api/create-order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order_id: orderId, session_id: sessionId }),
    });

    if (!res.ok) {
      const err = await res.json();
      addMessage('agent', `Payment setup failed: ${err.detail || 'Unknown error'}. Please try again.`);
      return;
    }

    const orderData = await res.json();

    // 3. Open Razorpay Checkout with actual customer details
    const options = {
      key: orderData.key_id,
      amount: orderData.amount,
      currency: orderData.currency,
      name: 'ShopMind Electronics',
      description: 'Laptop Purchase',
      order_id: orderData.razorpay_order_id,
      handler: async function (response) {
        await verifyPayment(response, orderId);
      },
      prefill: {
        name: customerInfo.name || '',
        email: customerInfo.email || '',
        contact: customerInfo.phone || '',
      },
      theme: {
        color: '#3b82f6',
      },
      modal: {
        ondismiss: function () {
          addMessage('agent', "Payment was cancelled. Your cart is still saved — you can checkout whenever you're ready!");
        },
      },
    };

    const rzp = new Razorpay(options);

    rzp.on('payment.failed', async function (response) {
      await handlePaymentFailure(response, orderId);
    });

    rzp.open();
  } catch (err) {
    console.error('Razorpay checkout error:', err);
    addMessage('agent', "There was an issue opening the payment window. Please try again.");
  }
}

/**
 * Verify payment signature with backend after successful Razorpay Checkout.
 */
async function verifyPayment(razorpayResponse, orderId) {
  try {
    const res = await fetch('/api/verify-payment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        razorpay_order_id: razorpayResponse.razorpay_order_id,
        razorpay_payment_id: razorpayResponse.razorpay_payment_id,
        razorpay_signature: razorpayResponse.razorpay_signature,
      }),
    });

    const data = await res.json();

    if (data.status === 'success') {
      addMessage('agent',
        "🎉 **Payment successful!** Your order has been confirmed.\n\n" +
        `**Order ID:** #${orderId}\n` +
        `**Payment ID:** ${razorpayResponse.razorpay_payment_id}\n\n` +
        "Thank you for shopping with ShopMind AI! Your items will be shipped soon. 🚀"
      );
      await refreshCart();
    } else {
      addMessage('agent',
        "⚠️ **Payment verification failed.** The payment may not have gone through correctly.\n\n" +
        "Don't worry — if money was deducted, it will be refunded automatically. " +
        "Would you like to try again?"
      );
    }
  } catch (err) {
    console.error('Payment verification error:', err);
    addMessage('agent', "We couldn't verify your payment status. Please check your order in the dashboard or try again.");
  }
}

/**
 * Handle payment failure from Razorpay Checkout.
 */
async function handlePaymentFailure(response, orderId) {
  const error = response.error || {};

  try {
    await fetch('/api/payment-failed', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        order_id: orderId,
        razorpay_order_id: error.metadata?.order_id,
        error_code: error.code,
        error_description: error.description,
        error_reason: error.reason,
      }),
    });
  } catch (err) {
    console.error('Failed to log payment failure:', err);
  }

  addMessage('agent',
    "❌ **Payment didn't go through.**\n\n" +
    `**Reason:** ${error.description || 'The payment was declined or timed out.'}\n\n` +
    "Your cart is still saved. Would you like to:\n" +
    "- **Retry** the payment?\n" +
    "- Try a **different payment method**?\n\n" +
    "Just let me know and I'll help you complete the purchase!"
  );
}
