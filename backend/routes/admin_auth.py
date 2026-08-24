"""
admin_auth.py — Admin signup, login, and profile endpoints.

Provides email+password authentication for the merchant dashboard.
Signup is gated behind an ADMIN_SIGNUP_CODE env var so random visitors
cannot create dashboard accounts.
"""

import os

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

from backend.auth import (
    create_access_token,
    get_current_admin,
    hash_password,
    verify_password,
    JWT_EXPIRE_HOURS,
)
from backend.database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])

ADMIN_SIGNUP_CODE = os.getenv("ADMIN_SIGNUP_CODE", "")


# ── Request / response models ─────────────────

class SignupRequest(BaseModel):
    email: str
    password: str
    signup_code: str


class LoginRequest(BaseModel):
    email: str
    password: str


# ── POST /api/admin/signup ─────────────────────

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def admin_signup(
    body: SignupRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    """Register a new admin/merchant account.

    Requires a valid ``signup_code`` that matches the ``ADMIN_SIGNUP_CODE``
    environment variable. Passwords must be at least 8 characters.
    """
    # Validate signup code
    if body.signup_code != ADMIN_SIGNUP_CODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signup code.",
        )

    # Validate password length
    if len(body.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters.",
        )

    # Hash password and insert
    hashed = hash_password(body.password)

    try:
        await conn.execute(
            """
            INSERT INTO admin_users (email, password_hash)
            VALUES ($1, $2)
            """,
            body.email.lower().strip(),
            hashed,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    return {
        "message": "Admin account created successfully.",
        "email": body.email.lower().strip(),
    }


# ── POST /api/admin/login ──────────────────────

@router.post("/login")
async def admin_login(
    body: LoginRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    """Authenticate an admin and return a JWT token.

    Returns a generic error message on failure — does not reveal
    whether the email or password was wrong.
    """
    email = body.email.lower().strip()

    row = await conn.fetchrow(
        "SELECT id, email, password_hash FROM admin_users WHERE email = $1",
        email,
    )

    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    token = create_access_token(data={"sub": row["email"]})

    return {
        "token": token,
        "email": row["email"],
        "expires_in": f"{JWT_EXPIRE_HOURS}h",
    }


# ── GET /api/admin/me ──────────────────────────

@router.get("/me")
async def admin_me(admin: dict = Depends(get_current_admin)):
    """Return the authenticated admin's profile.

    Used by the frontend to verify token validity on page load.
    """
    return {"email": admin["email"]}
