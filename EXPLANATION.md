# EXPLANATION — ShopMind AI

A running, human-readable log explaining WHY each significant piece of
ShopMind AI was built the way it was. Read this to understand the entire
project without re-reading the code.

---

## Stage 1 — Database Schema + Seed Data

### What this part does
Defines the data layer: five PostgreSQL tables that store everything the
system needs — products, shopping carts, orders, an AI decision audit
trail, and co-purchase patterns.

### Why it was built this way

**Direct Postgres via asyncpg (not Supabase REST API):**
The hackathon requirement is that the LLM has NO built-in product knowledge
and must query a real database. Using asyncpg gives us raw SQL control,
which is essential for complex queries (array overlap for use_case matching,
JOINs for co-purchase lookups, etc.). The Supabase REST API would add an
unnecessary abstraction layer and limit query flexibility.

**Separate schema.sql file:**
Keeps DDL readable and portable. The seed script reads and executes it,
but a human can also run it directly via psql. All CREATE statements use
`IF NOT EXISTS` for safe re-runs.

**use_case as TEXT[] (Postgres array):**
Products can have multiple use cases (e.g., a laptop can be for both
"gaming" and "ml"). Using a Postgres array lets us query with the `&&`
(array overlap) operator — this is more natural and faster than a junction
table for this scale.

**source field on cart_items:**
Tracks whether each cart addition was an AI recommendation, an AI upsell,
or an organic user action. This powers the merchant dashboard's "AI vs
Human" comparison — a direct hackathon judging criterion.

**ai_actions table design:**
Every tool call the agent makes gets a row here: what tool, what input,
what output, what the agent decided, whether the user approved it, and
whether it succeeded. This is the "audit trail" the hackathon explicitly
requires. The `decision` field is free-text so the agent's reasoning can
be captured without a rigid enum.

**co_purchase_history as a materialized pattern:**
Rather than computing "frequently bought together" on the fly (which
would require order history we don't have at seed time), we pre-seed
realistic co-purchase counts. The `get_complementary_products` tool
queries this table. In production, this would be populated from actual
order data.

**Seed data realism:**
25 laptops span ₹40,990–₹1,19,990 with realistic Indian market brands,
genuine GPU/CPU model numbers, and meaningful use_case tags. The 10
accessories are actual products. Co-purchase counts are generated
programmatically based on logical rules (gaming laptops pair with cooling
pads and gaming mice; coding laptops pair with productivity mice and
monitors).

### Limitations
- No full-text search index — for 35 products, sequential scan is fine
- Co-purchase data is synthetic — in production, update from real orders
- No migrations framework (Alembic) — overkill for hackathon

---

## Stage 2 — Tool Functions

### What this part does
Seven async Python functions that the LLM agent calls to query the
database. These are the agent's "hands" — its only way to learn about
products, carts, and orders.

### Why it was built this way

**All tools take asyncpg.Connection as first arg:**
This keeps them framework-agnostic — they're just functions, not tied to
FastAPI routes. The agent loop passes the connection; the tools don't
know or care about HTTP.

**Dynamic SQL building in search_products:**
Instead of a fixed query, search_products builds WHERE clauses based on
which parameters the LLM provides. The LLM can search by budget only,
use_case only, RAM only, or any combination. This is what makes the
agent genuinely flexible — it's not following a script.

**Stock check before add_to_cart (not decrement):**
Stock is checked but NOT decremented on add-to-cart — only on successful
payment. This prevents phantom stock reduction from abandoned carts.
When stock is insufficient, the function returns a structured error that
the agent can interpret and respond to (the stock failure handling from
Section 6).

**Neutral JSON tool definitions:**
Tool schemas are defined in a provider-agnostic JSON format. This means
swapping from Gemini to Claude or GPT-4 only requires writing a new
adapter — the tool definitions and functions stay the same.

---

## Stage 3 — Agent Loop + Gemini Adapter

### What this part does
The core AI brain: a loop that sends conversation history to Gemini,
gets back either text or tool calls, executes tools, feeds results back,
and repeats until the LLM produces a final text response.

### Why it was built this way

**Provider-agnostic loop:**
`agent_loop.py` doesn't import anything from Google. It calls
`adapter.call_llm()` which returns a generic `LLMResponse` with `text`
or `tool_calls`. A different adapter (e.g., `claude_adapter.py`) could
be swapped in by changing one import.

**6-call cap:**
Section 5 requires capping at 6 tool calls per user turn to prevent
runaway loops. The loop counts calls and returns a graceful fallback
message if the limit is hit.

**System prompt injection:**
The system prompt explicitly tells the LLM it has NO built-in product
knowledge and MUST call tools. This is the key architectural constraint
from Section 2 — without it, the LLM would hallucinate products.

**asyncio.to_thread for Gemini calls:**
The google-genai SDK's `generate_content` is synchronous. We wrap it
in `asyncio.to_thread()` so it doesn't block FastAPI's event loop.

### How it connects
- Takes conversation history + user message as input
- Calls GeminiAdapter for LLM inference
- Dispatches tool calls to TOOL_DISPATCH (from tools.py)
- Logs every call via logging_middleware.py
- Returns AgentResponse (text, pending_confirmation, or error)

---

## Stage 4 — Confirmation Gate

### What this part does
Two tools — `add_to_cart` and `initiate_checkout` — cannot execute
without explicit user confirmation. When the LLM wants to call them,
the loop pauses and returns a `pending_confirmation` response. The
frontend shows Confirm/Cancel buttons. Only after human click does
execution proceed.

### Why it was built this way

**This is a hackathon judging criterion:**
"Every money action explainable, bounded and gated." The confirmation
gate is the "gated" part. It proves the AI cannot spend the user's money
autonomously.

**PendingConfirmation data structure:**
Stores action_id (UUID), tool_name, tool_args, and a human-readable
description. The action_id prevents replay attacks — each confirmation
is unique.

**In-memory session store:**
For a hackathon demo, we store conversation histories and pending actions
in a Python dict keyed by session_id. Production would use Redis or a
database, but this is sufficient for single-server demo.

### Tradeoffs
- In-memory state is lost on server restart
- Only one pending action per session at a time (simplification)

---

## Stage 5 — ai_actions Logging

### What this part does
Every tool call — successful or failed, confirmed or auto-executed —
gets logged to the `ai_actions` table. This creates a complete audit
trail that powers the merchant dashboard's "AI Decision Trace" panel.

### Why it was built this way

**This is the other half of the judging criterion:**
"Every money action explainable." The ai_actions log IS the explanation.
A judge can look at any session and see exactly what the agent did, why
it did it, whether the user approved it, and whether it succeeded.

**decision field captures reasoning:**
Not just "called search_products" but "Called search_products to gather
information" or "Agent wants to add Product #14 to cart. Awaiting user
confirmation. → User confirmed. Result: success." This makes the trace
readable by humans, not just machines.

**Three logging functions:**
- `log_tool_call`: INSERT — used for every tool execution
- `log_confirmation_result`: UPDATE — used when user confirms/cancels
- `get_session_actions`: SELECT — used by the dashboard

---

## Stage 6 — Razorpay Integration

### What this part does
Integrates with Razorpay's Orders API and Checkout.js to handle real
(test-mode) payments. Creates Razorpay orders, opens the Checkout modal,
verifies payment signatures, and handles webhooks.

### Why it was built this way

**Two-phase verification:**
1. Frontend callback: verifies `razorpay_signature` immediately after
   payment to show success/failure to the user.
2. Webhook: server-to-server verification for reliability (handles cases
   where the user's browser closes mid-payment).

**Raw body verification for webhooks:**
The webhook handler uses `await request.body()` to get raw bytes before
JSON parsing. This is critical — re-serializing parsed JSON would change
whitespace and break HMAC verification.

**Lazy-initialized Razorpay client:**
Avoids startup errors when Razorpay keys aren't configured yet (e.g.,
during development of non-payment features).

---

## Stage 7 — Failure Handling

### What this part does
Deliberately builds and gracefully handles two failure scenarios:

**Option A — Payment failure:**
When Razorpay Checkout reports a payment failure, the system:
1. Updates the order to `status='failed'` with the specific failure_reason
2. Logs to ai_actions with `success=false`
3. Shows a clear, human-friendly message (not a raw error)
4. Offers retry or alternative payment method
5. The failure appears distinctly in the dashboard

**Option B — Stock failure mid-flow:**
When a product goes out of stock between recommendation and add-to-cart:
1. `add_to_cart` detects `stock < quantity`
2. Returns `{success: false, reason: "out_of_stock"}`
3. Agent interprets the tool result and explains the situation
4. Agent proactively calls `search_products` to find alternatives
5. Logged to ai_actions with appropriate decision text

### Why it was built this way

**Explicitly required by Section 6:**
"At least one failure scenario must be deliberately built and gracefully
handled, not just wrapped in a generic try/catch." Both failures produce
specific, structured error responses that the agent interprets — not
generic error pages.

**Visible in the dashboard:**
Failed orders have a dedicated panel in the merchant dashboard with red
styling and failure reasons. This proves to judges that the system
handles failure, not just happy paths.

---

## Stage 8 — Agent-Readable Catalog Endpoint

### What this part does
`GET /api/agent-catalog` returns the full product catalog in a structured
JSON format designed for OTHER AI agents to query programmatically.

### Why it was built this way

**Required by the track:**
The hackathon requires addressing "making the merchant transactable by
other AI agents/buyers, not just humans." This endpoint is that
demonstration.

**Machine-consumable schema:**
Products include structured `price` objects (amount + currency + display),
`stock` objects (available boolean + quantity), nested `specs`, and
flat `use_cases` arrays. This is designed for zero-ambiguity programmatic
consumption.

**HATEOAS-style _links:**
The response includes `_links` with URLs for related endpoints (search,
cart, docs). This helps external agents discover what actions are
available beyond just reading the catalog.

**_meta with supported_actions:**
Lists what tools/actions are available, so an external AI agent can
understand the merchant's capabilities without reading documentation.

### Limitations
- No authentication — in production, would add API keys for external
  agents
- No pagination — fine for 35 products, would need cursor-based
  pagination for large catalogs
- No full agent-to-agent protocol — this is a clear, honest, working
  example, not a complete multi-agent system

---

## Stage 9 — Chat UI

### What this part does
A browser-based chat interface where customers interact with ShopMind AI.
Features a message area with agent/user bubbles, inline Confirm/Cancel
buttons for gated actions, a cart sidebar showing current items with
source attribution, and Razorpay Checkout integration.

### Why it was built this way

**Vanilla HTML/CSS/JS (no React/Vue):**
For a hackathon, build speed matters more than framework elegance. No
build step means instant iteration. The CSS uses custom properties
(variables) for a clean, consistent design.

**Session via localStorage UUID:**
No authentication — the session_id is a UUID generated on first visit
and stored in localStorage. Simple, sufficient for demo purposes.

**Inline confirmation buttons:**
When the agent returns a `pending_confirmation` response, the chat
renders Confirm and Cancel buttons directly in the message bubble. This
makes the gating visible and interactive — a judge can see the human is
in control.

**Cart sidebar with source badges:**
Each cart item shows whether it was "AI Recommended", "AI Upsell", or
"Added by you" — visually demonstrating the AI attribution tracking that
powers the dashboard metrics.

---

## Stage 10 — Merchant Dashboard

### What this part does
A dashboard showing merchants what the AI agent actually did, with four
panels designed to address the hackathon judging criteria.

### Why it was built this way

**Four panels, each serving a judging criterion:**

1. **Business Impact:** Revenue, AI-assisted %, upsell metrics — proves
   "growing merchant revenue via an agent."

2. **AI Decision Trace:** Chronological log of every tool call with
   input/output details, agent reasoning, and approval status — proves
   "every money action explainable."

3. **Orders Table (AI vs Human):** Shows which orders were AI-assisted
   vs organic, with upsell acceptance — proves the agent adds measurable
   value.

4. **Failure Handling:** Dedicated section for failed orders — proves
   "one failure handled gracefully" per Section 6.

**Auto-refresh every 30 seconds:**
The dashboard updates live, so during a demo you can have the chat open
in one tab and the dashboard in another, and see actions appear in
real-time.

**Session selector:**
Lets you drill into a specific conversation's decision trace, or view
all sessions combined.

---

## How the Judging Criteria Are Met

| Criterion | Where It's Demonstrated |
|---|---|
| "Every money action explainable" | ai_actions table + Dashboard AI Decision Trace panel |
| "Bounded" | 6-call cap per turn in agent_loop.py |
| "Gated" | Confirmation gate on add_to_cart and initiate_checkout |
| "Show the audit trail" | Dashboard AI Decision Trace + orders table |
| "One failure handled gracefully" | Payment failure (Option A) + Stock failure (Option B) + Stock-at-payment failure (Option C) |
| "Growing merchant revenue via agent" | Dashboard Business Impact panel, source attribution on cart items, upsell metrics now populated |
| "Transactable by other AI agents" | GET /api/agent-catalog endpoint |

---

## Audit & Enhancement Pass (2026-08-24)

### Stage A — Populate upsell/attribution fields on orders

**What was wrong:** The `orders` table had columns `upsell_accepted`,
`upsell_amount`, `ai_recommended_product_id`, and
`actual_product_purchased_id` defined in `schema.sql` and queried by
`/api/dashboard/summary`, but `initiate_checkout` only wrote
`session_id, total, ai_assisted, status`. The Business Impact panel
always showed zero upsell revenue.

**Why it mattered:** The upsell metrics are a core part of the "growing
merchant revenue" judging criterion. Without them, the dashboard couldn't
demonstrate that the AI actually drove upsell revenue.

**What changed:**
- `initiate_checkout` now inspects `cart_items` by `source` field:
  items with `source='ai_upsell'` → `upsell_accepted=true`,
  `upsell_amount` = sum of those items' subtotals. Items with
  `source='ai_recommendation'` → first one's `product_id` set as
  `ai_recommended_product_id`.
- `verify_payment` now sets `actual_product_purchased_id` from the cart
  items at payment time (preferring `ai_recommendation`-sourced items).
- `verify_payment` success now also logged to `ai_actions` for audit
  completeness (previously only failures were logged).

**Assumption:** `ai_recommended_product_id` and `actual_product_purchased_id`
are single-valued columns in the schema. When there are multiple matching
cart items, we pick the first one (ordered by `ai_recommendation` source
first, then `created_at`). A production system would use a junction table
for multi-product orders.

---

### Stage B — Stop leaking all customers' orders to the client

**What was wrong:** `checkout.js`'s `handleCheckoutConfirmed()` called
`GET /api/dashboard/orders` — an unauthenticated endpoint returning every
order for every session — just to find the current session's new order.

**Why it mattered:** Any customer's browser could see every other
customer's session_id, order total, and status. This is a data leak that
would be a serious vulnerability in production and could lose marks under
the "bounded" criterion.

**What changed:**
- Added `tool_result: Optional[dict]` to `AgentResponse` and
  `ChatResponse` so the confirmed tool's output (including `order_id`)
  flows directly through the API response.
- `execute_confirmed_action` now captures the tool result and threads
  it through every return path.
- `checkout.js` reads `order_id` and `total` from the `tool_result`
  field directly — no network call to `/api/dashboard/orders`.
- `/api/dashboard/orders` remains available for the merchant dashboard
  (it's only the customer-facing flow that no longer calls it).

---

### Stage C — Decrement stock on successful payment

**What was wrong:** `add_to_cart` checked stock but never decremented it.
Neither `verify_payment` nor the webhook `order.paid` handler decremented
stock. Catalog stock numbers never reflected real sales.

**Why it mattered:** Without stock decrement, the catalog would show
infinite availability and the out-of-stock failure scenario would never
trigger organically. This undermines the "bounded" and "failure handling"
criteria.

**What changed:** `verify_payment` now decrements `products.stock` for
each cart item after successful signature verification, using
`UPDATE ... SET stock = stock - $1 WHERE id = $2 AND stock >= $1`.

**Design choice:** Stock decrement happens in `verify_payment` (the
client-facing endpoint), NOT in the webhook `order.paid` handler. This
avoids double-decrement on the same order when both the client callback
and the webhook fire. The webhook handler only updates order status as a
fallback.

**Edge case:** If a race condition causes insufficient stock at payment
time (stock was available when added to cart, but was purchased by another
session between then and payment), the order is marked `failed` with
`failure_reason='insufficient_stock_at_payment'` and logged to
`ai_actions`. This is a third failure scenario on top of Options A and B.

---

### Stage D — Fix CORS credentials misconfiguration

**What was wrong:** `main.py` set `allow_origins=["*"]` together with
`allow_credentials=True`. Per the CORS specification, this combination is
invalid — browsers will reject it (they require a specific origin, not
wildcard, when credentials are enabled).

**Why it mattered:** In practice this meant cross-origin requests with
credentials would fail in compliant browsers. Since the app doesn't
actually use cookies (session_id is passed in request bodies), the
`allow_credentials=True` was unnecessary.

**What changed:** Set `allow_credentials=False`. No functional impact
since session_id was never passed via cookies.

---

### Stage E — Validate order ownership on payment creation

**What was wrong:** `POST /api/create-order` accepted a bare `order_id`
without any check that it belonged to the requesting session. Any request
could create a Razorpay order against any internal order.

**Why it mattered:** This violates the "bounded" criterion — actions
should be scoped to the session that created them.

**What changed:** The endpoint now requires `session_id` in the request
body and queries `orders WHERE id = $1` then checks
`row["session_id"] != session_id` → returns 403. The frontend's
`openRazorpayCheckout` now includes `session_id: sessionId` in the
request body.

---

### Stage F — Demo-ability and dashboard clarity

**What was wrong (1):** The Option B failure scenario (out-of-stock)
required manual SQL to trigger during a demo. **Fix:** Added
`POST /api/admin/simulate-stockout` and `POST /api/admin/restore-stock`
endpoints, clearly gated as dev/demo-only in code and docs. NOT for
production use.

**What was wrong (2):** The `decision` field in the AI Decision Trace
panel was rendered in plain small text, not standing out from the
Input/Output details. Since this field is the project's strongest
evidence for the "explainable" judging criterion, it should be visually
prominent. **Fix:** Styled the decision text with larger font, semi-bold
weight, accent background highlight, and a left border, plus a 💡 icon
prefix.

**What was wrong (3):** The "how each judging criterion is met" narrative
was only in EXPLANATION.md, which judges might not read. **Fix:** Added
the same table to the top of README.md with a "Where to Look" column
linking to specific UI locations.

### Tradeoffs and remaining limitations

- **In-memory session state** is still lost on server restart. Production
  would use Redis.
- **Stock decrement is not inside a DB transaction** spanning all cart
  items — if item 2 fails after item 1 was decremented, item 1's stock
  is not rolled back. For a hackathon demo this is acceptable; production
  would use `BEGIN/COMMIT/ROLLBACK`.
- **`actual_product_purchased_id`** is single-valued per order. Multi-item
  orders only record the first (prioritizing AI-recommended) product.

---

## Stage G — Merchant/Admin Authentication Architecture

### Problem Solved
Previously, `/api/dashboard/*` endpoints (summary, ai-actions, orders, sessions) and the frontend dashboard were completely open. Anyone visiting `/dashboard.html` could see all customer orders, session IDs, and AI decision traces.

### Design Decisions & Implementation

1. **Bcrypt Password Hashing:**
   Admin passwords are never stored or logged in plaintext. They are hashed using bcrypt with salted rounds via the standard `bcrypt` library. Minimum password length (8 characters) is enforced on signup.

2. **Gated Signup (`ADMIN_SIGNUP_CODE`):**
   Since ShopMind AI is a merchant-facing demo (not multi-tenant SaaS), self-registration is protected by a shared secret (`ADMIN_SIGNUP_CODE`). Requests with incorrect signup codes are rejected with `403 Forbidden`. Duplicate email attempts are caught and returned as `409 Conflict`.

3. **Stateless JWT Tokens:**
   On successful login, the server issues a signed JWT token containing the admin's email and a 24-hour expiration timestamp (`exp`). The token is signed using HMAC-SHA256 with `JWT_SECRET`.

4. **Logout Mechanism:**
   We chose a **stateless JWT with client-side discard** for logout. When an admin logs out, the client clears the token from `sessionStorage` and displays the login view. Because this is a single-tenant hackathon architecture with reasonable token expiry, a stateful server-side token revocation / blacklist database was intentionally omitted to avoid unnecessary overhead.

5. **Separation of Concerns (Privileged vs Anonymous):**
   - **Protected (Gated):** All `/api/dashboard/*` routes enforce the `get_current_admin` FastAPI dependency and return `401 Unauthorized` if the token is missing, expired, or tampered with.
   - **Customer-Facing (Open):** Customer routes (`/api/chat`, `/api/cart`, `/api/create-order`, `/api/verify-payment`, `/api/agent-catalog`) remain completely anonymous without login requirements.
   - **Demo Endpoints:** `/api/admin/simulate-stockout` and `/api/admin/restore-stock` are kept open for convenient live hackathon demonstrations.

---

## Stage H — Lightweight Customer Cart Recovery Architecture

### Problem Solved
Because anonymous shopping sessions are keyed by a UUID stored in browser `localStorage`, customers who switched devices or cleared storage lost access to their cart. We needed a lightweight way to restore carts across devices without forcing customers to create full accounts or remember passwords.

### Design Decisions & Implementation

1. **Reuse Checkout Prefill Information:**
   Razorpay Checkout requires customer contact details (name, email, phone). Rather than creating a separate registration system, ShopMind AI captures these details at checkout time and stores them in `customer_identities (session_id, email, phone, name, updated_at)`.

2. **Lightweight Recovery via `/api/session/recover`:**
   A customer on a new device can click "🔄 Recover" in the cart sidebar, enter their email or phone number, and retrieve their previous `session_id`. The frontend seamlessly updates its active session and reloads the cart.

> [!IMPORTANT]
> **Convenience vs. Security Boundary:**
> Cart recovery is intentionally designed as a **low-stakes convenience feature, NOT a secure identity verification system**. Anyone who knows a customer's email or phone number can recover their anonymous cart. In an e-commerce context, a shopping cart contains no payment information or private PII; real payment actions still require the Razorpay payment gateway with OTP/3D-Secure authentication. This tradeoff provides seamless UX without adding login friction.

---

## Stage I — Cart Item Removal & Empty Cart (Dual-Path Architecture)

### Problem Solved
Customers and the AI agent could previously only add items to the cart or view it. There was no mechanism to remove an unwanted item or clear the cart.

### Design Decisions & Implementation

1. **Dual-Path Capability:**
   - **Conversational (AI-driven):** The agent has access to `remove_from_cart(session_id, product_id)` and `clear_cart(session_id)` tools. When a customer says *"remove the mouse"* or *"empty my cart"*, the LLM invokes the respective tool, reasons over the result, and responds naturally.
   - **Direct UI (Manual):** Each item in the cart sidebar features a `×` remove button, and a `🗑️ Empty Cart` button is available at the bottom. These call `DELETE /api/cart/item` and `DELETE /api/cart` directly, bypassing LLM roundtrips for speed.

2. **Confirmation Gate Policy:**
   Per the original project specification, confirmation gates are strictly reserved for **money-adjacent and cart-growing actions** (`add_to_cart`, `initiate_checkout`). Removing an item or clearing the cart reduces liability and does not trigger the confirmation gate modal; it executes immediately.

3. **Audit Trail Integrity (`user_direct` vs `shopmind_v1`):**
   To keep the audit trail accurate for judges, direct UI actions are still logged to `ai_actions`, but with `agent_name = 'user_direct'` and `decision = 'Direct UI action — no AI reasoning involved'`. This allows the merchant dashboard to distinguish between actions taken autonomously by the AI and manual clicks by the customer.

4. **UX Safeguard for Destructive Actions:**
   For `clear_cart` from the UI, a lightweight browser `confirm("Remove all items from your cart?")` dialog is shown before execution as a basic UX safety check.

---

## Stages J1–J6 — Campaign Orchestrator (Proactive Revenue Recovery)

### What this feature does

The Campaign Orchestrator is a **proactive agent** — separate from the reactive chat agent — that periodically scans for abandoned carts and uses LLM reasoning to decide whether to send a recovery nudge to the customer.  Unlike a simple scheduled email blast ("always email after 1 hour"), the orchestrator evaluates each abandoned cart individually and makes a genuine reasoning decision:

- **Should it nudge at all?** Low-value carts (e.g., a single Rs.1,299 accessory) or very recently abandoned carts might not warrant intervention.
- **What kind of nudge?** A plain reminder ("You left items in your cart!"), a discount offer (e.g., 5% for returning customers, 10% for first-time high-value buyers), or no action.
- **What channel?** Email if the customer has one on file, SMS if only a phone number is available.

Every decision — including deliberate "no action" decisions — is logged to both `campaign_actions` (purpose-built table) and `ai_actions` (shared audit trail), so the merchant can see exactly why the orchestrator acted (or didn't) for every cart.

### Why it was built this way

**1. Genuine LLM reasoning, not a fixed rule:**

This is the core design principle.  A hardcoded rule like "email everyone after 1 hour with 10% off" is:
- Not intelligent (wastes margin on customers who'd return anyway).
- Not explainable (the decision text would be identical every time).
- Not what the hackathon asks for (the track brief specifically calls out "Campaign orchestrator" as an example of agent-driven automation).

Instead, the orchestrator sends each cart's full context (items, value, age, customer type) to the LLM with a carefully engineered system prompt that acts as a decision framework.  The LLM produces a unique, readable reasoning for each cart — e.g., *"Cart worth Rs.61,990, abandoned 2 hours ago, first-time customer — offering a plain email reminder without discount since the cart value doesn't yet justify margin loss."*

**2. Same tool-calling pattern as the chat agent:**

The orchestrator uses the same architectural pattern as the rest of ShopMind:
- Neutral JSON-Schema tool definitions (`campaign_tool_definitions.py`) → Gemini adapter conversion.
- Async tool functions (`campaign_tools.py`) following `async func(conn, **kwargs) -> dict`.
- LLM calls via the shared `GeminiAdapter`.
- Audit logging via the shared `log_tool_call()` helper.

This means the campaign orchestrator's decisions show up in the **same `ai_actions` audit trail** that the chat agent, checkout flow, and direct UI actions all use — just with `agent_name = 'campaign_orchestrator'` instead of `'shopmind_v1'`.  A dashboard filter can easily separate them.

**3. Separate `campaign_actions` table:**

While `ai_actions` is the generic audit log (every tool call from every agent), `campaign_actions` is a purpose-built table optimized for campaign-specific queries: "show me all nudges sent this week, sorted by cart value."  The dashboard's Campaign Orchestrator panel reads from this table directly, avoiding the need to parse `ai_actions.output` JSON.

The two tables are linked: `campaign_actions.ai_action_log_id` references `ai_actions.id`, so you can always trace a campaign decision back to its full audit trail entry.

**4. Simulated sends (no real email/SMS):**

Since there's no real email/SMS provider in this hackathon build, nudges are **simulated** — the orchestrator logs exactly what would have been sent, to whom, and why.  The `simulated_channel` column records whether it would be email or SMS.  This keeps the system safe for demo purposes while proving the reasoning works end-to-end.

**5. No confirmation gate (but fully auditable):**

Unlike the chat agent's `add_to_cart` and `initiate_checkout` tools, campaign nudges don't go through a human confirmation gate because:
- They don't execute real payments or mutations.
- They're simulated sends, not actual dispatches.
- The merchant can review all decisions in the dashboard after the fact.

However, every decision is **prominently logged** — the `decision` field contains the LLM's full reasoning, visible in both the Campaign Orchestrator dashboard panel and the AI Decision Trace.

**6. Cooldown and cost controls:**

- **Cooldown:** Sessions that have been nudged in the last 6 hours are excluded from future scans (`COOLDOWN_HOURS = 6`), preventing spam.
- **Cart cap:** Each scan evaluates at most 20 carts (`MAX_CARTS_PER_RUN = 20`), preventing runaway LLM costs.
- **Discount cap:** The system enforces a hard 15% maximum discount (`MAX_DISCOUNT_PERCENT = 15`), regardless of what the LLM suggests.

### How it connects to the rest of the system

| Component | Connection |
|---|---|
| `ai_actions` table | Campaign decisions logged with `agent_name='campaign_orchestrator'`, visible alongside chat agent entries in the AI Decision Trace panel. |
| `log_tool_call()` | Reuses the existing logging helper with the new optional `agent_name` parameter (backward-compatible). |
| `GeminiAdapter` | Uses the same adapter for LLM calls; tool definitions converted via the same `convert_tool_definitions()` path. |
| `get_current_admin` | Campaign API routes are protected by the same JWT-based admin auth used by all dashboard endpoints. |
| Merchant Dashboard | New panel added alongside existing Business Impact, AI Decision Trace, Orders, and Failure Handling panels. |

### How this satisfies the hackathon track brief

> **"Campaign orchestrator" example direction:**

This feature directly implements the campaign orchestrator use case mentioned in the track brief.  It proves that the ShopMind agent can **proactively grow revenue** — not just respond reactively in chat — and that even a "marketing" action goes through the same **explainable, logged, reasoning pattern** as a checkout action.

The key differentiator is that the orchestrator's decisions are **visible**:
- The merchant sees WHY a nudge was (or wasn't) sent, in plain English.
- The audit trail (`ai_actions`) shows campaign orchestrator entries alongside chat agent entries, proving a unified observability layer.
- The dashboard panel shows stats (nudges sent vs. skipped) alongside detailed per-cart reasoning.

### Assumptions and limitations

1. **No real email/SMS:** Nudges are simulated.  In production, `record_campaign_decision` would integrate with SendGrid, Twilio, or similar.
2. **Single-connection execution:** The orchestrator runs on a single database connection per scan.  For high-volume stores, this would need connection pooling and potentially async worker queues.
3. **Cooldown is session-based, not customer-based:** If the same customer has multiple sessions, each is tracked independently.  A production system would deduplicate by customer identity.
4. **LLM latency:** Each cart requires a separate LLM call (~1-3 seconds), so scanning 20 carts takes ~20-60 seconds.  This is acceptable for manual/demo use but would need batching or streaming for a real cron job at scale.
5. **Returning customer detection:** Uses `customer_identities` to match email/phone across sessions.  Customers who don't provide contact info are treated as "unknown" (not first-time, not returning).

