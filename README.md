<div align="center">

# 🧠 ShopMind AI — Autonomous Agentic Commerce Assistant

**An intelligent, autonomous e-commerce shopping agent with human-in-the-loop gating, multi-key quota rotation, explainable AI audit trails, and Razorpay payment integration.**

[![Python](https://img.shields.io/badge/Python-3.12%20%7C%203.10-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-SDK%20v2-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![Razorpay](https://img.shields.io/badge/Razorpay-Payment%20Gateway-0C2340?style=for-the-badge&logo=razorpay&logoColor=white)](https://razorpay.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://supabase.com/)
[![Vercel](https://img.shields.io/badge/Deployed%20on-Vercel-000000?style=for-the-badge&logo=vercel&logoColor=white)](https://vercel.com/)

[Features](#-key-features) • [Architecture](#-architecture) • [Getting Started](#-getting-started) • [Deployment](#-vercel-deployment) • [API Reference](#-api-endpoints) • [Key Rotation](#-multi-account-gemini-key-pool)

</div>

---

## 📖 Overview

**ShopMind AI** is an agentic e-commerce platform built for the **Razorpay Hackathon (Track 1: AI Growth & Agentic Commerce)**. Unlike basic prompt-engineered chatbots, ShopMind AI operates as a **genuine autonomous agent loop**:

1. **Zero Hallucination Guarantee**: The LLM has **no built-in product knowledge**. It must query tools (`search_products`, `compare_products`, `get_stock`) to retrieve verified product specs, pricing, and availability.
2. **Human-in-the-Loop Gating**: Sensitive, money-moving actions (`add_to_cart`, `initiate_checkout`) pause the loop and require explicit client confirmation before execution.
3. **Multi-Account API Key Rotation Pool**: Overcomes free-tier Gemini API quota limitations (`429 RESOURCE_EXHAUSTED`) with seamless round-robin rotation, automatic cooldown, and instant background failover across multiple Google accounts with zero loss of conversation memory.
4. **Client-Friendly Error Sanitization**: Technical backend exceptions, rate limits, and server tracebacks are filtered into polite, natural English for shoppers and merchants, while complete tracebacks are logged server-side for developers.
5. **Autonomous Campaign Orchestrator**: A proactive background agent evaluates abandoned shopping carts, reasons about customer history and margins, and crafts targeted reminders or discount recovery nudges.
6. **Machine-Readable Agent Catalog**: Exposes a HATEOAS-compliant catalog (`/api/agent-catalog`) enabling external autonomous AI agents to search, compare, and transact directly.

---

## 🚀 Key Features

| Feature | Description | File Reference |
| :--- | :--- | :--- |
| **Autonomous Agent Loop** | Bounded reasoning turn loop (max 10 tool calls per turn) with tool calling. | [`backend/agent_loop.py`](backend/agent_loop.py) |
| **Multi-Key Pool & Rotation** | Automatic round-robin load balancing & instant 429 quota failover across multiple Gemini keys. | [`backend/adapters/key_pool.py`](backend/adapters/key_pool.py) |
| **Confirmation Gating (HITL)** | High-stakes actions require one-click user confirmation before modifying cart or checkout state. | [`backend/tool_definitions.py`](backend/tool_definitions.py) |
| **Full AI Audit Trail** | Every decision, tool input, output, and user approval is recorded immutably in PostgreSQL. | [`backend/logging_middleware.py`](backend/logging_middleware.py) |
| **Razorpay Integration** | End-to-end checkout with atomic inventory row-locking (`FOR UPDATE`) and webhook signature validation. | [`backend/routes/checkout.py`](backend/routes/checkout.py) |
| **Proactive Campaign Agent** | Periodically inspects abandoned carts to calculate conversion probability and recovery nudges. | [`backend/campaign_agent.py`](backend/campaign_agent.py) |
| **Merchant Analytics Dashboard** | Real-time tracking of AI-driven revenue, upsell conversion rate, and handled failure proofs. | [`frontend/dashboard.html`](frontend/dashboard.html) |
| **Client-Friendly Sanitization** | Eliminates raw API codes, URLs, or Python traces from customer and admin UIs. | [`backend/error_messages.py`](backend/error_messages.py) |

---

## 🏗️ Architecture

```mermaid
flowchart TD
    subgraph ClientLayer["🖥️ Frontend & External Agents"]
        Customer["Shopper UI\n(HTML5 / CSS / Vanilla JS)"]
        Merchant["Merchant Dashboard\n(JWT Protected)"]
        ExtAgent["External AI Agents\n(/api/agent-catalog)"]
    end

    subgraph APILayer["⚡ FastAPI Gateway (Vercel Serverless)"]
        ChatRoute["/api/chat\n(Session & Confirmation)"]
        CheckoutRoute["/api/create-order\n/api/verify-payment"]
        CampaignRoute["/api/campaigns/run\n/api/campaigns/history"]
        WebhookRoute["/api/webhook\n(HMAC-SHA256 Signed)"]
    end

    subgraph AgentCore["🧠 ShopMind Intelligence Engine"]
        AgentLoop["Agent Reasoning Loop\n(Max 10 calls/turn)"]
        KeyPool["Gemini Key Pool\n(Multi-Account 429 Failover)"]
        CampaignOrch["Campaign Orchestrator\n(Autonomous Cart Recovery)"]
        ErrorSanitizer["Error Sanitizer\n(Client-Friendly Messaging)"]
    end

    subgraph StorageAndPayment["💾 Data & Gateway"]
        Supabase[("PostgreSQL (Supabase)\n• session_state\n• ai_actions\n• orders & items\n• products & stock")]
        Razorpay["💳 Razorpay API\n(Test Mode)"]
    end

    Customer <--> ChatRoute
    Merchant <--> CampaignRoute
    Customer <--> CheckoutRoute
    ExtAgent <--> APILayer

    ChatRoute <--> AgentLoop
    CampaignRoute <--> CampaignOrch
    AgentLoop <--> KeyPool
    CampaignOrch <--> KeyPool
    AgentLoop -. Errors .-> ErrorSanitizer

    AgentLoop <--> Supabase
    CheckoutRoute <--> Supabase
    CheckoutRoute <--> Razorpay
    Razorpay -- Webhooks --> WebhookRoute
```

---

## 🔄 Multi-Account Gemini Key Pool

Free-tier Google Gemini keys have strict rate limits (15 Requests Per Minute / daily caps). ShopMind AI includes a built-in, thread-safe key rotation pool:

* **Stateless Continuity**: Gemini API calls are completely stateless. Conversation history is persisted in PostgreSQL (`session_state` table). Swapping keys between turns or during a retry has **zero impact on conversation memory**.
* **Round-Robin Scheduling**: Distributes incoming calls across all healthy keys.
* **Instant 429 Failover**: When an account reaches its quota limit (`429 RESOURCE_EXHAUSTED`), the pool:
  1. Places that specific key in a temporary 60-second cooldown (escalates to 10 minutes on repeated limits).
  2. Masks the key for security in logs (e.g. `...xY4q`).
  3. Immediately rotates to the next healthy key in the pool and retries the exact turn.
  4. The customer experiences zero errors or dropped turns.

---

## 📂 Project Structure

```
Razor_pay_agent/
├── api/
│   └── index.py                     # Vercel serverless entrypoint & shims
├── backend/
│   ├── adapters/
│   │   ├── gemini_adapter.py        # Gemini SDK adapter with tool conversion
│   │   └── key_pool.py              # Thread-safe multi-account key rotation pool
│   ├── routes/
│   │   ├── admin_auth.py            # Merchant dashboard JWT authentication
│   │   ├── campaign.py              # Cart recovery campaign endpoints
│   │   ├── cart.py                  # Shopping cart CRUD operations
│   │   ├── catalog.py               # Human & machine-readable agent catalog
│   │   ├── chat.py                  # Conversational agent & confirmation gating
│   │   ├── checkout.py              # Razorpay order creation & payment verification
│   │   ├── dashboard.py             # Business KPIs & AI decision audit trail
│   │   ├── session.py               # Customer identity & cart restoration
│   │   └── webhook.py               # Razorpay signed webhook handler
│   ├── agent_loop.py                # Core bounded tool-calling reasoning loop
│   ├── auth.py                      # Password hashing (bcrypt) & JWT helpers
│   ├── campaign_agent.py            # Autonomous abandoned cart recovery reasoning
│   ├── campaign_tools.py            # Context extraction & decision logging tools
│   ├── campaign_tool_definitions.py # Tool schemas for campaign LLM
│   ├── database.py                  # Asyncpg connection pooling & direct fallbacks
│   ├── error_messages.py            # Client-friendly error classifier & translation
│   ├── logging_middleware.py        # Immutable ai_actions audit logger
│   ├── main.py                      # FastAPI application setup & CORS configuration
│   ├── models.py                    # Pydantic request & response models
│   ├── reset_data.py                # Database test data reset utility
│   ├── schema.sql                   # Complete PostgreSQL schema DDL
│   ├── seed.py                      # Sample laptop catalog & co-purchase seeds
│   ├── tools.py                     # E-commerce tools (search, compare, stock, cart)
│   └── tool_definitions.py          # JSON schema declarations for shopping tools
├── frontend/
│   ├── css/
│   │   └── styles.css               # Modern dark/light responsive styling
│   ├── js/
│   │   ├── chat.js                  # Chat UI, streaming states & HITL buttons
│   │   ├── checkout.js              # Razorpay Standard Checkout modal integration
│   │   ├── dashboard.js             # Merchant KPI charts, tables, & action logs
│   │   └── dashboard-auth.js        # Admin login/signup modal & token persistence
│   ├── dashboard.html               # Merchant Intelligence Dashboard
│   └── index.html                   # Customer Shopping Experience
├── .env.example                     # Environment configuration template
├── requirements.txt                 # Python dependencies
├── vercel.json                      # Vercel serverless & static routing rules
└── README.md                        # Documentation
```

---

## 🛠️ Getting Started

### Prerequisites
* Python 3.10 or 3.12
* PostgreSQL database (e.g. [Supabase](https://supabase.com/) free tier)
* One or more [Google Gemini API Keys](https://aistudio.google.com/)
* [Razorpay Test Account](https://dashboard.razorpay.com/)

---

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/shopmind-ai.git
cd shopmind-ai
```

### 2. Set Up Virtual Environment & Dependencies

```bash
# Create virtual environment
python -m venv venv

# Activate (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Activate (Linux/macOS)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

### 3. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```ini
# Database Connection (Supabase transaction pooler URL recommended)
DATABASE_URL=postgresql://postgres.your-id:your-password@aws-0-region.pooler.supabase.com:6543/postgres

# Single or Multi-Account Gemini API Keys
# You can provide multiple comma-separated keys from different accounts for automatic rotation:
GEMINI_API_KEYS=AIzaSyA...,AIzaSyB...,AIzaSyC...
# Or a single key:
GEMINI_API_KEY=AIzaSyA...
GEMINI_MODEL=gemini-2.5-flash

# Razorpay Test Credentials
RAZORPAY_KEY_ID=rzp_test_your_key_id
RAZORPAY_KEY_SECRET=your_razorpay_secret
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret

# Merchant Dashboard Security
JWT_SECRET=generate-a-secure-random-string-for-jwt
ADMIN_SIGNUP_CODE=shopmind-admin-2024

# CORS (Defaults to localhost if omitted)
CORS_ALLOW_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
```

---

### 4. Initialize the Database

Run the database seeder to create tables, populate the product catalog (laptops and accessories), and insert co-purchase history:

```bash
python -m backend.seed
```

---

### 5. Start the Application

```bash
uvicorn backend.main:app --reload --port 8000
```

Open your browser:
* **Customer Chatbot:** [`http://localhost:8000/`](http://localhost:8000/)
* **Merchant Dashboard:** [`http://localhost:8000/dashboard.html`](http://localhost:8000/dashboard.html)
* **Agent-to-Agent Catalog:** [`http://localhost:8000/api/agent-catalog`](http://localhost:8000/api/agent-catalog)
* **Interactive API Docs (Swagger):** [`http://localhost:8000/docs`](http://localhost:8000/docs)

---

## ☁️ Vercel Deployment

ShopMind AI is pre-configured for **zero-configuration deployment on Vercel** using `@vercel/python` and static CDN hosting:

### 1. Install Vercel CLI & Deploy

```bash
npm install -g vercel
vercel
```

### 2. Configure Environment Variables on Vercel
In your Vercel Dashboard (**Settings** → **Environment Variables**), add:
* `DATABASE_URL`
* `GEMINI_API_KEYS` (comma-separated list of your keys)
* `GEMINI_MODEL`
* `RAZORPAY_KEY_ID`
* `RAZORPAY_KEY_SECRET`
* `RAZORPAY_WEBHOOK_SECRET`
* `JWT_SECRET`
* `ADMIN_SIGNUP_CODE`
* `CORS_ALLOW_ORIGINS` (e.g. `https://your-project.vercel.app`)

### 3. Deploy to Production

```bash
vercel --prod
```

### 4. Setup Razorpay Webhooks
In your [Razorpay Dashboard](https://dashboard.razorpay.com/) → **Settings** → **Webhooks**:
* **URL:** `https://your-project.vercel.app/api/webhook`
* **Secret:** The same secret specified in `RAZORPAY_WEBHOOK_SECRET`
* **Events:** Check `order.paid` and `payment.failed`

---

## 📡 API Endpoints

### Conversational & Shopping
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/session/start` | Restore or initialize customer session by email |
| `POST` | `/api/chat` | Main agent conversation turn |
| `POST` | `/api/chat/confirm` | Confirm a pending action (`add_to_cart`, `checkout`) |
| `POST` | `/api/chat/cancel` | Decline a pending action |
| `GET` | `/api/cart` | Retrieve current cart items, total, and AI-attribution tags |
| `DELETE` | `/api/cart/item` | Remove a single item from cart |
| `DELETE` | `/api/cart` | Empty entire cart |

### Payments & Orders (Razorpay)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/create-order` | Generate Razorpay order ID with server-side pricing |
| `POST` | `/api/verify-payment` | Verify HMAC payment signature & atomically decrement stock |
| `POST` | `/api/payment-failed` | Log failed checkout attempt with error reason |
| `POST` | `/api/webhook` | Asynchronous Razorpay webhook handler |

### Merchant Dashboard & Intelligence
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/admin/signup` | Register new merchant admin with signup code |
| `POST` | `/api/admin/login` | Authenticate merchant and issue JWT bearer token |
| `GET` | `/api/dashboard/summary` | Retrieve revenue, AI-assisted %, AOV, and upsell metrics |
| `GET` | `/api/dashboard/ai-actions` | Audit trail of tool calls, inputs, outputs, and reasoning |
| `GET` | `/api/dashboard/orders` | Order history with payment status & failure reasons |
| `POST` | `/api/campaigns/run` | Trigger autonomous abandoned cart recovery scan |
| `GET` | `/api/campaigns/history` | View campaign actions, reasoning, and discounts sent |

### External AI Agent Interface
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/agent-catalog` | HATEOAS machine-readable catalog with transaction endpoints |

---

## 🛡️ Security & Integrity

* **Row-Level Locking**: Stock deduction uses PostgreSQL `FOR UPDATE` transactions to prevent race conditions or overselling during high-volume flash checkouts.
* **Tamper-Proof Pricing**: Frontend client never submits prices. All monetary amounts are computed directly from the PostgreSQL catalog snapshot.
* **Cryptographic Signatures**: Razorpay payment verifications use HMAC-SHA256 signature verification matching Razorpay's secret key.
* **No Leaked Credentials**: All API keys, connection strings, and JWT tokens are kept out of source control and read via environment variables.

---

## 👥 Contributors

* **ShopMind AI Team** — Built with ❤️ for the Razorpay Hackathon.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
