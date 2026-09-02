"""
cart.py — Cart API routes for the ShopMind AI frontend.
All direct UI cart modifications are recorded to the ai_actions audit trail.
"""

from typing import Optional
import asyncpg
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from backend.database import get_db
from backend.logging_middleware import log_tool_call
from backend.tools import get_cart, remove_from_cart, clear_cart

router = APIRouter(prefix="/api", tags=["cart"])


class RemoveItemRequest(BaseModel):
    session_id: str
    product_id: int


class ClearCartRequest(BaseModel):
    session_id: str


@router.get("/cart")
async def api_get_cart(
    session_id: str = Query(..., description="Shopping session ID"),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get the current cart contents for a session."""
    return await get_cart(conn, session_id)


@router.delete("/cart/item")
async def api_remove_cart_item(
    body: RemoveItemRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    """Remove a specific item from the cart via direct UI action.

    Logs the manual removal to the ai_actions audit trail with agent_name='user_direct'.
    """
    result = await remove_from_cart(conn, body.session_id, body.product_id)

    # Log to audit trail as a direct user action
    await log_tool_call(
        conn=conn,
        session_id=body.session_id,
        tool_name="remove_from_cart",
        tool_input={"product_id": body.product_id},
        tool_output=result,
        decision="Direct UI action — no AI reasoning involved",
        user_approved=True,
        success=result.get("success", True),
    )

    # Update agent_name to 'user_direct' for this direct action
    await conn.execute(
        """
        UPDATE ai_actions
        SET agent_name = 'user_direct'
        WHERE id = (
            SELECT id FROM ai_actions
            WHERE session_id = $1
            ORDER BY id DESC
            LIMIT 1
        )
        """,
        body.session_id,
    )

    return result


@router.delete("/cart")
async def api_clear_cart(
    body: ClearCartRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    """Clear all items from the cart via direct UI action.

    Logs the manual cart clear to the ai_actions audit trail with agent_name='user_direct'.
    """
    result = await clear_cart(conn, body.session_id)

    # Log to audit trail as a direct user action
    await log_tool_call(
        conn=conn,
        session_id=body.session_id,
        tool_name="clear_cart",
        tool_input={"session_id": body.session_id},
        tool_output=result,
        decision="Direct UI action — no AI reasoning involved",
        user_approved=True,
        success=result.get("success", True),
    )

    # Update agent_name to 'user_direct' for this direct action
    await conn.execute(
        """
        UPDATE ai_actions
        SET agent_name = 'user_direct'
        WHERE id = (
            SELECT id FROM ai_actions
            WHERE session_id = $1
            ORDER BY id DESC
            LIMIT 1
        )
        """,
        body.session_id,
    )

    return result
