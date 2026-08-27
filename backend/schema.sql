-- ============================================================
-- ShopMind AI — Database Schema
-- ============================================================
-- All tables use IF NOT EXISTS for idempotent runs.
-- Designed for Supabase Postgres (standard PostgreSQL 15+).
-- ============================================================

-- Products table: laptops and accessories
CREATE TABLE IF NOT EXISTS products (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    price           NUMERIC(10, 2) NOT NULL,
    ram_gb          INTEGER,
    gpu             VARCHAR(100),
    cpu             VARCHAR(100),
    use_case        TEXT[] DEFAULT '{}',
    stock           INTEGER NOT NULL DEFAULT 0,
    category        VARCHAR(50) NOT NULL DEFAULT 'laptop'
);

-- Cart items: per-session shopping cart
CREATE TABLE IF NOT EXISTS cart_items (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(100) NOT NULL,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity        INTEGER NOT NULL DEFAULT 1,
    source          VARCHAR(20) NOT NULL DEFAULT 'organic'
                    CHECK (source IN ('ai_recommendation', 'ai_upsell', 'organic')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cart_items_session ON cart_items(session_id);

-- Orders: tracks checkout and payment lifecycle
CREATE TABLE IF NOT EXISTS orders (
    id                          SERIAL PRIMARY KEY,
    session_id                  VARCHAR(100) NOT NULL,
    total                       NUMERIC(10, 2),
    ai_assisted                 BOOLEAN DEFAULT FALSE,
    ai_recommended_product_id   INTEGER REFERENCES products(id),
    actual_product_purchased_id INTEGER REFERENCES products(id),
    upsell_accepted             BOOLEAN,
    upsell_amount               NUMERIC(10, 2),
    razorpay_order_id           VARCHAR(100),
    razorpay_payment_id         VARCHAR(100),
    status                      VARCHAR(20) NOT NULL DEFAULT 'created'
                                CHECK (status IN ('created', 'paid', 'failed')),
    failure_reason              TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_session ON orders(session_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- Immutable order snapshot. Payment processing must use these rows rather
-- than the live cart, which a customer can change while a payment is pending.
CREATE TABLE IF NOT EXISTS order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    source      VARCHAR(20) NOT NULL
                CHECK (source IN ('ai_recommendation', 'ai_upsell', 'organic')),
    unit_price  NUMERIC(10, 2) NOT NULL CHECK (unit_price >= 0)
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);

-- AI Actions: full audit trail of every agent tool call
CREATE TABLE IF NOT EXISTS ai_actions (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(100) NOT NULL,
    agent_name      VARCHAR(50) NOT NULL DEFAULT 'shopmind_v1',
    action_type     VARCHAR(50) NOT NULL DEFAULT 'tool_call',
    tool_name       VARCHAR(100),
    input           JSONB,
    output          JSONB,
    decision        TEXT,
    user_approved   BOOLEAN,
    success         BOOLEAN,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_actions_session ON ai_actions(session_id);
CREATE INDEX IF NOT EXISTS idx_ai_actions_timestamp ON ai_actions(timestamp);

-- Co-purchase history: tracks which products are frequently bought together
CREATE TABLE IF NOT EXISTS co_purchase_history (
    product_id              INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    complementary_product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    co_purchase_count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (product_id, complementary_product_id)
);

-- Admin/merchant users for dashboard authentication (Stage G)
CREATE TABLE IF NOT EXISTS admin_users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Customer identities for cart recovery (Stage H)
CREATE TABLE IF NOT EXISTS customer_identities (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(100) NOT NULL,
    email           VARCHAR(255),
    phone           VARCHAR(20),
    name            VARCHAR(255),
    recovery_code_hash TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ci_email UNIQUE (email),
    CONSTRAINT uq_ci_phone UNIQUE (phone),
    CONSTRAINT chk_ci_contact CHECK (email IS NOT NULL OR phone IS NOT NULL)
);

-- Campaign Orchestrator actions (Stage J1)
-- Purpose-built table for proactive recovery nudge decisions.
-- Separate from ai_actions (which stays the generic audit log) so the
-- dashboard can render a clean "Campaign Orchestrator" panel without
-- parsing ai_actions.output JSON.
CREATE TABLE IF NOT EXISTS campaign_actions (
    id                  SERIAL PRIMARY KEY,
    session_id          VARCHAR(100) NOT NULL,
    cart_snapshot        JSONB,
    cart_value           NUMERIC(10, 2),
    cart_age_minutes     INTEGER,
    decision             TEXT,
    action_taken         VARCHAR(20) NOT NULL DEFAULT 'no_action'
                         CHECK (action_taken IN ('no_action', 'reminder', 'discount_offer')),
    discount_percent     NUMERIC(5, 2),
    simulated_channel    VARCHAR(10)
                         CHECK (simulated_channel IN ('email', 'sms') OR simulated_channel IS NULL),
    ai_action_log_id     INTEGER REFERENCES ai_actions(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaign_actions_session ON campaign_actions(session_id);
CREATE INDEX IF NOT EXISTS idx_campaign_actions_created ON campaign_actions(created_at);
