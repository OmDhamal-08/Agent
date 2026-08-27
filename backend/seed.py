"""
seed.py — Standalone script to create tables and seed the database.

Run directly: python -m backend.seed
Creates all tables (idempotent) and inserts seed data (clears existing first).
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# ──────────────────────────────────────────────
# Seed data: 25 laptops
# ──────────────────────────────────────────────
LAPTOPS = [
    # (name, price, ram_gb, gpu, cpu, use_case[], stock)
    ("Acer Aspire 5 (2024)", 42990, 8, "Intel Iris Xe", "Intel i5-1340P", ["general", "coding"], 15),
    ("Lenovo IdeaPad Slim 3", 40990, 8, "AMD Radeon 610M", "AMD Ryzen 5 7520U", ["general"], 20),
    ("HP Pavilion 15 (2024)", 52990, 8, "Intel Iris Xe", "Intel i5-1335U", ["general", "coding"], 12),
    ("ASUS VivoBook 15 OLED", 44990, 8, "AMD Radeon Graphics", "AMD Ryzen 5 7530U", ["general", "coding"], 18),
    ("Dell Inspiron 15 3530", 48990, 8, "Intel UHD Graphics", "Intel i5-1335U", ["general"], 14),
    ("Acer Nitro V 15", 62990, 8, "NVIDIA RTX 3050", "Intel i5-13420H", ["gaming", "coding"], 10),
    ("Lenovo IdeaPad Gaming 3", 64990, 8, "NVIDIA RTX 3050", "AMD Ryzen 5 7535HS", ["gaming"], 8),
    ("HP Victus 15 (2024)", 61990, 8, "NVIDIA RTX 3050", "Intel i5-12450H", ["gaming"], 11),
    ("MSI Thin GF63", 59990, 8, "NVIDIA RTX 3050", "Intel i5-12450H", ["gaming"], 9),
    ("ASUS TUF Gaming F15", 69990, 16, "NVIDIA RTX 4050", "Intel i5-12500H", ["gaming", "coding"], 7),
    ("Lenovo LOQ 15IRX9", 74990, 16, "NVIDIA RTX 4050", "Intel i7-13650HX", ["gaming", "coding"], 6),
    ("Acer Nitro 16", 79990, 16, "NVIDIA RTX 4050", "AMD Ryzen 7 7735HS", ["gaming", "ml"], 5),
    ("Dell G15 5530", 82990, 16, "NVIDIA RTX 4050", "Intel i7-13650HX", ["gaming", "coding"], 8),
    ("HP Omen 16 (2024)", 89990, 16, "NVIDIA RTX 4060", "Intel i7-13700HX", ["gaming", "ml", "video_editing"], 4),
    ("ASUS ROG Strix G15", 94990, 16, "NVIDIA RTX 4060", "AMD Ryzen 7 7735HS", ["gaming", "ml"], 5),
    ("MSI Katana 15 B13V", 84990, 16, "NVIDIA RTX 4060", "Intel i7-13620H", ["gaming", "video_editing"], 6),
    ("Lenovo Legion 5i Pro", 99990, 16, "NVIDIA RTX 4060", "Intel i7-13700HX", ["gaming", "ml", "coding"], 4),
    ("ASUS ROG Strix G16", 109990, 16, "NVIDIA RTX 4060", "Intel i9-13980HX", ["gaming", "ml", "video_editing"], 3),
    ("Dell XPS 15 9530", 104990, 16, "NVIDIA RTX 4050", "Intel i7-13700H", ["coding", "video_editing", "general"], 5),
    ("Lenovo ThinkPad E16 Gen 1", 72990, 16, "Intel Iris Xe", "Intel i7-1355U", ["coding", "general"], 10),
    ("HP EliteBook 840 G10", 89990, 16, "Intel Iris Xe", "Intel i7-1365U", ["coding", "general"], 7),
    ("ASUS Zenbook 14 OLED", 76990, 16, "AMD Radeon 780M", "AMD Ryzen 7 7730U", ["coding", "general"], 9),
    ("MSI Creator Z16 HX", 114990, 32, "NVIDIA RTX 4060", "Intel i7-13700HX", ["ml", "video_editing", "coding"], 3),
    ("Lenovo Legion Pro 5 16", 119990, 32, "NVIDIA RTX 4060", "AMD Ryzen 7 7745HX", ["gaming", "ml", "video_editing"], 2),
    ("ASUS ProArt Studiobook 16", 117990, 32, "NVIDIA RTX 4060", "Intel i9-13980HX", ["ml", "video_editing"], 3),
]

# ──────────────────────────────────────────────
# Seed data: 10 accessories
# ──────────────────────────────────────────────
ACCESSORIES = [
    # (name, price, category_tag, stock)
    ("Cosmic Byte Equinox Laptop Cooling Pad", 1499, "cooling_pad", 50),
    ("Deepcool N80 RGB Laptop Cooler", 2499, "cooling_pad", 30),
    ("Logitech G502 HERO Gaming Mouse", 3999, "mouse", 40),
    ("Logitech MX Master 3S Wireless Mouse", 7999, "mouse", 25),
    ("HP Prelude 15.6\" Laptop Bag", 1299, "bag", 60),
    ("ASUS ROG Ranger BP1500 Backpack", 3499, "bag", 20),
    ("LG UltraWide 29WQ600 29\" Monitor", 18999, "monitor", 10),
    ("Dell S2722QC 27\" 4K USB-C Monitor", 24999, "monitor", 8),
    ("Redgear Shadow Amulet Mechanical Keyboard", 2499, "keyboard", 35),
    ("HyperX Cloud Stinger 2 Gaming Headset", 3499, "headset", 25),
]

# ──────────────────────────────────────────────
# Co-purchase pairings (product_index → accessory_index, count)
# Indexes refer to position in LAPTOPS / ACCESSORIES lists (0-based).
# These are built programmatically below based on rules.
# ──────────────────────────────────────────────


def build_co_purchase_pairs() -> list[tuple[int, int, int]]:
    """
    Build co-purchase pairs based on product characteristics.
    Returns list of (laptop_product_id, accessory_product_id, co_purchase_count).
    Product IDs are 1-based (matching SERIAL primary keys).
    Laptops are IDs 1-25, accessories are IDs 26-35.
    """
    pairs = []
    accessory_base_id = len(LAPTOPS) + 1  # Accessories start after laptops

    for laptop_idx, laptop in enumerate(LAPTOPS):
        laptop_id = laptop_idx + 1
        _name, _price, _ram, gpu, _cpu, use_cases, _stock = laptop

        has_gaming = "gaming" in use_cases
        has_ml = "ml" in use_cases
        has_coding = "coding" in use_cases
        has_dedicated_gpu = "RTX" in gpu or "GTX" in gpu

        for acc_idx, acc in enumerate(ACCESSORIES):
            acc_id = accessory_base_id + acc_idx
            _acc_name, _acc_price, acc_cat, _acc_stock = acc

            count = 0

            # Cooling pads: high co-purchase for gaming/GPU laptops
            if acc_cat == "cooling_pad":
                if has_gaming or has_dedicated_gpu:
                    count = 45 + (laptop_idx % 20)  # 45-64
                else:
                    count = 10 + (laptop_idx % 10)  # 10-19

            # Mice: gaming mouse for gaming laptops, productivity mouse for coding
            elif acc_cat == "mouse":
                if acc_idx == 2 and has_gaming:  # G502 gaming mouse
                    count = 50 + (laptop_idx % 15)
                elif acc_idx == 3 and (has_coding or has_ml):  # MX Master 3S
                    count = 40 + (laptop_idx % 12)
                else:
                    count = 5 + (laptop_idx % 8)

            # Bags: everyone buys bags
            elif acc_cat == "bag":
                if acc_idx == 5 and has_gaming:  # ROG backpack for gamers
                    count = 35 + (laptop_idx % 10)
                elif acc_idx == 4:  # HP bag — universal
                    count = 30 + (laptop_idx % 15)
                else:
                    count = 8 + (laptop_idx % 5)

            # Monitors: ML/coding/video editing users
            elif acc_cat == "monitor":
                if has_ml or "video_editing" in use_cases:
                    count = 30 + (laptop_idx % 10)
                elif has_coding:
                    count = 20 + (laptop_idx % 8)
                else:
                    count = 3 + (laptop_idx % 5)

            # Keyboard: coding and gaming
            elif acc_cat == "keyboard":
                if has_gaming or has_coding:
                    count = 25 + (laptop_idx % 10)
                else:
                    count = 5 + (laptop_idx % 5)

            # Headset: gaming
            elif acc_cat == "headset":
                if has_gaming:
                    count = 40 + (laptop_idx % 10)
                else:
                    count = 5 + (laptop_idx % 5)

            if count > 0:
                pairs.append((laptop_id, acc_id, count))

    return pairs


async def run_seed():
    """Connect to the database, create tables, and seed data."""
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable is not set.")
        print("Copy .env.example to .env and fill in your Supabase connection string.")
        sys.exit(1)

    print(f"Connecting to database...")
    is_tx_pooler = ":6543" in DATABASE_URL
    conn = await asyncpg.connect(
        dsn=DATABASE_URL,
        statement_cache_size=0 if is_tx_pooler else 1024,
    )

    try:
        # ── Step 1: Run schema DDL ──
        print("Creating tables...")
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        await conn.execute(schema_sql)
        print("✓ Tables created (or already exist).")

        # ── Step 2: Clear existing seed data (idempotent re-runs) ──
        print("Clearing existing seed data...")
        await conn.execute("DELETE FROM co_purchase_history")
        await conn.execute("DELETE FROM ai_actions")
        await conn.execute("DELETE FROM cart_items")
        await conn.execute("DELETE FROM order_items")
        await conn.execute("DELETE FROM orders")
        await conn.execute("DELETE FROM products")
        # Reset serial sequences so IDs start from 1
        await conn.execute("ALTER SEQUENCE products_id_seq RESTART WITH 1")
        await conn.execute("ALTER SEQUENCE cart_items_id_seq RESTART WITH 1")
        await conn.execute("ALTER SEQUENCE orders_id_seq RESTART WITH 1")
        await conn.execute("ALTER SEQUENCE ai_actions_id_seq RESTART WITH 1")
        print("✓ Existing data cleared.")

        # ── Step 3: Insert laptops ──
        print(f"Inserting {len(LAPTOPS)} laptops...")
        for laptop in LAPTOPS:
            name, price, ram_gb, gpu, cpu, use_case, stock = laptop
            await conn.execute(
                """
                INSERT INTO products (name, price, ram_gb, gpu, cpu, use_case, stock, category)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'laptop')
                """,
                name, price, ram_gb, gpu, cpu, use_case, stock,
            )
        print(f"✓ {len(LAPTOPS)} laptops inserted.")

        # ── Step 4: Insert accessories ──
        print(f"Inserting {len(ACCESSORIES)} accessories...")
        for acc in ACCESSORIES:
            name, price, category_tag, stock = acc
            await conn.execute(
                """
                INSERT INTO products (name, price, ram_gb, gpu, cpu, use_case, stock, category)
                VALUES ($1, $2, NULL, NULL, NULL, $3, $4, 'accessory')
                """,
                name, price, [category_tag], stock,
            )
        print(f"✓ {len(ACCESSORIES)} accessories inserted.")

        # ── Step 5: Insert co-purchase history ──
        pairs = build_co_purchase_pairs()
        print(f"Inserting {len(pairs)} co-purchase pairs...")
        for laptop_id, acc_id, count in pairs:
            await conn.execute(
                """
                INSERT INTO co_purchase_history (product_id, complementary_product_id, co_purchase_count)
                VALUES ($1, $2, $3)
                ON CONFLICT (product_id, complementary_product_id) DO UPDATE
                SET co_purchase_count = $3
                """,
                laptop_id, acc_id, count,
            )
        print(f"✓ {len(pairs)} co-purchase pairs inserted.")

        # ── Step 6: Verify ──
        print("\n── Verification ──")
        laptop_count = await conn.fetchval(
            "SELECT COUNT(*) FROM products WHERE category = 'laptop'"
        )
        accessory_count = await conn.fetchval(
            "SELECT COUNT(*) FROM products WHERE category = 'accessory'"
        )
        pair_count = await conn.fetchval("SELECT COUNT(*) FROM co_purchase_history")
        total_products = await conn.fetchval("SELECT COUNT(*) FROM products")

        print(f"  Laptops:          {laptop_count}")
        print(f"  Accessories:      {accessory_count}")
        print(f"  Total products:   {total_products}")
        print(f"  Co-purchase pairs:{pair_count}")

        # Quick sanity check on price range
        min_price = await conn.fetchval(
            "SELECT MIN(price) FROM products WHERE category = 'laptop'"
        )
        max_price = await conn.fetchval(
            "SELECT MAX(price) FROM products WHERE category = 'laptop'"
        )
        print(f"  Laptop price range: ₹{min_price:,.0f} – ₹{max_price:,.0f}")

        # Show a few sample products
        print("\n── Sample Products ──")
        rows = await conn.fetch(
            "SELECT id, name, price, ram_gb, gpu, category FROM products ORDER BY id LIMIT 5"
        )
        for row in rows:
            print(
                f"  [{row['id']:>2}] {row['name']:<35} ₹{row['price']:>10,.0f}  "
                f"{row['ram_gb'] or '-':>3}GB  {row['gpu'] or 'N/A':<20} ({row['category']})"
            )

        print("\n✅ Seed completed successfully!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_seed())
