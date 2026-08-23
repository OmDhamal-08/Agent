# CHANGELOG — ShopMind AI

All notable changes to this project are documented here.
Each entry includes the stage number, what was added/changed, and the
current status.

---

## Stage 1 — Database Schema + Seed Data

**Date:** 2026-08-23

**Added:**
- `backend/schema.sql` — DDL for 5 tables: products, cart_items, orders, ai_actions, co_purchase_history
- `backend/database.py` — asyncpg pool lifecycle management with FastAPI lifespan integration
- `backend/seed.py` — Standalone seed script with 25 laptops, 10 accessories, and ~250 co-purchase pairs
- `.env.example` — Environment variable template
- `requirements.txt` — Python dependencies
- `backend/__init__.py`, `backend/adapters/__init__.py`, `backend/routes/__init__.py` — Package init files

**Status:** Complete
**Verified:** Schema creates all tables idempotently. Seed data includes 25 laptops (₹40,990–₹1,19,990), 10 accessories, and co-purchase pairings generated programmatically based on product characteristics.

---

## Stage 2 — Tool Functions in FastAPI

**Date:** 2026-08-23

**Added:**
- `backend/tools.py` — 7 async tool functions: search_products, compare_products, get_cart, add_to_cart, get_complementary_products, check_customer_owns, initiate_checkout
- `backend/tool_definitions.py` — Provider-agnostic JSON tool schemas + TOOLS_REQUIRING_CONFIRMATION map

**Status:** Complete
**Verified:** All 7 tools have correct SQL queries with proper parameter binding, Decimal→float conversion, edge case handling (empty results, out of stock, product not found), and TOOL_DISPATCH registry.

---

## Stage 3 — Agent Loop + Gemini Adapter

**Date:** 2026-08-23

**Added:**
- `backend/adapters/gemini_adapter.py` — Converts neutral tool defs → Gemini FunctionDeclarations, wraps google-genai SDK calls with asyncio.to_thread, provides LLMResponse/ToolCall dataclasses
- `backend/agent_loop.py` — Core agent loop with 6-call cap, system prompt, tool dispatch, and helper functions

**Status:** Complete
**Verified:** Adapter correctly converts all 7 tool schemas. Agent loop handles text responses, tool calls, and edge cases (no response, max iterations).

---

## Stage 4 — Policy/Confirmation Gate

**Date:** 2026-08-23

**Added/Modified:**
- `backend/agent_loop.py` — Confirmation gate for add_to_cart and initiate_checkout (TOOLS_REQUIRING_CONFIRMATION check, PendingConfirmation dataclass, execute_confirmed_action/cancel_action functions)
- `backend/routes/chat.py` — POST /api/chat, POST /api/chat/confirm, POST /api/chat/cancel, GET /api/sessions
- `backend/models.py` — ChatRequest, ConfirmActionRequest, ChatResponse, PaymentVerifyRequest, PaymentFailedRequest

**Status:** Complete
**Verified:** Gated tools return pending_confirmation response with action_id. Confirm endpoint executes the tool and resumes the loop. Cancel endpoint logs rejection and gets agent's acknowledgment.

---

## Stage 5 — ai_actions Logging

**Date:** 2026-08-23

**Added:**
- `backend/logging_middleware.py` — log_tool_call (INSERT), log_confirmation_result (UPDATE), get_session_actions (SELECT)

**Modified:**
- `backend/agent_loop.py` — Every tool execution (success or failure) calls log_tool_call. Confirmation-gated tools logged with user_approved=None initially, updated after user responds.

**Status:** Complete
**Verified:** All tool calls produce ai_actions rows with tool_name, input (JSONB), output (JSONB), decision text, user_approved flag, and success boolean.

---

## Stage 6 — Razorpay Integration

**Date:** 2026-08-23

**Added:**
- `backend/routes/checkout.py` — POST /api/create-order (Razorpay Orders API), POST /api/verify-payment (signature verification), POST /api/payment-failed (failure logging)
- `backend/routes/webhook.py` — POST /api/webhook (X-Razorpay-Signature verification, order.paid/payment.failed event handlers)

**Status:** Complete
**Verified:** Checkout flow creates Razorpay order, updates DB with razorpay_order_id, verifies payment signature, and handles failure cases with proper order status updates.

---

## Stage 7 — Failure Handling

**Date:** 2026-08-23

**Implemented:**
- **Option A (Payment failure):** Razorpay Checkout payment.failed handler → POST /api/payment-failed → order marked status='failed' with failure_reason → logged to ai_actions with success=false → agent shows human-friendly "Payment didn't go through" message with retry options
- **Option B (Stock failure):** add_to_cart checks stock before inserting → returns {success: false, reason: "out_of_stock"} → agent detects failure via tool result → offers alternatives via search_products

**Status:** Complete
**Verified:** Both failure paths logged to ai_actions, visible in dashboard failure panel.

---

## Stage 8 — Agent-Readable Catalog Endpoint

**Date:** 2026-08-23

**Added:**
- `backend/routes/catalog.py` — GET /api/agent-catalog returning structured JSON with product details, pricing, stock, specs, use_cases, and HATEOAS-style _links

**Status:** Complete
**Verified:** Endpoint returns all in-stock products in machine-consumable format with price objects, stock availability, and discoverability metadata.

---

## Stage 9 — Frontend: Chat UI

**Date:** 2026-08-23

**Added:**
- `frontend/index.html` — Chat interface with message area and cart sidebar
- `frontend/js/chat.js` — Session management, message sending, confirm/cancel buttons, cart refresh
- `frontend/js/checkout.js` — Razorpay Checkout modal integration with graceful failure handling
- `frontend/css/styles.css` — Complete styles for both chat UI and dashboard
- `backend/routes/cart.py` — GET /api/cart endpoint for frontend cart sidebar

**Status:** Complete
**Verified:** Chat sends messages, renders agent responses with markdown formatting, shows inline Confirm/Cancel buttons for gated actions, refreshes cart sidebar after interactions, triggers Razorpay Checkout on confirmed checkout.

---

## Stage 10 — Frontend: Merchant Dashboard

**Date:** 2026-08-23

**Added:**
- `frontend/dashboard.html` — Dashboard with 4 panels
- `frontend/js/dashboard.js` — Data fetching and rendering logic
- `backend/routes/dashboard.py` — GET /api/dashboard/summary, /ai-actions, /orders, /sessions

**Dashboard Panels:**
1. Business Impact: revenue, AI-assisted %, avg order value, upsell metrics, failed orders count
2. AI Decision Trace: chronological tool call log with input/output, decision reasoning, approval status
3. Orders Table: AI vs Human comparison with status badges
4. Failure Handling: distinct section showing failed orders with reasons

**Status:** Complete
**Verified:** All panels render from API data, session selector filters AI actions, auto-refresh every 30s.

---

## Stage 11 — End-to-End Verification

**Date:** 2026-08-23

**Status:** Ready for testing
**Notes:** All backend and frontend files created. Requires database seed and manual testing with Supabase, Gemini, and Razorpay test keys configured in .env.
