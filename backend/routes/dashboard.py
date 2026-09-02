"""Dashboard API endpoints for the merchant dashboard.

Provides business-impact metrics, AI decision audit trails, order history,
and session listings used by the front-end admin dashboard.
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query

from backend.database import get_db
from backend.logging_middleware import get_session_actions
from backend.auth import get_current_admin

router = APIRouter(prefix='/api/dashboard', tags=['dashboard'])


def _serialize(value: Any) -> Any:
    """Convert non-JSON-serializable types to JSON-safe equivalents.

    Handles ``Decimal`` → ``float`` and ``datetime`` → ISO-8601 string
    conversions that asyncpg record values commonly require.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize_row(row: dict) -> dict:
    """Apply ``_serialize`` to every value in a row dict."""
    return {k: _serialize(v) for k, v in row.items()}


@router.get('/summary')
async def dashboard_summary(conn: asyncpg.Connection = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """Business impact metrics.

    Aggregates data from the ``orders`` and ``ai_actions`` tables to
    compute revenue, conversion, and upsell KPIs for the dashboard.
    """
    # Total orders & revenue
    totals = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                        AS total_orders,
            COALESCE(SUM(total), 0)  AS total_revenue
        FROM orders
        WHERE status = 'paid'
        """
    )

    # AI-assisted orders & revenue
    ai = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                        AS ai_assisted_orders,
            COALESCE(SUM(total), 0)  AS ai_assisted_revenue
        FROM orders
        WHERE status = 'paid'
          AND ai_assisted = true
        """
    )

    total_orders = totals['total_orders']
    total_revenue = float(totals['total_revenue'])
    ai_orders = ai['ai_assisted_orders']
    ai_revenue = float(ai['ai_assisted_revenue'])

    ai_pct = (ai_orders / total_orders * 100) if total_orders > 0 else 0.0
    avg_order = (total_revenue / total_orders) if total_orders > 0 else 0.0

    # Upsell metrics
    upsell = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                        AS upsell_accepted_count,
            COALESCE(SUM(upsell_amount), 0)  AS upsell_revenue
        FROM orders
        WHERE status = 'paid'
          AND upsell_accepted = true
        """
    )

    # Failed orders
    failed = await conn.fetchval(
        "SELECT COUNT(*) FROM orders WHERE status = 'failed'"
    )

    # Upsell acceptance rate — from ai_actions audit trail
    # Every add_to_cart with source='ai_upsell' that went through confirmation
    upsell_offers_total = await conn.fetchval(
        """
        SELECT COUNT(*) FROM ai_actions
        WHERE tool_name = 'add_to_cart'
          AND input::text LIKE '%%ai_upsell%%'
          AND user_approved IS NOT NULL
        """
    ) or 0
    upsell_offers_accepted = await conn.fetchval(
        """
        SELECT COUNT(*) FROM ai_actions
        WHERE tool_name = 'add_to_cart'
          AND input::text LIKE '%%ai_upsell%%'
          AND user_approved = true
        """
    ) or 0
    upsell_acceptance_rate = round(
        (upsell_offers_accepted / upsell_offers_total * 100) if upsell_offers_total > 0 else 0.0, 1
    )

    # Conversion rate — sessions with agent activity vs sessions with paid orders
    total_sessions = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT session_id) FROM ai_actions
        WHERE agent_name IS NULL OR agent_name != 'campaign_orchestrator'
        """
    ) or 0
    converting_sessions = await conn.fetchval(
        "SELECT COUNT(DISTINCT session_id) FROM orders WHERE status = 'paid'"
    ) or 0
    conversion_rate = round(
        (converting_sessions / total_sessions * 100) if total_sessions > 0 else 0.0, 1
    )

    return {
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'ai_assisted_orders': ai_orders,
        'ai_assisted_revenue': ai_revenue,
        'ai_assisted_percentage': round(ai_pct, 2),
        'avg_order_value': round(avg_order, 2),
        'upsell_accepted_count': upsell['upsell_accepted_count'],
        'upsell_revenue': float(upsell['upsell_revenue']),
        'failed_orders_count': failed,
        'upsell_acceptance_rate': upsell_acceptance_rate,
        'upsell_offers_total': upsell_offers_total,
        'upsell_offers_accepted': upsell_offers_accepted,
        'conversion_rate': conversion_rate,
        'total_sessions': total_sessions,
        'converting_sessions': converting_sessions,
    }


@router.get('/ai-actions')
async def dashboard_ai_actions(
    session_id: str | None = Query(None, description="Filter by session ID"),
    limit: int = Query(100, ge=1, le=500, description="Max rows to return"),
    conn: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """AI Decision Trace.

    Returns the chronological audit trail of tool calls the agent made,
    optionally filtered by ``session_id``. JSONB ``input``/``output``
    fields are parsed back into dicts for the JSON response.
    """
    actions = await get_session_actions(conn, session_id, limit)

    serialized: list[dict] = []
    for action in actions:
        row = {}
        for key, value in action.items():
            if key in ('input', 'output') and isinstance(value, str):
                try:
                    row[key] = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    row[key] = value
            else:
                row[key] = _serialize(value)
        serialized.append(row)

    return {
        'actions': serialized,
        'count': len(serialized),
    }


@router.get('/orders')
async def dashboard_orders(conn: asyncpg.Connection = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """All orders with status info.

    Joins the ``orders`` table with ``products`` to include the product
    name alongside each order record.
    """
    rows = await conn.fetch(
        """
        SELECT
            o.id,
            o.session_id,
            o.total,
            o.ai_assisted,
            o.status,
            o.failure_reason,
            o.razorpay_order_id,
            o.razorpay_payment_id,
            o.upsell_accepted,
            o.upsell_amount,
            o.created_at
        FROM orders o
        ORDER BY o.created_at DESC
        """
    )

    orders = [_serialize_row(dict(row)) for row in rows]

    return {
        'orders': orders,
        'count': len(orders),
    }


@router.get('/sessions')
async def dashboard_sessions(conn: asyncpg.Connection = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """List unique shopping sessions.

    Aggregates ``ai_actions`` by ``session_id`` to show the action count
    and time window for each session.
    """
    rows = await conn.fetch(
        """
        SELECT
            session_id,
            COUNT(*)            AS action_count,
            MIN(timestamp)      AS first_action,
            MAX(timestamp)      AS last_action
        FROM ai_actions
        GROUP BY session_id
        ORDER BY MAX(timestamp) DESC
        """
    )

    sessions = [_serialize_row(dict(row)) for row in rows]

    return {
        'sessions': sessions,
    }


@router.get('/comparison')
async def dashboard_comparison(conn: asyncpg.Connection = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """AI vs Human side-by-side comparison metrics.

    Splits paid orders by ``ai_assisted`` flag and computes orders count,
    revenue, AOV, and cross-sell attach rate for each group.
    """
    # AI-assisted group
    ai = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                        AS orders,
            COALESCE(SUM(total), 0)         AS revenue,
            COUNT(*) FILTER (WHERE upsell_accepted = true) AS cross_sell_count
        FROM orders
        WHERE status = 'paid' AND ai_assisted = true
        """
    )

    # Organic group
    organic = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                        AS orders,
            COALESCE(SUM(total), 0)         AS revenue,
            COUNT(*) FILTER (WHERE upsell_accepted = true) AS cross_sell_count
        FROM orders
        WHERE status = 'paid' AND ai_assisted = false
        """
    )

    ai_orders = ai['orders']
    ai_revenue = float(ai['revenue'])
    ai_aov = round(ai_revenue / ai_orders, 2) if ai_orders > 0 else 0.0
    ai_cross_sell = round(ai['cross_sell_count'] / ai_orders * 100, 1) if ai_orders > 0 else 0.0

    org_orders = organic['orders']
    org_revenue = float(organic['revenue'])
    org_aov = round(org_revenue / org_orders, 2) if org_orders > 0 else 0.0
    org_cross_sell = round(organic['cross_sell_count'] / org_orders * 100, 1) if org_orders > 0 else 0.0

    return {
        'ai': {
            'orders': ai_orders,
            'revenue': ai_revenue,
            'aov': ai_aov,
            'cross_sell_rate': ai_cross_sell,
        },
        'organic': {
            'orders': org_orders,
            'revenue': org_revenue,
            'aov': org_aov,
            'cross_sell_rate': org_cross_sell,
        },
    }
