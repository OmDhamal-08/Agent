"""
reset_data.py — Wipe all transactional data and re-seed product catalog.

Run: python -m backend.reset_data

Truncates all tables (keeps structure), then re-seeds only products
and co_purchase_history. Leaves orders, cart_items, ai_actions,
customer_identities, campaign_actions, order_items all empty.
"""

import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


async def reset_database():
    """Truncate all data and re-seed products + co-purchase history."""
    if not DATABASE_URL:
        print("❌ DATABASE_URL not set in environment.")
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)

    try:
        print("\n🗑️  Wiping all table data (keeping structure)...")

        # Truncate all tables with CASCADE to handle foreign keys
        await conn.execute("""
            TRUNCATE TABLE
                campaign_actions,
                ai_actions,
                order_items,
                orders,
                cart_items,
                customer_identities,
                co_purchase_history,
                products
            CASCADE
        """)

        print("✅ All tables truncated successfully.")

        # Verify all tables are empty
        tables = [
            'products', 'cart_items', 'orders', 'order_items',
            'ai_actions', 'customer_identities', 'campaign_actions',
            'co_purchase_history',
        ]
        for table in tables:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"   {table}: {count} rows")

    finally:
        await conn.close()

    # Re-seed products and co-purchase history using existing seed.py
    print("\n🌱 Re-seeding products and co-purchase history...")
    from backend.seed import run_seed
    await run_seed()

    # Verify re-seeded data
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        products_count = await conn.fetchval("SELECT COUNT(*) FROM products")
        copurchase_count = await conn.fetchval("SELECT COUNT(*) FROM co_purchase_history")
        orders_count = await conn.fetchval("SELECT COUNT(*) FROM orders")
        cart_count = await conn.fetchval("SELECT COUNT(*) FROM cart_items")
        ai_count = await conn.fetchval("SELECT COUNT(*) FROM ai_actions")

        print(f"\n📊 Final state:")
        print(f"   products: {products_count} rows (re-seeded)")
        print(f"   co_purchase_history: {copurchase_count} rows (re-seeded)")
        print(f"   orders: {orders_count} rows (empty)")
        print(f"   cart_items: {cart_count} rows (empty)")
        print(f"   ai_actions: {ai_count} rows (empty)")
        print(f"\n✅ Database reset complete! Ready for fresh testing.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(reset_database())
