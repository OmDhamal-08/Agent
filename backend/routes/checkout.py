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
        ``{ "order_id": <int> }``

    Workflow:
        1. Fetch the order row from the ``orders`` table.
        2. Convert the total to paise (× 100).
        3. Call the Razorpay Orders API.
        4. Persist the ``razorpay_order_id`` back to the ``orders`` row.
        5. Return order details the frontend needs to open Razorpay Checkout.

    Returns:
        A dict with ``razorpay_order_id``, ``amount`` (paise),
        ``currency``, and ``key_id``.

    Raises:
        HTTPException 400: If ``order_id`` is missing.
        HTTPException 404: If the order does not exist.
        HTTPException 500: If the Razorpay API call fails.
    """
    order_id: int | None = payload.get("order_id")
    if order_id is None:
        raise HTTPException(status_code=400, detail="order_id is required")

    # 1. Look up the internal order
    row = await conn.fetchrow(
        "SELECT id, total, status FROM orders WHERE id = $1",
        order_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # 2. Amount in paise
    amount_paise: int = int(row["total"] * 100)

    # 3. Create the Razorpay order
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

    # 4. Persist the razorpay_order_id
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

    # 5. Return details for the frontend
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
    await conn.execute(
        """
        UPDATE orders
        SET status              = 'paid',
            razorpay_payment_id = $1
        WHERE id = $2
        """,
        payload.razorpay_payment_id,
        order_id,
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
