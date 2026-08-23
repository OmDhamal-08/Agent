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
| "One failure handled gracefully" | Payment failure (Option A) + Stock failure (Option B) |
| "Growing merchant revenue via agent" | Dashboard Business Impact panel, source attribution on cart items |
| "Transactable by other AI agents" | GET /api/agent-catalog endpoint |
