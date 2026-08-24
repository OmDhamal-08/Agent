"""
session.py — Customer identity and cart recovery endpoints.

Provides lightweight cart recovery via email or phone without requiring
passwords or customer account registration. Captures identity details
provided during checkout and allows recovering cart session IDs on other devices.
"""

from typing import Optional
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.database import get_db

router = APIRouter(prefix="/api/session", tags=["session"])


# ── Request Models ─────────────────────────────

class IdentifyRequest(BaseModel):
    session_id: str
    email: Optional[str] = None
    phone: Optional[str] = None
    name: Optional[str] = None


class RecoverRequest(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None


# ── POST /api/session/identify ─────────────────

@router.post("/identify")
async def session_identify(
    body: IdentifyRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    """Save or update customer identity for a shopping session.

    Called during checkout when the customer provides their name, email,
    or phone number. Links their contact information to their current session_id.
    """
    email = body.email.lower().strip() if body.email else None
    phone = body.phone.strip() if body.phone else None
    name = body.name.strip() if body.name else None

    if not email and not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least email or phone number is required to save customer identity.",
        )

    # Check if a customer identity record exists with either this email or phone
    existing = None
    if email:
        existing = await conn.fetchrow(
            "SELECT id FROM customer_identities WHERE email = $1",
            email,
        )
    if not existing and phone:
        existing = await conn.fetchrow(
            "SELECT id FROM customer_identities WHERE phone = $1",
            phone,
        )

    if existing:
        # Update existing identity with the active session_id and latest details
        await conn.execute(
            """
            UPDATE customer_identities
            SET session_id = $1,
                name = COALESCE($2, name),
                email = COALESCE($3, email),
                phone = COALESCE($4, phone),
                updated_at = NOW()
            WHERE id = $5
            """,
            body.session_id,
            name,
            email,
            phone,
            existing["id"],
        )
    else:
        # Insert new identity record
        await conn.execute(
            """
            INSERT INTO customer_identities (session_id, email, phone, name, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            """,
            body.session_id,
            email,
            phone,
            name,
        )

    return {
        "status": "ok",
        "message": "Customer identity updated successfully.",
        "session_id": body.session_id,
    }


# ── POST /api/session/recover ──────────────────

@router.post("/recover")
async def session_recover(
    body: RecoverRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    """Recover a previous session_id using an email or phone number.

    Allows customers switching devices or browsers to retrieve their
    saved shopping cart without needing a password.
    """
    email = body.email.lower().strip() if body.email else None
    phone = body.phone.strip() if body.phone else None

    if not email and not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide an email or phone number to recover your cart.",
        )

    row = None
    if email:
        row = await conn.fetchrow(
            """
            SELECT session_id, name, email, phone
            FROM customer_identities
            WHERE email = $1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            email,
        )

    if not row and phone:
        row = await conn.fetchrow(
            """
            SELECT session_id, name, email, phone
            FROM customer_identities
            WHERE phone = $1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            phone,
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No saved cart found for that email or phone number.",
        )

    return {
        "status": "success",
        "session_id": row["session_id"],
        "name": row["name"],
    }
