"""Campaign Orchestrator tool functions.
Async PostgreSQL implementations for the tools the campaign orchestrator agent uses.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

import asyncpg

from backend.logging_middleware import log_tool_call

COOLDOWN_HOURS = 6
"""Minimum hours between nudges for the same session."""

CAMPAIGN_AGENT_NAME = "campaign_orchestrator"
"""Agent name used when logging to ai_actions."""


def _dec(value: Any) -> float:
    """Convert ``Decimal`` to a JSON-safe ``float``."""
    if isinstance(value, Decimal):
        return float(value)
    return value


async def find_abandoned_carts(
    conn: asyncpg.Connection,
    min_age_minutes: int = 30,
    min_cart_value: float = 0,
) -> Dict[str, Any]:
    """Find carts that look abandoned and may benefit from a recovery nudge.

    A cart is considered abandoned when:
    - It has at least one item.
    - Its most recent item was added more than *min_age_minutes* ago.
    - There is no paid or pending (``created``) order for the same session
      that was created after the cart's last update.
    - The session has not been nudged in the last ``COOLDOWN_HOURS`` hours.
    - The total cart value meets the *min_cart_value* threshold.

    Args:
        conn: Database connection.
        min_age_minutes: Minimum minutes since last cart activity.
        min_cart_value: Minimum cart value (in store currency) to consider.

    Returns:
        ``{"abandoned_carts": [...], "count": N}``
    """
    rows = await conn.fetch(
        """
        WITH cart_summary AS (
            SELECT
                ci.session_id,
                SUM(p.price * ci.quantity)       AS cart_value,
                MAX(ci.created_at)               AS last_activity,
                EXTRACT(EPOCH FROM (NOW() - MAX(ci.created_at))) / 60
                                                  AS age_minutes,
                json_agg(json_build_object(
                    'product_id', p.id,
                    'name',      p.name,
                    'price',     p.price,
                    'quantity',  ci.quantity,
                    'category',  p.category
                ))                               AS items
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            GROUP BY ci.session_id
        )
        SELECT
            cs.session_id,
            cs.cart_value,
            cs.age_minutes::integer     AS cart_age_minutes,
            cs.items                    AS cart_items,
            cid.email                   AS customer_email,
            cid.phone                   AS customer_phone,
            cid.name                    AS customer_name
        FROM cart_summary cs
        LEFT JOIN customer_identities cid
            ON cid.session_id = cs.session_id
        WHERE cs.age_minutes >= $1
          AND cs.cart_value  >= $2
          -- Exclude sessions with an order created after last cart activity
          AND NOT EXISTS (
              SELECT 1 FROM orders o
              WHERE o.session_id = cs.session_id
                AND o.created_at >= cs.last_activity
          )
          -- Exclude sessions nudged within the cooldown window
          AND NOT EXISTS (
              SELECT 1 FROM campaign_actions ca
              WHERE ca.session_id = cs.session_id
                AND ca.action_taken != 'no_action'
                AND ca.created_at >= NOW() - ($3 || ' hours')::interval
          )
        ORDER BY cs.cart_value DESC
        """,
        min_age_minutes,
        min_cart_value,
        str(COOLDOWN_HOURS),
    )

    carts: List[Dict[str, Any]] = []
    for row in rows:
        carts.append({
            "session_id":       row["session_id"],
            "cart_value":       _dec(row["cart_value"]),
            "cart_age_minutes": row["cart_age_minutes"],
            "cart_items":       row["cart_items"] if isinstance(row["cart_items"], list) else json.loads(row["cart_items"]),
            "customer_email":   row["customer_email"],
            "customer_phone":   row["customer_phone"],
            "customer_name":    row["customer_name"],
        })

    return {"abandoned_carts": carts, "count": len(carts)}


async def get_cart_context(
    conn: asyncpg.Connection,
    session_id: str,
) -> Dict[str, Any]:
    """Get detailed cart contents and customer context for one session.

    Returns cart items with totals, plus whether the customer is a
    first-time or returning buyer (based on completed orders linked
    via ``customer_identities``).

    Args:
        conn: Database connection.
        session_id: The session whose cart to inspect.

    Returns:
        ``{"cart": {...}, "customer_type": "...", "past_orders_count": N}``
    """
    # ── Cart contents ───────────────────────
    items = await conn.fetch(
        """
        SELECT
            p.id        AS product_id,
            p.name,
            p.price,
            ci.quantity,
            p.category,
            p.ram_gb,
            p.gpu,
            p.cpu,
            ci.source,
            ci.created_at
        FROM cart_items ci
        JOIN products p ON p.id = ci.product_id
        WHERE ci.session_id = $1
        ORDER BY ci.created_at
        """,
        session_id,
    )

    if not items:
        return {
            "cart": {"items": [], "total": 0, "item_count": 0},
            "customer_type": "unknown",
            "past_orders_count": 0,
        }

    cart_items = []
    total = 0.0
    for row in items:
        subtotal = float(row["price"]) * row["quantity"]
        total += subtotal
        cart_items.append({
            "product_id": row["product_id"],
            "name":       row["name"],
            "price":      _dec(row["price"]),
            "quantity":   row["quantity"],
            "category":   row["category"],
            "ram_gb":     row["ram_gb"],
            "gpu":        row["gpu"],
            "cpu":        row["cpu"],
            "source":     row["source"],
            "subtotal":   round(subtotal, 2),
        })

    # ── Customer history ────────────────────
    # Check if this customer (by email/phone) has completed orders
    past_orders_count = 0
    identity = await conn.fetchrow(
        "SELECT email, phone FROM customer_identities WHERE session_id = $1",
        session_id,
    )

    if identity:
        # Look for paid orders from any session with the same email or phone
        conditions = []
        params: list[Any] = []
        idx = 1
        if identity["email"]:
            conditions.append(f"cid2.email = ${idx}")
            params.append(identity["email"])
            idx += 1
        if identity["phone"]:
            conditions.append(f"cid2.phone = ${idx}")
            params.append(identity["phone"])
            idx += 1

        if conditions:
            where_clause = " OR ".join(conditions)
            past_orders_count = await conn.fetchval(
                f"""
                SELECT COUNT(*) FROM orders o
                JOIN customer_identities cid2
                    ON cid2.session_id = o.session_id
                WHERE ({where_clause})
                  AND o.status = 'paid'
                """,
                *params,
            )

    customer_type = "returning" if past_orders_count > 0 else "first_time"

    return {
        "cart": {
            "items":      cart_items,
            "total":      round(total, 2),
            "item_count": len(cart_items),
        },
        "customer_type":    customer_type,
        "past_orders_count": past_orders_count,
    }


async def record_campaign_decision(
    conn: asyncpg.Connection,
    session_id: str,
    cart_snapshot: dict | list,
    cart_value: float,
    cart_age_minutes: int,
    decision: str,
    action_taken: str,
    discount_percent: float | None = None,
    simulated_channel: str | None = None,
) -> Dict[str, Any]:
    """Persist the orchestrator's decision and log it to the audit trail.

    Writes one row to ``campaign_actions`` and one row to ``ai_actions``
    (via ``log_tool_call``), linking them via ``ai_action_log_id``.

    Args:
        conn: Database connection.
        session_id: The session this decision applies to.
        cart_snapshot: JSON-serializable cart contents at decision time.
        cart_value: Total cart value.
        cart_age_minutes: Minutes since last cart activity.
        decision: The LLM's free-text reasoning for this decision.
        action_taken: One of ``'no_action'``, ``'reminder'``,
                      ``'discount_offer'``.
        discount_percent: Discount percentage (only for ``discount_offer``).
        simulated_channel: ``'email'``, ``'sms'``, or ``None``.

    Returns:
        ``{"campaign_action_id": N, "ai_action_log_id": N, "success": True}``
    """
    # Validate action_taken
    valid_actions = ("no_action", "reminder", "discount_offer")
    if action_taken not in valid_actions:
        action_taken = "no_action"

    # Clamp discount
    if discount_percent is not None:
        discount_percent = max(0, min(discount_percent, 15))

    # 1. Log to ai_actions via the shared helper
    ai_log_id = await log_tool_call(
        conn=conn,
        session_id=session_id,
        tool_name="record_campaign_decision",
        tool_input={
            "session_id":       session_id,
            "cart_value":       cart_value,
            "cart_age_minutes": cart_age_minutes,
            "action_taken":     action_taken,
            "discount_percent": discount_percent,
            "simulated_channel": simulated_channel,
        },
        tool_output={
            "decision":    decision,
            "action_taken": action_taken,
            "simulated":   True,
        },
        decision=decision,
        user_approved=None,   # No human confirmation needed
        success=True,
        agent_name=CAMPAIGN_AGENT_NAME,
    )

    # 2. Write to campaign_actions with the ai_actions FK
    snapshot_json = json.dumps(cart_snapshot) if not isinstance(cart_snapshot, str) else cart_snapshot

    row = await conn.fetchrow(
        """
        INSERT INTO campaign_actions
            (session_id, cart_snapshot, cart_value, cart_age_minutes,
             decision, action_taken, discount_percent, simulated_channel,
             ai_action_log_id)
        VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        session_id,
        snapshot_json,
        cart_value,
        cart_age_minutes,
        decision,
        action_taken,
        discount_percent,
        simulated_channel,
        ai_log_id,
    )

    action_label = {
        "no_action": "No action taken",
        "reminder": f"SIMULATED {simulated_channel or 'email'} reminder sent",
        "discount_offer": f"SIMULATED {simulated_channel or 'email'} with {discount_percent}% discount offer sent",
    }.get(action_taken, "Unknown action")

    print(f"[Campaign Orchestrator] {action_label} for session {session_id[:8]}... "
          f"(cart Rs.{cart_value:,.0f}, age {cart_age_minutes}min)")

    return {
        "campaign_action_id": row["id"],
        "ai_action_log_id":   ai_log_id,
        "success":            True,
    }


CAMPAIGN_TOOL_DISPATCH: Dict[str, Any] = {
    "find_abandoned_carts":    find_abandoned_carts,
    "get_cart_context":        get_cart_context,
    "record_campaign_decision": record_campaign_decision,
}
