"""
ShopMind AI – Tool functions for the agent.

Each tool is an async function that takes an ``asyncpg.Connection`` as its
first argument and returns a plain dict (JSON-serialisable).  The module
exposes a ``TOOL_DISPATCH`` mapping so the orchestrator can look up any
tool by name.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

import asyncpg


# Helpers

def _dec(value: Any) -> float:
    """Convert a Decimal (or numeric) value to a plain float for JSON."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row_to_product(row: asyncpg.Record) -> Dict[str, Any]:
    """Map an asyncpg Record from the *products* table to a JSON-safe dict."""
    return {
        "id": row["id"],
        "name": row["name"],
        "price": _dec(row["price"]),
        "ram_gb": row["ram_gb"],
        "gpu": row["gpu"],
        "cpu": row["cpu"],
        "use_case": list(row["use_case"]) if row["use_case"] else [],
        "stock": row["stock"],
        "category": row["category"],
    }




async def search_products(
    conn: asyncpg.Connection,
    budget_max: Optional[float] = None,
    use_cases: Optional[List[str]] = None,
    min_ram_gb: Optional[int] = None,
) -> Dict[str, Any]:
    """Search for laptops that match the given filters.

    Filters applied (all optional):
    - ``budget_max``: maximum price (inclusive).
    - ``use_cases``: list of use-case tags; at least one must overlap with the
      product's ``use_case`` array (PostgreSQL ``&&`` operator).
    - ``min_ram_gb``: minimum RAM in gigabytes.

    Fixed filters: ``category = 'laptop'`` and ``stock > 0``.

    Returns::

        {"products": [...], "count": N}
    """
    conditions: List[str] = ["category = 'laptop'", "stock > 0"]
    params: List[Any] = []
    idx = 1  # positional parameter counter

    if budget_max is not None:
        conditions.append(f"price <= ${idx}")
        params.append(budget_max)
        idx += 1

    if use_cases:
        conditions.append(f"use_case && ${idx}")
        params.append(use_cases)
        idx += 1

    if min_ram_gb is not None:
        conditions.append(f"ram_gb >= ${idx}")
        params.append(min_ram_gb)
        idx += 1

    where = " AND ".join(conditions)
    query = f"SELECT * FROM products WHERE {where} ORDER BY price ASC"

    rows = await conn.fetch(query, *params)
    products = [_row_to_product(r) for r in rows]

    return {"products": products, "count": len(products)}




async def compare_products(
    conn: asyncpg.Connection,
    product_ids: Sequence[int],
) -> Dict[str, Any]:
    """Return full details for the requested product IDs so the user can
    compare them side-by-side.

    Returns::

        {"products": [...], "count": N}
    """
    if not product_ids:
        return {"products": [], "count": 0}

    rows = await conn.fetch(
        "SELECT * FROM products WHERE id = ANY($1)",
        list(product_ids),
    )
    products = [_row_to_product(r) for r in rows]

    return {"products": products, "count": len(products)}




async def get_cart(
    conn: asyncpg.Connection,
    session_id: str,
) -> Dict[str, Any]:
    """Retrieve the current shopping cart for *session_id*.

    Returns::

        {
            "items": [
                {
                    "product_id": int,
                    "product_name": str,
                    "price": float,
                    "quantity": int,
                    "source": str,
                    "subtotal": float,
                },
                ...
            ],
            "total": float,
            "item_count": int,
        }
    """
    rows = await conn.fetch(
        """
        SELECT
            ci.product_id,
            p.name   AS product_name,
            p.price,
            ci.quantity,
            ci.source
        FROM cart_items ci
        JOIN products p ON p.id = ci.product_id
        WHERE ci.session_id = $1
        ORDER BY ci.created_at ASC
        """,
        session_id,
    )

    items: List[Dict[str, Any]] = []
    total = 0.0
    item_count = 0

    for row in rows:
        price = _dec(row["price"])
        qty = row["quantity"]
        subtotal = round(price * qty, 2)
        total += subtotal
        item_count += qty
        items.append({
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "price": price,
            "quantity": qty,
            "source": row["source"],
            "subtotal": subtotal,
        })

    return {
        "items": items,
        "total": round(total, 2),
        "item_count": item_count,
    }




async def add_to_cart(
    conn: asyncpg.Connection,
    session_id: str,
    product_id: int,
    quantity: int = 1,
    source: str = "organic",
) -> Dict[str, Any]:
    """Add a product to the cart after verifying stock availability.

    Stock is **not** decremented here – it is only reduced upon successful
    payment processing.

    Returns on success::

        {"success": true, "cart_item_id": N, "product_name": str, "quantity": N}

    Returns when out of stock::

        {"success": false, "reason": "out_of_stock",
         "product_id": N, "available_stock": N}
    """
    # Check current stock level
    row = await conn.fetchrow(
        "SELECT name, stock FROM products WHERE id = $1",
        product_id,
    )

    if row is None:
        return {
            "success": False,
            "reason": "product_not_found",
            "product_id": product_id,
        }

    available_stock: int = row["stock"]
    product_name: str = row["name"]

    if available_stock < quantity:
        return {
            "success": False,
            "reason": "out_of_stock",
            "product_id": product_id,
            "available_stock": available_stock,
        }

    # Insert into cart (do NOT decrement stock)
    cart_item_id = await conn.fetchval(
        """
        INSERT INTO cart_items (session_id, product_id, quantity, source)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        session_id,
        product_id,
        quantity,
        source,
    )

    return {
        "success": True,
        "cart_item_id": cart_item_id,
        "product_name": product_name,
        "quantity": quantity,
    }




async def get_complementary_products(
    conn: asyncpg.Connection,
    product_id: int,
) -> Dict[str, Any]:
    """Return up to 5 complementary products for *product_id* based on
    co-purchase history, ordered by co-purchase frequency (descending).

    Returns::

        {
            "product_id": int,
            "complementary_products": [...],
            "count": N,
        }
    """
    rows = await conn.fetch(
        """
        SELECT
            p.id,
            p.name,
            p.price,
            p.ram_gb,
            p.gpu,
            p.cpu,
            p.use_case,
            p.stock,
            p.category,
            cph.co_purchase_count
        FROM co_purchase_history cph
        JOIN products p ON p.id = cph.complementary_product_id
        WHERE cph.product_id = $1
        ORDER BY cph.co_purchase_count DESC
        LIMIT 5
        """,
        product_id,
    )

    complementary = []
    for row in rows:
        product = _row_to_product(row)
        product["co_purchase_count"] = row["co_purchase_count"]
        complementary.append(product)

    return {
        "product_id": product_id,
        "complementary_products": complementary,
        "count": len(complementary),
    }




async def check_customer_owns(
    conn: asyncpg.Connection,
    session_id: str,
    category: str,
) -> Dict[str, Any]:
    """Check whether the customer already has an item of a given
    *category* in their cart.

    For accessories the sub-category (e.g. ``'mouse'``, ``'bag'``) is stored
    inside the ``use_case`` text-array column.  This function checks whether
    any product in the cart contains *category* as an element of its
    ``use_case`` array.

    Returns::

        {"owns": bool, "items": [{"id": int, "name": str, "price": float}, ...]}
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT
            p.id,
            p.name,
            p.price
        FROM cart_items ci
        JOIN products p ON p.id = ci.product_id
        WHERE ci.session_id = $1
          AND $2 = ANY(p.use_case)
        """,
        session_id,
        category,
    )

    items = [
        {"id": row["id"], "name": row["name"], "price": _dec(row["price"])}
        for row in rows
    ]

    return {"owns": len(items) > 0, "items": items}




async def initiate_checkout(
    conn: asyncpg.Connection,
    session_id: str,
) -> Dict[str, Any]:
    """Create a new order from the current cart contents.

    * Fetches the cart via :func:`get_cart`.
    * Determines ``ai_assisted`` by checking whether any cart item's source
      is ``'ai_recommendation'`` or ``'ai_upsell'``.
    * Inserts a new row into ``orders`` with ``status = 'created'``.

    Returns on success::

        {"success": true, "order_id": N, "total": float,
         "item_count": N, "ai_assisted": bool}

    Returns when the cart is empty::

        {"success": false, "reason": "cart_is_empty"}
    """
    # Lock the cart rows while creating an order snapshot.  Subsequent payment
    # processing uses order_items, never the mutable live cart.
    async with conn.transaction():
        rows = await conn.fetch(
            """
            SELECT ci.product_id, ci.quantity, ci.source, p.name AS product_name, p.price
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.session_id = $1
            ORDER BY ci.created_at ASC
            FOR UPDATE OF ci
            """,
            session_id,
        )

        if not rows:
            return {"success": False, "reason": "cart_is_empty"}

        cart_items = []
        total = 0.0
        item_count = 0
        for row in rows:
            price = _dec(row["price"])
            quantity = row["quantity"]
            subtotal = round(price * quantity, 2)
            total += subtotal
            item_count += quantity
            cart_items.append({
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "price": price,
                "quantity": quantity,
                "source": row["source"],
                "subtotal": subtotal,
            })

        ai_sources = {"ai_recommendation", "ai_upsell"}
        ai_assisted = any(item["source"] in ai_sources for item in cart_items)

    # ── Upsell attribution ──────────────────────────────────────
    # Check for ai_upsell items to populate upsell metrics
        upsell_items = [i for i in cart_items if i["source"] == "ai_upsell"]
        upsell_accepted = len(upsell_items) > 0
        upsell_amount = round(sum(i["subtotal"] for i in upsell_items), 2) if upsell_accepted else None

    # Pick the first ai_recommendation-sourced item for the attribution column.
    # Assumption: ai_recommended_product_id is single-valued; if there are
    # multiple AI-recommended items we take the first one.
        ai_rec_items = [i for i in cart_items if i["source"] == "ai_recommendation"]
        ai_recommended_product_id = ai_rec_items[0]["product_id"] if ai_rec_items else None

        order_id = await conn.fetchval(
        """
        INSERT INTO orders
            (session_id, total, ai_assisted, status,
             upsell_accepted, upsell_amount, ai_recommended_product_id)
        VALUES ($1, $2, $3, 'created', $4, $5, $6)
        RETURNING id
        """,
            session_id,
            round(total, 2),
            ai_assisted,
            upsell_accepted,
            upsell_amount,
            ai_recommended_product_id,
        )
        await conn.executemany(
            """
            INSERT INTO order_items (order_id, product_id, quantity, source, unit_price)
            VALUES ($1, $2, $3, $4, $5)
            """,
            [
                (order_id, item["product_id"], item["quantity"], item["source"], item["price"])
                for item in cart_items
            ],
        )

    return {
        "success": True,
        "order_id": order_id,
        "total": round(total, 2),
        "item_count": item_count,
        "ai_assisted": ai_assisted,
    }




async def remove_from_cart(
    conn: asyncpg.Connection,
    session_id: str,
    product_id: int,
) -> Dict[str, Any]:
    """Remove a specific product from the customer's cart.

    Returns::

        {"success": true, "removed": {"product_id": N, "product_name": str}}
        or {"success": false, "reason": "not_in_cart", "product_id": N}
    """
    row = await conn.fetchrow(
        """
        DELETE FROM cart_items
        WHERE id = (
            SELECT ci.id FROM cart_items ci
            WHERE ci.session_id = $1 AND ci.product_id = $2
            LIMIT 1
        )
        RETURNING product_id
        """,
        session_id,
        product_id,
    )

    if row is None:
        return {
            "success": False,
            "reason": "not_in_cart",
            "product_id": product_id,
        }

    # Fetch product name for friendly message
    product_name = await conn.fetchval(
        "SELECT name FROM products WHERE id = $1",
        product_id,
    )

    return {
        "success": True,
        "removed": {
            "product_id": product_id,
            "product_name": product_name or f"Product #{product_id}",
        },
    }




async def clear_cart(
    conn: asyncpg.Connection,
    session_id: str,
) -> Dict[str, Any]:
    """Remove all items from the customer's shopping cart.

    Returns::

        {"success": true, "items_removed": N}
    """
    status = await conn.execute(
        "DELETE FROM cart_items WHERE session_id = $1",
        session_id,
    )

    # status is 'DELETE N'
    count = 0
    try:
        count = int(status.split()[1])
    except (IndexError, ValueError):
        count = 0

    return {
        "success": True,
        "items_removed": count,
    }




async def get_pre_checkout_suggestions(
    conn: asyncpg.Connection,
    session_id: str,
) -> Dict[str, Any]:
    """Get personalized accessory suggestions based on the current cart.

    Looks at all laptops in the cart, finds complementary products from
    co_purchase_history, filters out items already in the cart, deduplicates
    across laptops, and returns a clean list ordered by popularity.

    Returns::

        {
            "suggestions": [...],
            "count": N,
            "cart_total": float,
            "cart_item_count": int,
        }
    """
    # 1. Get all items currently in the cart
    cart_rows = await conn.fetch(
        """
        SELECT ci.product_id, p.name, p.price, p.category, ci.quantity
        FROM cart_items ci
        JOIN products p ON p.id = ci.product_id
        WHERE ci.session_id = $1
        ORDER BY ci.created_at ASC
        """,
        session_id,
    )

    if not cart_rows:
        return {
            "suggestions": [],
            "count": 0,
            "cart_total": 0,
            "cart_item_count": 0,
        }

    # Calculate cart summary
    cart_product_ids = set()
    cart_total = 0.0
    cart_item_count = 0
    laptop_ids: List[int] = []

    for row in cart_rows:
        cart_product_ids.add(row["product_id"])
        cart_total += _dec(row["price"]) * row["quantity"]
        cart_item_count += row["quantity"]
        if row["category"] == "laptop":
            laptop_ids.append(row["product_id"])

    if not laptop_ids:
        # No laptops in cart — nothing to suggest
        return {
            "suggestions": [],
            "count": 0,
            "cart_total": round(cart_total, 2),
            "cart_item_count": cart_item_count,
        }

    # 2. Find complementary products for ALL laptops in the cart, deduplicated
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (p.id)
            p.id,
            p.name,
            p.price,
            p.category,
            p.ram_gb,
            p.gpu,
            p.cpu,
            p.use_case,
            p.stock,
            cph.co_purchase_count,
            anchor.name AS anchor_product_name
        FROM co_purchase_history cph
        JOIN products p ON p.id = cph.complementary_product_id
        JOIN products anchor ON anchor.id = cph.product_id
        WHERE cph.product_id = ANY($1)
          AND p.stock > 0
          AND p.id != ALL($2)
        ORDER BY p.id, cph.co_purchase_count DESC
        """,
        laptop_ids,
        list(cart_product_ids),
    )

    # 3. Build suggestions list sorted by co-purchase frequency
    suggestions = []
    for row in rows:
        suggestions.append({
            "id": row["id"],
            "name": row["name"],
            "price": _dec(row["price"]),
            "category": row["category"],
            "co_purchase_count": row["co_purchase_count"],
            "relevance": f"Frequently bought with {row['anchor_product_name']}",
        })

    # Sort by popularity (highest co-purchase count first)
    suggestions.sort(key=lambda x: x["co_purchase_count"], reverse=True)

    # Limit to top 5 suggestions
    suggestions = suggestions[:5]

    return {
        "suggestions": suggestions,
        "count": len(suggestions),
        "cart_total": round(cart_total, 2),
        "cart_item_count": cart_item_count,
    }


# Dispatch table

TOOL_DISPATCH: Dict[str, Any] = {
    "search_products": search_products,
    "compare_products": compare_products,
    "get_cart": get_cart,
    "add_to_cart": add_to_cart,
    "get_complementary_products": get_complementary_products,
    "check_customer_owns": check_customer_owns,
    "initiate_checkout": initiate_checkout,
    "remove_from_cart": remove_from_cart,
    "clear_cart": clear_cart,
    "get_pre_checkout_suggestions": get_pre_checkout_suggestions,
}
