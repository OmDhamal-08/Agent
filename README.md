# 🧠 ShopMind AI — Agentic Commerce Assistant

> Razorpay Hackathon Submission · Track 1: AI Growth & Agentic Commerce

ShopMind AI is a customer-facing AI shopping agent for laptops, powered by a
genuine LLM agent loop (Gemini), backed by a real Postgres database, with
Razorpay payment integration and a merchant dashboard showing what the agent
actually did.

## How Each Judging Criterion Is Met

| Criterion | How It's Met | Where to Look |
|---|---|---|
| **Every money action explainable** | Every tool call (success, failure, confirmation) logged to `ai_actions` with `decision` text describing the agent's reasoning | Dashboard → AI Decision Trace panel |
| **Bounded** | Agent loop capped at 6 tool calls per user turn — prevents runaway spending | `agent_loop.py` line 31: `MAX_TOOL_CALLS_PER_TURN = 6` |
| **Gated** | `add_to_cart` and `initiate_checkout` require explicit Confirm/Cancel click before executing | Chat UI → inline Confirm/Cancel buttons |
| **Audit trail** | Complete chronological log of every agent action with inputs, outputs, reasoning, and approval status | Dashboard → AI Decision Trace + Orders Table |
| **Failure handled gracefully** | (A) Payment failure → clear message + retry options + logged to ai_actions. (B) Out-of-stock → agent explains and searches alternatives | Dashboard → Handled Failures panel |
| **Growing merchant revenue** | AI recommendations and upsells tracked via `source` field → upsell metrics on dashboard | Dashboard → Business Impact (Upsell Revenue, AI-Assisted %) |
| **Transactable by other AI agents** | `GET /api/agent-catalog` — machine-readable catalog with structured pricing, stock, specs, and HATEOAS links | http://localhost:8000/api/agent-catalog |

> For detailed design rationale behind every component, see [EXPLANATION.md](EXPLANATION.md).

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual keys:
#   DATABASE_URL     — Supabase Postgres connection string
#   GEMINI_API_KEY   — Google Gemini API key
#   RAZORPAY_KEY_ID  — Razorpay test-mode Key ID (rzp_test_...)
#   RAZORPAY_KEY_SECRET — Razorpay test-mode Key Secret
#   RAZORPAY_WEBHOOK_SECRET — Webhook signing secret
#   JWT_SECRET — long random secret for merchant dashboard tokens
#   ADMIN_SIGNUP_CODE — private code required to create merchant accounts
#   CORS_ALLOW_ORIGINS — optional comma-separated browser origins
```

### 3. Seed the database

```bash
python -m backend.seed
```

### 4. Run the server

```bash
uvicorn backend.main:app --reload --port 8000
```

### 5. Open in browser

- **Chat UI:** http://localhost:8000/
- **Merchant Dashboard:** http://localhost:8000/dashboard.html
- **Agent Catalog API:** http://localhost:8000/api/agent-catalog
- **API Docs:** http://localhost:8000/docs

## Architecture

```
Customer ↔ Chat UI ↔ FastAPI ↔ Agent Loop ↔ Gemini LLM
                                   ↕
                              Tool Functions ↔ Postgres (Supabase)
                                   ↕
                           Razorpay Orders API
```

- **LLM has NO product knowledge** — learns everything by calling tools
- **Agent loop** decides which tool to call (max 6 per turn)
- **add_to_cart** and **initiate_checkout** require human confirmation
- **Every tool call** logged to `ai_actions` table (audit trail)
- **Payment failures** handled gracefully with user-friendly messages
- **Stock decremented** on successful payment verification

## Key Files

| File | Purpose |
|------|---------|
| `backend/agent_loop.py` | Core agent loop — the brain |
| `backend/tools.py` | 7 tool functions that query Postgres |
| `backend/adapters/gemini_adapter.py` | Gemini API adapter (swappable) |
| `backend/routes/chat.py` | Chat API with confirmation gating |
| `backend/routes/checkout.py` | Razorpay payment flow + stock decrement |
| `backend/routes/catalog.py` | Agent-readable catalog (GET /api/agent-catalog) |
| `backend/routes/dashboard.py` | Merchant dashboard API |
| `frontend/index.html` | Chat UI |
| `frontend/dashboard.html` | Merchant dashboard |
| `EXPLANATION.md` | Design reasoning for judges |
| `CHANGELOG.md` | Stage-by-stage build log |

## Hackathon Judging Criteria

See [EXPLANATION.md](EXPLANATION.md) for detailed coverage of each criterion.
