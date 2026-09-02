"""FastAPI application entry point for ShopMind AI."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import create_pool, close_pool

load_dotenv()


def _cors_origins() -> list[str]:
    """Read an explicit comma-separated CORS allow-list."""
    configured = os.getenv("CORS_ALLOW_ORIGINS", "")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return ["http://localhost:8000", "http://127.0.0.1:8000"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the database connection pool lifecycle."""
    print("[ShopMind AI] Starting server...")
    pool = await create_pool()
    print(f"[ShopMind AI] Database pool created (min=2, max=10)")
    yield
    await close_pool()
    print("[ShopMind AI] Database pool closed. Goodbye!")


app = FastAPI(
    title="ShopMind AI",
    description="Agentic commerce assistant for laptops — Razorpay Hackathon",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend (served from same origin or dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,  # No cookies used; session_id is in request bodies
    allow_methods=["*"],
    allow_headers=["*"],
)


from backend.routes.chat import router as chat_router
from backend.routes.cart import router as cart_router
from backend.routes.checkout import router as checkout_router
from backend.routes.webhook import router as webhook_router
from backend.routes.catalog import router as catalog_router
from backend.routes.dashboard import router as dashboard_router
from backend.routes.admin_auth import router as admin_auth_router
from backend.routes.session import router as session_router
from backend.routes.campaign import router as campaign_router

app.include_router(chat_router)
app.include_router(cart_router)
app.include_router(checkout_router)
app.include_router(webhook_router)
app.include_router(catalog_router)
app.include_router(dashboard_router)
app.include_router(admin_auth_router)
app.include_router(session_router)
app.include_router(campaign_router)


@app.get("/api/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "service": "shopmind-ai"}



# ⚠️  These are NOT production-safe. They exist solely so that the
#     Option B failure scenario (out-of-stock) can be triggered live
#     during a hackathon demo without manual SQL.

from fastapi import Depends
from backend.database import get_db
from backend.auth import get_current_admin
import asyncpg


@app.post("/api/admin/simulate-stockout")
async def simulate_stockout(
    payload: dict,
    conn: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """[DEV/DEMO ONLY] Set a product's stock to 0 to simulate out-of-stock."""
    product_id = payload.get("product_id")
    if product_id is None:
        return {"error": "product_id is required"}

    await conn.execute(
        "UPDATE products SET stock = 0 WHERE id = $1",
        product_id,
    )
    return {
        "status": "ok",
        "message": f"Product {product_id} stock set to 0 (simulated stockout)",
        "warning": "DEV/DEMO ONLY — not for production use",
    }


@app.post("/api/admin/restore-stock")
async def restore_stock(
    payload: dict,
    conn: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """[DEV/DEMO ONLY] Restore a product's stock to a given quantity."""
    product_id = payload.get("product_id")
    stock = payload.get("stock", 10)
    if product_id is None:
        return {"error": "product_id is required"}

    await conn.execute(
        "UPDATE products SET stock = $1 WHERE id = $2",
        stock,
        product_id,
    )
    return {
        "status": "ok",
        "message": f"Product {product_id} stock restored to {stock}",
        "warning": "DEV/DEMO ONLY — not for production use",
    }


# Static files — only for local dev (Vercel serves frontend via CDN)
if not os.getenv("VERCEL"):
    FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
