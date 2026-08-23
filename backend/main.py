"""
main.py — FastAPI application entry point for ShopMind AI.

Wires together: database pool, route modules, static file serving, and CORS.
Run with: uvicorn backend.main:app --reload
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import create_pool, close_pool

load_dotenv()

# ──────────────────────────────────────────────
# Lifespan: database pool lifecycle
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the database connection pool lifecycle."""
    print("🚀 Starting ShopMind AI...")
    pool = await create_pool()
    print(f"✓ Database pool created (min=2, max=10)")
    yield
    await close_pool()
    print("✓ Database pool closed. Goodbye!")


# ──────────────────────────────────────────────
# App setup
# ──────────────────────────────────────────────

app = FastAPI(
    title="ShopMind AI",
    description="Agentic commerce assistant for laptops — Razorpay Hackathon",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend (served from same origin or dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Hackathon: allow all; production: restrict
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# Register route modules
# ──────────────────────────────────────────────

from backend.routes.chat import router as chat_router
from backend.routes.cart import router as cart_router
from backend.routes.checkout import router as checkout_router
from backend.routes.webhook import router as webhook_router
from backend.routes.catalog import router as catalog_router
from backend.routes.dashboard import router as dashboard_router

app.include_router(chat_router)
app.include_router(cart_router)
app.include_router(checkout_router)
app.include_router(webhook_router)
app.include_router(catalog_router)
app.include_router(dashboard_router)


# ──────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "service": "shopmind-ai"}


# ──────────────────────────────────────────────
# Static files (frontend)
# ──────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
