"""Customer identity and session management."""

import uuid
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.database import get_db

router = APIRouter(prefix="/api/session", tags=["session"])


class SessionStartRequest(BaseModel):
    email: str


class IdentifyRequest(BaseModel):
    session_id: str
    email: Optional[str] = None
    phone: Optional[str] = None
    name: Optional[str] = None


@router.post("/start")
async def session_start(
    body: SessionStartRequest, conn: asyncpg.Connection = Depends(get_db)
) -> dict[str, str | bool | None]:
    """Start or resume a session by email."""
    email = body.email.lower().strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")

    row = await conn.fetchrow(
        "SELECT session_id, name FROM customer_identities WHERE email = $1", email
    )
    if row:
        return {
            "session_id": row["session_id"],
            "is_new": False,
            "name": row["name"],
        }
    
    new_session_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO customer_identities (session_id, email, updated_at)
        VALUES ($1, $2, NOW())
        """,
        new_session_id, email,
    )
    return {
        "session_id": new_session_id,
        "is_new": True,
        "name": None,
    }


@router.post("/identify")
async def session_identify(
    body: IdentifyRequest, conn: asyncpg.Connection = Depends(get_db)
) -> dict[str, str]:
    """Save customer identity during checkout."""
    email = body.email.lower().strip() if body.email else None
    phone = body.phone.strip() if body.phone else None
    name = body.name.strip() if body.name else None
    if not email and not phone:
        raise HTTPException(status_code=400, detail="At least an email or phone number is required.")

    email_match = await conn.fetchrow("SELECT id FROM customer_identities WHERE email = $1", email) if email else None
    phone_match = await conn.fetchrow("SELECT id FROM customer_identities WHERE phone = $1", phone) if phone else None
    if email_match and phone_match and email_match["id"] != phone_match["id"]:
        raise HTTPException(status_code=409, detail="Email and phone belong to different customer records.")

    existing = email_match or phone_match
    
    if existing:
        await conn.execute(
            """
            UPDATE customer_identities
            SET session_id = $1, name = COALESCE($2, name), email = COALESCE($3, email),
                phone = COALESCE($4, phone), updated_at = NOW()
            WHERE id = $5
            """,
            body.session_id, name, email, phone, existing["id"],
        )
    else:
        await conn.execute(
            """
            INSERT INTO customer_identities
                (session_id, email, phone, name, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            """,
            body.session_id, email, phone, name,
        )

    return {
        "status": "ok",
        "message": "Customer identity updated successfully.",
        "session_id": body.session_id,
    }
