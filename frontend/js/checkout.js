/**
 * checkout.js — Razorpay Checkout integration for ShopMind AI.
 *
 * Handles creating Razorpay orders, opening the Checkout modal,
 * verifying payments, and reporting failures.
 */

/**
 * Called after the agent's initiate_checkout tool succeeds and user confirms.
 * Extracts the order_id from the agent response and triggers Razorpay Checkout.
 */
async function handleCheckoutConfirmed(agentResponse) {
  // The agent response content should mention the order details
  // We need to get the latest order for this session
  try {
    const res = await fetch(`/api/cart?session_id=${sessionId}`);
    const cart = await res.json();

    // Find the latest order by querying orders
    const ordersRes = await fetch('/api/dashboard/orders');
    const ordersData = await ordersRes.json();

    // Find the most recent 'created' order for our session
    const myOrder = ordersData.orders.find(o =>
      o.session_id === sessionId && o.status === 'created'
    );

    if (!myOrder) {
      addMessage('agent', "I couldn't find your order. Let's try the checkout again.");
      return;
    }

    lastOrderId = myOrder.id;
    await openRazorpayCheckout(myOrder.id, myOrder.total);
  } catch (err) {
    console.error('Checkout error:', err);
    addMessage('agent', "There was an issue preparing your payment. Please try again.");
  }
}

/**
 * Creates a Razorpay order and opens the Checkout modal.
 */
async function openRazorpayCheckout(orderId, totalAmount) {
  try {
    // 1. Create Razorpay order on backend
    const res = await fetch('/api/create-order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order_id: orderId }),
    });

    if (!res.ok) {
      const err = await res.json();
      addMessage('agent', `Payment setup failed: ${err.detail || 'Unknown error'}. Please try again.`);
      return;
    }

    const orderData = await res.json();

    // 2. Open Razorpay Checkout modal
    const options = {
      key: orderData.key_id,
      amount: orderData.amount,
      currency: orderData.currency,
      name: 'ShopMind Electronics',
      description: 'Laptop Purchase',
      order_id: orderData.razorpay_order_id,
      handler: async function (response) {
        // 3. Payment success callback — verify signature
        await verifyPayment(response, orderId);
      },
      prefill: {
        name: 'Test Customer',
        email: 'test@shopmind.ai',
        contact: '9876543210',
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

    // 4. Payment failure handler
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
      // Clear cart display
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
 * This is the GRACEFUL FAILURE HANDLING required by Section 6.
 */
async function handlePaymentFailure(response, orderId) {
  const error = response.error || {};

  // Log the failure to backend
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

  // Show a clear, human-friendly message — NOT a raw error
  addMessage('agent',
    "❌ **Payment didn't go through.**\n\n" +
    `**Reason:** ${error.description || 'The payment was declined or timed out.'}\n\n` +
    "Your cart is still saved. Would you like to:\n" +
    "- **Retry** the payment?\n" +
    "- Try a **different payment method**?\n\n" +
    "Just let me know and I'll help you complete the purchase!"
  );
}
