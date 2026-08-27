"""Customer identity and recovery-code protected cart recovery."""

import hashlib
import hmac
import secrets
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.database import get_db

router = APIRouter(prefix="/api/session", tags=["session"])


class IdentifyRequest(BaseModel):
    session_id: str
    email: Optional[str] = None
    phone: Optional[str] = None
    name: Optional[str] = None


class RecoverRequest(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    recovery_code: str


def _hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


@router.post("/identify")
async def session_identify(
    body: IdentifyRequest, conn: asyncpg.Connection = Depends(get_db)
) -> dict[str, str]:
    """Save a customer identity and issue a high-entropy recovery code."""
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
    recovery_code = secrets.token_urlsafe(12)
    recovery_code_hash = _hash_recovery_code(recovery_code)
    if existing:
        await conn.execute(
            """
            UPDATE customer_identities
            SET session_id = $1, name = COALESCE($2, name), email = COALESCE($3, email),
                phone = COALESCE($4, phone), recovery_code_hash = $5, updated_at = NOW()
            WHERE id = $6
            """,
            body.session_id, name, email, phone, recovery_code_hash, existing["id"],
        )
    else:
        await conn.execute(
            """
            INSERT INTO customer_identities
                (session_id, email, phone, name, recovery_code_hash, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            """,
            body.session_id, email, phone, name, recovery_code_hash,
        )

    return {
        "status": "ok",
        "message": "Customer identity updated successfully.",
        "session_id": body.session_id,
        "recovery_code": recovery_code,
    }


@router.post("/recover")
async def session_recover(
    body: RecoverRequest, conn: asyncpg.Connection = Depends(get_db)
) -> dict[str, str | None]:
    """Recover a cart only after contact and recovery-code verification."""
    email = body.email.lower().strip() if body.email else None
    phone = body.phone.strip() if body.phone else None
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Please provide an email or phone number.")

    row = None
    if email:
        row = await conn.fetchrow(
            "SELECT session_id, name, recovery_code_hash FROM customer_identities WHERE email = $1",
            email,
        )
    if row is None and phone:
        row = await conn.fetchrow(
            "SELECT session_id, name, recovery_code_hash FROM customer_identities WHERE phone = $1",
            phone,
        )
    supplied_hash = _hash_recovery_code(body.recovery_code)
    if row is None or not row["recovery_code_hash"] or not hmac.compare_digest(supplied_hash, row["recovery_code_hash"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The recovery details are invalid.")
    return {"status": "success", "session_id": row["session_id"], "name": row["name"]}
