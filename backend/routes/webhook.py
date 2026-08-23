"""
webhook.py — Razorpay webhook handler for ShopMind AI.

Receives server-to-server webhook events from Razorpay (e.g. ``order.paid``,
``payment.failed``).  The handler verifies the ``X-Razorpay-Signature``
header before processing any event, and *always* returns ``200 OK`` so
Razorpay does not retry indefinitely.
"""

import logging
import os
import json
from typing import Any

import razorpay
from fastapi import APIRouter, Request, Header, HTTPException

from backend.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["webhook"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_webhook_secret() -> str:
    """Return the Razorpay webhook secret from the environment.

    Raises:
        RuntimeError: If ``RAZORPAY_WEBHOOK_SECRET`` is not set.
    """
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("RAZORPAY_WEBHOOK_SECRET is not configured")
    return secret


def _verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify the Razorpay webhook HMAC-SHA256 signature.

    Args:
        body: Raw request body bytes.
        signature: Value of the ``X-Razorpay-Signature`` header.
        secret: The webhook secret configured in the Razorpay dashboard.

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    try:
        client = razorpay.Client(auth=("", ""))  # auth not needed for utility
        client.utility.verify_webhook_signature(
            body.decode("utf-8"),
            signature,
            secret,
        )
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


async def _handle_order_paid(event_payload: dict[str, Any]) -> None:
    """Process an ``order.paid`` webhook event.

    Updates the order status to ``'paid'`` and stores the
    ``razorpay_payment_id`` extracted from the event payload.

    Args:
        event_payload: The ``payload`` dict from the webhook body.
    """
    payment_entity = (
        event_payload.get("payment", {}).get("entity", {})
    )
    razorpay_order_id: str | None = payment_entity.get("order_id")
    razorpay_payment_id: str | None = payment_entity.get("id")

    if not razorpay_order_id:
        logger.warning("order.paid event missing order_id in payment entity")
        return

    async for conn in get_db():
        await conn.execute(
            """
            UPDATE orders
            SET status              = 'paid',
                razorpay_payment_id = $1
            WHERE razorpay_order_id = $2
              AND status != 'paid'
            """,
            razorpay_payment_id,
            razorpay_order_id,
        )
        logger.info(
            "Webhook: order.paid — updated order with razorpay_order_id=%s",
            razorpay_order_id,
        )


async def _handle_payment_failed(event_payload: dict[str, Any]) -> None:
    """Process a ``payment.failed`` webhook event.

    Updates the order status to ``'failed'`` and records the failure
    reason from the error metadata attached to the payment entity.

    Args:
        event_payload: The ``payload`` dict from the webhook body.
    """
    payment_entity = (
        event_payload.get("payment", {}).get("entity", {})
    )
    razorpay_order_id: str | None = payment_entity.get("order_id")
    error_description: str = payment_entity.get(
        "error_description", "unknown_error"
    )
    error_reason: str | None = payment_entity.get("error_reason")

    failure_reason = " | ".join(filter(None, [error_description, error_reason]))

    if not razorpay_order_id:
        logger.warning("payment.failed event missing order_id in payment entity")
        return

    async for conn in get_db():
        await conn.execute(
            """
            UPDATE orders
            SET status         = 'failed',
                failure_reason = $1
            WHERE razorpay_order_id = $2
              AND status NOT IN ('paid', 'failed')
            """,
            failure_reason,
            razorpay_order_id,
        )
        logger.info(
            "Webhook: payment.failed — updated order with razorpay_order_id=%s: %s",
            razorpay_order_id,
            failure_reason,
        )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

# Map of event names to their handler coroutines
_EVENT_HANDLERS: dict[str, Any] = {
    "order.paid": _handle_order_paid,
    "payment.failed": _handle_payment_failed,
}


@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(None),
) -> dict[str, str]:
    """Handle incoming Razorpay webhook events.

    Workflow:
        1. Read the raw request body.
        2. Verify the ``X-Razorpay-Signature`` header using the webhook
           secret.
        3. Parse the JSON body and dispatch to the appropriate handler
           based on the ``event`` field.
        4. Always return ``200 OK`` — Razorpay retries on non-2xx
           responses, so even unhandled events get a success response.

    Returns:
        ``{ "status": "ok" }``
    """
    body: bytes = await request.body()

    # --- Signature verification -------------------------------------------
    if not x_razorpay_signature:
        logger.warning("Webhook request missing X-Razorpay-Signature header")
        # Still return 200 to prevent Razorpay from retrying bad requests
        return {"status": "ok"}

    try:
        webhook_secret = _get_webhook_secret()
    except RuntimeError:
        logger.error("RAZORPAY_WEBHOOK_SECRET not configured; ignoring webhook")
        return {"status": "ok"}

    if not _verify_webhook_signature(body, x_razorpay_signature, webhook_secret):
        logger.warning("Webhook signature verification failed")
        # Return 200 to avoid retries; the event is simply ignored
        return {"status": "ok"}

    # --- Parse and dispatch -----------------------------------------------
    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Webhook body is not valid JSON")
        return {"status": "ok"}

    event_name: str = data.get("event", "")
    event_payload: dict[str, Any] = data.get("payload", {})

    handler = _EVENT_HANDLERS.get(event_name)
    if handler is not None:
        try:
            await handler(event_payload)
        except Exception:
            logger.exception(
                "Error processing webhook event '%s'", event_name
            )
    else:
        logger.debug("Ignoring unhandled webhook event: %s", event_name)

    return {"status": "ok"}
