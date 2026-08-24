"""
checkout.py — Razorpay checkout routes for ShopMind AI.

Provides endpoints to create Razorpay orders, verify payment signatures
after checkout, and record payment failures.  All mutations are persisted
to the ``orders`` table and, where appropriate, logged to ``ai_actions``
for the admin audit trail.
"""

import logging
import os
import json
from typing import Any

import razorpay
from fastapi import APIRouter, Depends, HTTPException
import asyncpg

from backend.database import get_db
from backend.models import PaymentVerifyRequest, PaymentFailedRequest
from backend.logging_middleware import log_tool_call

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["checkout"])

# ---------------------------------------------------------------------------
# Lazy-initialised Razorpay client
# ---------------------------------------------------------------------------

_razorpay_client: razorpay.Client | None = None


def get_razorpay_client() -> razorpay.Client:
    """Return the singleton Razorpay client, creating it on first call.

    Reads ``RAZORPAY_KEY_ID`` and ``RAZORPAY_KEY_SECRET`` from the
    environment.  Raises ``RuntimeError`` if either is missing.
    """
    global _razorpay_client
    if _razorpay_client is None:
        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise RuntimeError("Razorpay keys not configured")
        _razorpay_client = razorpay.Client(auth=(key_id, key_secret))
    return _razorpay_client


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/create-order")
async def create_order(
    payload: dict[str, Any],
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Create a Razorpay order for an existing internal order.

    Request body:
        ``{ "order_id": <int>, "session_id": <str> }``

    Workflow:
        1. Fetch the order row from the ``orders`` table.
        2. Validate that the order belongs to the requesting session.
        3. Convert the total to paise (× 100).
        4. Call the Razorpay Orders API.
        5. Persist the ``razorpay_order_id`` back to the ``orders`` row.
        6. Return order details the frontend needs to open Razorpay Checkout.

    Returns:
        A dict with ``razorpay_order_id``, ``amount`` (paise),
        ``currency``, and ``key_id``.

    Raises:
        HTTPException 400: If ``order_id`` or ``session_id`` is missing.
        HTTPException 403: If the order does not belong to the session.
        HTTPException 404: If the order does not exist.
        HTTPException 500: If the Razorpay API call fails.
    """
    order_id: int | None = payload.get("order_id")
    session_id: str | None = payload.get("session_id")
    if order_id is None:
        raise HTTPException(status_code=400, detail="order_id is required")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    # 1. Look up the internal order
    row = await conn.fetchrow(
        "SELECT id, total, status, session_id FROM orders WHERE id = $1",
        order_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # 2. Validate session ownership
    if row["session_id"] != session_id:
        raise HTTPException(
            status_code=403,
            detail="Order does not belong to this session",
        )

    # 3. Amount in paise
    amount_paise: int = int(row["total"] * 100)

    # 4. Create the Razorpay order
    try:
        client = get_razorpay_client()
        rz_order = client.order.create(
            data={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": f"order_{order_id}",
            }
        )
    except Exception as exc:
        logger.error("Razorpay order creation failed for order %s: %s", order_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Razorpay order: {exc}",
        )

    razorpay_order_id: str = rz_order["id"]

    # 5. Persist the razorpay_order_id
    await conn.execute(
        "UPDATE orders SET razorpay_order_id = $1 WHERE id = $2",
        razorpay_order_id,
        order_id,
    )

    logger.info(
        "Created Razorpay order %s for internal order %s (₹%s)",
        razorpay_order_id,
        order_id,
        row["total"],
    )

    # 6. Return details for the frontend
    return {
        "razorpay_order_id": razorpay_order_id,
        "amount": amount_paise,
        "currency": "INR",
        "key_id": os.getenv("RAZORPAY_KEY_ID"),
    }


@router.post("/verify-payment")
async def verify_payment(
    payload: PaymentVerifyRequest,
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, str | int]:
    """Verify the Razorpay payment signature after checkout.

    Razorpay Checkout returns ``razorpay_order_id``,
    ``razorpay_payment_id``, and ``razorpay_signature``.  This endpoint
    verifies the HMAC-SHA256 signature to confirm the payment is genuine.

    On success the order status is set to ``'paid'`` and the payment ID
    is stored.  On signature mismatch the order is marked ``'failed'``.

    Returns:
        ``{ "status": "success" | "failed", "order_id": <int> }``
    """
    # Locate the internal order
    row = await conn.fetchrow(
        "SELECT id FROM orders WHERE razorpay_order_id = $1",
        payload.razorpay_order_id,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found for the given Razorpay order ID",
        )
    order_id: int = row["id"]

    # Attempt signature verification
    try:
        client = get_razorpay_client()
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": payload.razorpay_order_id,
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature": payload.razorpay_signature,
            }
        )
    except razorpay.errors.SignatureVerificationError:
        logger.warning(
            "Signature verification failed for order %s (Razorpay order %s)",
            order_id,
            payload.razorpay_order_id,
        )
        await conn.execute(
            """
            UPDATE orders
            SET status         = 'failed',
                failure_reason = 'signature_verification_failed'
            WHERE id = $1
            """,
            order_id,
        )
        return {"status": "failed", "order_id": order_id}

    # Signature valid — mark order as paid
    # Look up the order's session_id so we can find cart items
    order_row = await conn.fetchrow(
        "SELECT id, session_id FROM orders WHERE razorpay_order_id = $1",
        payload.razorpay_order_id,
    )
    order_session_id = order_row["session_id"] if order_row else None

    # Determine actual_product_purchased_id from cart items.
    # Prefer ai_recommendation-sourced items; fall back to the first item.
    actual_product_id = None
    cart_rows = []
    if order_session_id:
        cart_rows = await conn.fetch(
            """
            SELECT ci.product_id, ci.quantity, ci.source
            FROM cart_items ci
            WHERE ci.session_id = $1
            ORDER BY
                CASE WHEN ci.source = 'ai_recommendation' THEN 0 ELSE 1 END,
                ci.created_at ASC
            """,
            order_session_id,
        )
        if cart_rows:
            actual_product_id = cart_rows[0]["product_id"]

    # ── Stock decrement (transactional) ─────────────────────────
    # Decrement stock for each purchased item, guarded by stock >= quantity
    # to prevent negative stock under concurrent requests.
    if order_session_id and cart_rows:
        for cart_row in cart_rows:
            pid = cart_row["product_id"]
            qty = cart_row["quantity"]
            rows_affected = await conn.execute(
                """
                UPDATE products
                SET stock = stock - $1
                WHERE id = $2 AND stock >= $1
                """,
                qty,
                pid,
            )
            # rows_affected is e.g. "UPDATE 1" or "UPDATE 0"
            if rows_affected == "UPDATE 0":
                # Insufficient stock at payment time — fail the order gracefully
                logger.warning(
                    "Insufficient stock for product %s (qty %s) at payment "
                    "verification for order %s",
                    pid, qty, order_id,
                )
                await conn.execute(
                    """
                    UPDATE orders
                    SET status         = 'failed',
                        failure_reason = $1
                    WHERE id = $2
                    """,
                    f"insufficient_stock_at_payment: product_id={pid}",
                    order_id,
                )

                # Log the stock failure to ai_actions
                if order_session_id:
                    await log_tool_call(
                        conn=conn,
                        session_id=order_session_id,
                        tool_name="stock_check",
                        tool_input={"product_id": pid, "requested_quantity": qty},
                        tool_output={"error": "insufficient_stock_at_payment"},
                        decision=(
                            f"Payment verified but product {pid} has insufficient "
                            f"stock ({qty} requested). Order {order_id} marked as failed."
                        ),
                        user_approved=None,
                        success=False,
                    )

                return {"status": "failed", "order_id": order_id}

    await conn.execute(
        """
        UPDATE orders
        SET status                      = 'paid',
            razorpay_payment_id         = $1,
            actual_product_purchased_id = $2
        WHERE id = $3
        """,
        payload.razorpay_payment_id,
        actual_product_id,
        order_id,
    )

    # Log successful payment to ai_actions for audit completeness
    if order_session_id:
        await log_tool_call(
            conn=conn,
            session_id=order_session_id,
            tool_name="razorpay_checkout",
            tool_input={
                "order_id": order_id,
                "razorpay_order_id": payload.razorpay_order_id,
            },
            tool_output={
                "status": "payment_verified",
                "razorpay_payment_id": payload.razorpay_payment_id,
            },
            decision=f"Payment verified successfully for order {order_id}.",
            user_approved=None,
            success=True,
        )

    logger.info(
        "Payment verified for order %s (payment %s)",
        order_id,
        payload.razorpay_payment_id,
    )
    return {"status": "success", "order_id": order_id}


@router.post("/payment-failed")
async def payment_failed(
    payload: PaymentFailedRequest,
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, str | int]:
    """Record a payment failure reported by Razorpay Checkout.

    Called by the frontend when the Razorpay Checkout widget fires its
    ``payment.failed`` handler.  Updates the order status and logs the
    failure to the ``ai_actions`` audit trail.

    Returns:
        ``{ "status": "logged", "order_id": <int> }``
    """
    # Build a human-readable failure reason
    failure_reason = " | ".join(
        filter(
            None,
            [
                payload.error_code,
                payload.error_description,
                payload.error_reason,
            ],
        )
    ) or "unknown_error"

    # Update order status
    await conn.execute(
        """
        UPDATE orders
        SET status         = 'failed',
            failure_reason = $1
        WHERE id = $2
        """,
        failure_reason,
        payload.order_id,
    )

    # Log to ai_actions audit trail
    await log_tool_call(
        conn=conn,
        session_id=payload.session_id,
        tool_name="razorpay_checkout",
        tool_input={
            "order_id": payload.order_id,
            "razorpay_order_id": payload.razorpay_order_id,
        },
        tool_output={
            "error_code": payload.error_code,
            "error_description": payload.error_description,
            "error_reason": payload.error_reason,
        },
        decision=f"Payment failed for order {payload.order_id}: {failure_reason}",
        user_approved=None,
        success=False,
    )

    logger.warning(
        "Payment failed for order %s: %s",
        payload.order_id,
        failure_reason,
    )
    return {"status": "logged", "order_id": payload.order_id}
