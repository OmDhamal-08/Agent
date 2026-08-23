"""
cart.py — Cart API route for the ShopMind AI frontend.

Provides a simple GET endpoint to retrieve cart contents for a session,
used by the frontend cart sidebar.
"""

import asyncpg
from fastapi import APIRouter, Depends, Query

from backend.database import get_db
from backend.tools import get_cart

router = APIRouter(prefix="/api", tags=["cart"])


@router.get("/cart")
async def api_get_cart(
    session_id: str = Query(..., description="Shopping session ID"),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get the current cart contents for a session.

    Returns:
        Cart items with product details, quantities, sources, and total.
    """
    return await get_cart(conn, session_id)
