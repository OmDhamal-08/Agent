# 🧠 ShopMind AI — Agentic Commerce Assistant

> Razorpay Hackathon Submission · Track 1: AI Growth & Agentic Commerce

ShopMind AI is a customer-facing AI shopping agent for laptops, powered by a
genuine LLM agent loop (Gemini), backed by a real Postgres database, with
Razorpay payment integration and a merchant dashboard showing what the agent
actually did.

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

## Key Files

| File | Purpose |
|------|---------|
| `backend/agent_loop.py` | Core agent loop — the brain |
| `backend/tools.py` | 7 tool functions that query Postgres |
| `backend/adapters/gemini_adapter.py` | Gemini API adapter (swappable) |
| `backend/routes/chat.py` | Chat API with confirmation gating |
| `backend/routes/checkout.py` | Razorpay payment flow |
| `backend/routes/catalog.py` | Agent-readable catalog (GET /api/agent-catalog) |
| `backend/routes/dashboard.py` | Merchant dashboard API |
| `frontend/index.html` | Chat UI |
| `frontend/dashboard.html` | Merchant dashboard |
| `EXPLANATION.md` | Design reasoning for judges |
| `CHANGELOG.md` | Stage-by-stage build log |

## Hackathon Judging Criteria

See [EXPLANATION.md](EXPLANATION.md) for detailed coverage of each criterion.
