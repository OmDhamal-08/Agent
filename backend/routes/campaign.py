"""Campaign Orchestrator API endpoints.

Provides routes for triggering campaign scans and viewing campaign history,
protected by the same admin auth used by the dashboard.
"""

import os
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query

from backend.auth import get_current_admin
from backend.campaign_agent import run_campaign_scan
from backend.adapters.gemini_adapter import GeminiAdapter
from backend.database import get_db

router = APIRouter(prefix='/api/campaigns', tags=['campaigns'])

# ──────────────────────────────────────────────
# Singleton GeminiAdapter (same pattern as chat.py)
# ──────────────────────────────────────────────

_adapter: GeminiAdapter | None = None


def _get_adapter() -> GeminiAdapter:
    """Return (and lazily create) a GeminiAdapter for the campaign agent."""
    global _adapter
    if _adapter is None:
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise RuntimeError('GEMINI_API_KEY not set')
        _adapter = GeminiAdapter(api_key=api_key)
    return _adapter


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _serialize(value: Any) -> Any:
    """Convert non-JSON-serializable types to JSON-safe equivalents."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize_row(row: dict) -> dict:
    """Apply ``_serialize`` to every value in a row dict."""
    return {k: _serialize(v) for k, v in row.items()}


# ──────────────────────────────────────────────
# POST /api/campaigns/run
# ──────────────────────────────────────────────

@router.post('/run')
async def trigger_campaign_scan(
    conn: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Trigger one campaign orchestrator pass immediately.

    For manual / demo use from the merchant dashboard.  Returns a summary
    of all decisions made during this run.
    """
    adapter = _get_adapter()
    result = await run_campaign_scan(conn, adapter)

    # Ensure all values are JSON-serializable
    serialized_decisions = []
    for d in result.get("decisions", []):
        clean = {}
        for k, v in d.items():
            clean[k] = _serialize(v)
        serialized_decisions.append(clean)

    return {
        "carts_scanned": result["carts_scanned"],
        "nudges_sent":   result["nudges_sent"],
        "carts_skipped": result["carts_skipped"],
        "message":       result["message"],
        "decisions":     serialized_decisions,
    }


# ──────────────────────────────────────────────
# GET /api/campaigns/history
# ──────────────────────────────────────────────

@router.get('/history')
async def campaign_history(
    limit: int = Query(50, ge=1, le=200, description="Max rows to return"),
    conn: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return recent campaign_actions rows for the dashboard.

    Ordered by ``created_at DESC`` (most recent first).
    """
    rows = await conn.fetch(
        """
        SELECT
            id,
            session_id,
            cart_snapshot,
            cart_value,
            cart_age_minutes,
            decision,
            action_taken,
            discount_percent,
            simulated_channel,
            ai_action_log_id,
            created_at
        FROM campaign_actions
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )

    actions = []
    for row in rows:
        item = _serialize_row(dict(row))
        # Parse cart_snapshot JSONB if it's a string
        if isinstance(item.get("cart_snapshot"), str):
            import json
            try:
                item["cart_snapshot"] = json.loads(item["cart_snapshot"])
            except (json.JSONDecodeError, TypeError):
                pass
        actions.append(item)

    # Compute summary stats
    nudges = sum(1 for a in actions if a.get("action_taken") in ("reminder", "discount_offer"))
    skipped = sum(1 for a in actions if a.get("action_taken") == "no_action")

    return {
        "actions": actions,
        "count":   len(actions),
        "stats": {
            "nudges_sent":   nudges,
            "carts_skipped": skipped,
        },
    }
