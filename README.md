# ShopMind AI — Agentic Commerce Assistant

> Razorpay Hackathon Submission · Track 1: AI Growth & Agentic Commerce

ShopMind AI is a customer-facing AI shopping agent for laptops, powered by a
genuine LLM agent loop (Gemini), backed by a real Postgres database, with
Razorpay payment integration and a merchant dashboard showing what the agent
actually did.

## How Each Judging Criterion Is Met

| Criterion | How It's Met | Where to Look |
|---|---|---|
| **Every money action explainable** | Every tool call logged to `ai_actions` with `decision` text | Dashboard → AI Decision Trace |
| **Bounded** | Agent loop capped at 6 tool calls per turn | `agent_loop.py` `MAX_TOOL_CALLS_PER_TURN = 6` |
| **Gated** | `add_to_cart` and `initiate_checkout` require Confirm/Cancel click | Chat UI → inline buttons |
| **Audit trail** | Complete log of every agent action with inputs, outputs, reasoning | Dashboard → AI Decision Trace + Orders |
| **Failure handled gracefully** | Payment failure → retry options. Out-of-stock → alternatives | Dashboard → Failures panel |
| **Growing merchant revenue** | AI recommendations tracked via `source` field → upsell metrics | Dashboard → Business Impact |
| **Transactable by other AI agents** | Machine-readable catalog with HATEOAS links | `GET /api/agent-catalog` |

## Quick Start (Local)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your keys (see .env.example for details)
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
- **Dashboard:** http://localhost:8000/dashboard.html
- **Agent Catalog:** http://localhost:8000/api/agent-catalog
- **API Docs:** http://localhost:8000/docs

## Deploy to Vercel

### 1. Install Vercel CLI

```bash
npm install -g vercel
```

### 2. Set environment variables

In your Vercel project settings → Environment Variables, add:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Supabase Postgres connection string |
| `GEMINI_API_KEY` | Google Gemini API key |
| `RAZORPAY_KEY_ID` | Razorpay test-mode Key ID (`rzp_test_...`) |
| `RAZORPAY_KEY_SECRET` | Razorpay test-mode Key Secret |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook signing secret from Razorpay dashboard |
| `JWT_SECRET` | Random secret for merchant dashboard tokens |
| `ADMIN_SIGNUP_CODE` | Code required to create merchant accounts |
| `CORS_ALLOW_ORIGINS` | Your Vercel domain (e.g. `https://your-app.vercel.app`) |

### 3. Deploy

```bash
vercel --prod
```

### 4. Configure Razorpay webhooks

In Razorpay Dashboard → Webhooks, set the URL to:
```
https://your-app.vercel.app/api/webhook
```

Enable the `order.paid` and `payment.failed` events.

## Architecture

```
Customer ↔ Chat UI ↔ FastAPI ↔ Agent Loop ↔ Gemini LLM
                                   ↕
                               Tool Functions ↔ Postgres (Supabase)
                                   ↕
                           Razorpay Orders API
```

- LLM has NO built-in product knowledge — learns everything by calling tools
- Agent loop decides which tool to call (max 6 per turn)
- `add_to_cart` and `initiate_checkout` require human confirmation
- Every tool call logged to `ai_actions` table (audit trail)
- Stock decremented on successful payment verification

## Key Files

| File | Purpose |
|------|---------|
| `backend/agent_loop.py` | Core agent loop |
| `backend/tools.py` | Tool functions that query Postgres |
| `backend/adapters/gemini_adapter.py` | Gemini API adapter (swappable) |
| `backend/routes/chat.py` | Chat API with confirmation gating |
| `backend/routes/checkout.py` | Razorpay payment flow + stock decrement |
| `backend/routes/catalog.py` | Agent-readable catalog (`GET /api/agent-catalog`) |
| `backend/routes/dashboard.py` | Merchant dashboard API |
| `backend/campaign_agent.py` | Abandoned cart recovery agent |
| `frontend/index.html` | Chat UI |
| `frontend/dashboard.html` | Merchant dashboard |
