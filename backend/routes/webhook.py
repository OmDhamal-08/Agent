"""Verified Razorpay webhooks sharing the transactional payment finaliser."""

import json
import logging
import os
from typing import Any

import razorpay
from fastapi import APIRouter, Header, Request

from backend.database import get_db
from backend.routes.checkout import finalize_paid_order, log_payment_result

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["webhook"])


def _verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    try:
        razorpay.Client(auth=("", "")).utility.verify_webhook_signature(
            body.decode("utf-8"), signature, secret
        )
        return True
    except (UnicodeDecodeError, razorpay.errors.SignatureVerificationError):
        return False


async def _handle_order_paid(event_payload: dict[str, Any]) -> None:
    payment = event_payload.get("payment", {}).get("entity", {})
    razorpay_order_id = payment.get("order_id")
    razorpay_payment_id = payment.get("id")
    if not razorpay_order_id:
        logger.warning("order.paid event missing order_id")
        return

    async for conn in get_db():
        result = await finalize_paid_order(conn, razorpay_order_id, razorpay_payment_id)
        if result["outcome"] == "not_found":
            logger.warning("Webhook payment for unknown Razorpay order %s", razorpay_order_id)
            return
        await log_payment_result(conn, result, razorpay_order_id, razorpay_payment_id, "signed webhook")
        logger.info("Webhook finalised order %s (%s)", result["order_id"], result["outcome"])


async def _handle_payment_failed(event_payload: dict[str, Any]) -> None:
    payment = event_payload.get("payment", {}).get("entity", {})
    razorpay_order_id = payment.get("order_id")
    if not razorpay_order_id:
        logger.warning("payment.failed event missing order_id")
        return
    reason = " | ".join(filter(None, [payment.get("error_description"), payment.get("error_reason")]))
    async for conn in get_db():
        # Do not let an out-of-order failure overwrite a successfully paid order.
        await conn.execute(
            """
            UPDATE orders SET status = 'failed', failure_reason = $1
            WHERE razorpay_order_id = $2 AND status = 'created'
            """,
            reason or "unknown_error",
            razorpay_order_id,
        )


_EVENT_HANDLERS: dict[str, Any] = {
    "order.paid": _handle_order_paid,
    "payment.failed": _handle_payment_failed,
}


@router.post("/webhook")
async def razorpay_webhook(
    request: Request, x_razorpay_signature: str | None = Header(None)
) -> dict[str, str]:
    """Verify an event before processing; acknowledge invalid events once."""
    body = await request.body()
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not x_razorpay_signature or not secret:
        logger.warning("Ignoring unsigned webhook or webhook without configured secret")
        return {"status": "ok"}
    if not _verify_webhook_signature(body, x_razorpay_signature, secret):
        logger.warning("Ignoring webhook with an invalid signature")
        return {"status": "ok"}
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Ignoring webhook with invalid JSON")
        return {"status": "ok"}

    handler = _EVENT_HANDLERS.get(data.get("event"))
    if handler:
        try:
            await handler(data.get("payload", {}))
        except Exception:
            logger.exception("Webhook handler failed for event %s", data.get("event"))
    return {"status": "ok"}
