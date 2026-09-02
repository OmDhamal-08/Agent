"""Razorpay checkout routes with idempotent payment finalisation."""

import logging
import os
from collections import defaultdict
from typing import Any

import asyncpg
import razorpay
from fastapi import APIRouter, Depends, HTTPException

from backend.database import get_db
from backend.logging_middleware import log_tool_call
from backend.models import PaymentFailedRequest, PaymentVerifyRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["checkout"])

_razorpay_client: razorpay.Client | None = None


def get_razorpay_client() -> razorpay.Client:
    """Return the lazily-created Razorpay client."""
    global _razorpay_client
    if _razorpay_client is None:
        key_id = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
        key_secret = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()
        if not key_id or not key_secret:
            missing = []
            if not key_id:
                missing.append("RAZORPAY_KEY_ID")
            if not key_secret:
                missing.append("RAZORPAY_KEY_SECRET")
            raise RuntimeError(f"Razorpay environment variable(s) not set or empty: {', '.join(missing)}")
        _razorpay_client = razorpay.Client(auth=(key_id, key_secret))
    return _razorpay_client


async def finalize_paid_order(
    conn: asyncpg.Connection,
    razorpay_order_id: str,
    razorpay_payment_id: str | None,
) -> dict[str, Any]:
    """Atomically mark one order paid and decrement its snapshot inventory.

    Both verified browser callbacks and signed Razorpay webhooks call this
    function. The row lock makes repeated or concurrent delivery idempotent.
    """
    async with conn.transaction():
        order = await conn.fetchrow(
            """
            SELECT id, session_id, status
            FROM orders
            WHERE razorpay_order_id = $1
            FOR UPDATE
            """,
            razorpay_order_id,
        )
        if order is None:
            return {"outcome": "not_found"}

        order_id = order["id"]
        session_id = order["session_id"]
        if order["status"] == "paid":
            return {"outcome": "already_paid", "order_id": order_id, "session_id": session_id}
        if order["status"] == "failed":
            return {"outcome": "already_failed", "order_id": order_id, "session_id": session_id}
        if order["status"] != "created":
            return {"outcome": "invalid_state", "order_id": order_id, "session_id": session_id}

        items = await conn.fetch(
            """
            SELECT product_id, quantity, source
            FROM order_items
            WHERE order_id = $1
            ORDER BY id
            """,
            order_id,
        )
        # Support orders created before the order_items migration. New orders
        # always use the immutable snapshot above.
        if not items:
            items = await conn.fetch(
                """
                SELECT product_id, quantity, source
                FROM cart_items
                WHERE session_id = $1
                ORDER BY created_at
                """,
                session_id,
            )

        quantities: dict[int, int] = defaultdict(int)
        for item in items:
            quantities[item["product_id"]] += item["quantity"]

        # Lock all products before changing any stock. This prevents a partial
        # decrement when one item becomes unavailable.
        for product_id in sorted(quantities):
            product = await conn.fetchrow(
                "SELECT stock FROM products WHERE id = $1 FOR UPDATE", product_id
            )
            if product is None or product["stock"] < quantities[product_id]:
                await conn.execute(
                    """
                    UPDATE orders
                    SET status = 'failed', failure_reason = $1
                    WHERE id = $2
                    """,
                    f"insufficient_stock_at_payment: product_id={product_id}",
                    order_id,
                )
                return {
                    "outcome": "stock_unavailable",
                    "order_id": order_id,
                    "session_id": session_id,
                    "product_id": product_id,
                }

        for product_id, quantity in quantities.items():
            await conn.execute(
                "UPDATE products SET stock = stock - $1 WHERE id = $2",
                quantity,
                product_id,
            )

        actual_product_id = next(
            (item["product_id"] for item in items if item["source"] == "ai_recommendation"),
            items[0]["product_id"] if items else None,
        )
        await conn.execute(
            """
            UPDATE orders
            SET status = 'paid', razorpay_payment_id = $1,
                actual_product_purchased_id = $2, failure_reason = NULL
            WHERE id = $3
            """,
            razorpay_payment_id,
            actual_product_id,
            order_id,
        )
        # A successful order consumes the cart; later checkout attempts cannot
        # accidentally charge for the same items again.
        await conn.execute("DELETE FROM cart_items WHERE session_id = $1", session_id)
        return {"outcome": "paid", "order_id": order_id, "session_id": session_id}


async def log_payment_result(
    conn: asyncpg.Connection,
    result: dict[str, Any],
    razorpay_order_id: str,
    razorpay_payment_id: str | None,
    source: str,
) -> None:
    """Write one audit entry only for a newly finalised order."""
    if result["outcome"] not in {"paid", "stock_unavailable"}:
        return
    success = result["outcome"] == "paid"
    decision = (
        f"Payment finalised from {source} for order {result['order_id']}."
        if success
        else f"Payment received but stock was unavailable for product {result['product_id']}; order {result['order_id']} failed."
    )
    await log_tool_call(
        conn=conn,
        session_id=result["session_id"],
        tool_name="razorpay_checkout",
        tool_input={"order_id": result["order_id"], "razorpay_order_id": razorpay_order_id},
        tool_output={"status": result["outcome"], "razorpay_payment_id": razorpay_payment_id},
        decision=decision,
        user_approved=None,
        success=success,
    )


@router.post("/create-order")
async def create_order(payload: dict[str, Any], conn: asyncpg.Connection = Depends(get_db)) -> dict[str, Any]:
    """Create a Razorpay order once for a still-pending internal order."""
    order_id = payload.get("order_id")
    session_id = payload.get("session_id")
    if not isinstance(order_id, int) or not session_id:
        raise HTTPException(status_code=400, detail="order_id and session_id are required")

    row = await conn.fetchrow(
        "SELECT id, total, status, session_id, razorpay_order_id FROM orders WHERE id = $1",
        order_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if row["session_id"] != session_id:
        raise HTTPException(status_code=403, detail="Order does not belong to this session")
    if row["status"] != "created":
        raise HTTPException(status_code=409, detail="Order is no longer awaiting payment")

    amount_paise = int(row["total"] * 100)
    razorpay_order_id = row["razorpay_order_id"]
    if not razorpay_order_id:
        try:
            rz_order = get_razorpay_client().order.create(
                data={"amount": amount_paise, "currency": "INR", "receipt": f"order_{order_id}"}
            )
        except Exception as exc:
            logger.exception("Razorpay order creation failed for order %s", order_id)
            print(f"[ShopMind Razorpay ERROR] Order {order_id} creation failed: {exc}", flush=True)
            raise HTTPException(status_code=502, detail=f"Could not create the payment order: {exc}") from exc
        razorpay_order_id = rz_order["id"]
        updated = await conn.fetchval(
            """
            UPDATE orders SET razorpay_order_id = $1
            WHERE id = $2 AND razorpay_order_id IS NULL AND status = 'created'
            RETURNING razorpay_order_id
            """,
            razorpay_order_id,
            order_id,
        )
        if updated is None:
            razorpay_order_id = await conn.fetchval(
                "SELECT razorpay_order_id FROM orders WHERE id = $1", order_id
            )

    return {
        "razorpay_order_id": razorpay_order_id,
        "amount": amount_paise,
        "currency": "INR",
        "key_id": (os.getenv("RAZORPAY_KEY_ID") or "").strip(),
    }


@router.post("/verify-payment")
async def verify_payment(
    payload: PaymentVerifyRequest, conn: asyncpg.Connection = Depends(get_db)
) -> dict[str, str | int]:
    """Verify a client callback before finalising the matching order."""
    try:
        get_razorpay_client().utility.verify_payment_signature(
            {
                "razorpay_order_id": payload.razorpay_order_id,
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature": payload.razorpay_signature,
            }
        )
    except razorpay.errors.SignatureVerificationError:
        # An unsigned browser request must never be able to change an order's
        # state. Razorpay's signed webhook remains authoritative for failures.
        raise HTTPException(status_code=400, detail="Payment signature verification failed")

    result = await finalize_paid_order(conn, payload.razorpay_order_id, payload.razorpay_payment_id)
    if result["outcome"] == "not_found":
        raise HTTPException(status_code=404, detail="Order not found for the Razorpay order ID")
    if result["outcome"] == "already_failed":
        raise HTTPException(status_code=409, detail="Order has already failed")

    await log_payment_result(
        conn, result, payload.razorpay_order_id, payload.razorpay_payment_id, "verified client callback"
    )
    status = "success" if result["outcome"] in {"paid", "already_paid"} else "failed"
    return {"status": status, "order_id": result["order_id"]}


@router.post("/payment-failed")
async def payment_failed(
    payload: PaymentFailedRequest, conn: asyncpg.Connection = Depends(get_db)
) -> dict[str, str | int]:
    """Record an unverified browser failure only for its own pending order."""
    failure_reason = " | ".join(
        filter(None, [payload.error_code, payload.error_description, payload.error_reason])
    ) or "unknown_error"
    async with conn.transaction():
        order = await conn.fetchrow(
            "SELECT session_id, razorpay_order_id, status FROM orders WHERE id = $1 FOR UPDATE",
            payload.order_id,
        )
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        if order["session_id"] != payload.session_id:
            raise HTTPException(status_code=403, detail="Order does not belong to this session")
        if payload.razorpay_order_id and payload.razorpay_order_id != order["razorpay_order_id"]:
            raise HTTPException(status_code=400, detail="Razorpay order ID does not match")
        if order["status"] == "paid":
            return {"status": "ignored", "order_id": payload.order_id}
        # Browser callbacks are not signed. Keep the order retryable and let
        # the signed webhook make any authoritative failure-state transition.

    await log_tool_call(
        conn=conn,
        session_id=payload.session_id,
        tool_name="razorpay_checkout",
        tool_input={"order_id": payload.order_id, "razorpay_order_id": payload.razorpay_order_id},
        tool_output={"failure_reason": failure_reason},
        decision=f"Browser reported payment failure for order {payload.order_id}: {failure_reason}",
        user_approved=None,
        success=False,
    )
    return {"status": "logged", "order_id": payload.order_id}
