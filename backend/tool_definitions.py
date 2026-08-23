"""
tool_definitions.py
====================
Provider-agnostic JSON tool definitions for the ShopMind AI shopping assistant.

Each tool is described as a plain dict following the JSON Schema convention so
it can be trivially adapted to OpenAI function-calling, Anthropic tool-use,
Google Gemini, or any other LLM provider.

Exports
-------
TOOL_DEFINITIONS : list[dict]
    Complete schema for every tool the agent may invoke.
TOOLS_REQUIRING_CONFIRMATION : dict[str, str]
    Maps tool names that mutate state to a human-readable template
    describing the action.  Templates may contain ``{product_name}``
    and ``{quantity}`` placeholders for runtime interpolation.
"""

from typing import Any

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # 1. search_products
    {
        "name": "search_products",
        "description": (
            "Search for laptops matching the given criteria. "
            "All parameters are optional — omit any to not filter by that criterion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "budget_max": {
                    "type": "number",
                    "description": "Maximum budget in the store's base currency.",
                },
                "use_cases": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "coding",
                            "ml",
                            "gaming",
                            "general",
                            "video_editing",
                        ],
                    },
                    "description": (
                        "Intended use-cases to filter by. "
                        "Accepts one or more of: coding, ml, gaming, general, video_editing."
                    ),
                },
                "min_ram_gb": {
                    "type": "integer",
                    "description": "Minimum RAM in gigabytes.",
                },
            },
            "required": [],
        },
    },
    # 2. compare_products
    {
        "name": "compare_products",
        "description": (
            "Get detailed specs for multiple products to compare them side by side."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "product_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of product IDs to compare.",
                },
            },
            "required": ["product_ids"],
        },
    },
    # 3. get_cart
    {
        "name": "get_cart",
        "description": (
            "Get all items currently in the shopping cart with product details and total."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Active shopping session identifier.",
                },
            },
            "required": ["session_id"],
        },
    },
    # 4. add_to_cart
    {
        "name": "add_to_cart",
        "description": (
            "Add a product to the customer's cart. "
            "This action requires explicit user confirmation before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Active shopping session identifier.",
                },
                "product_id": {
                    "type": "integer",
                    "description": "ID of the product to add.",
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of units to add (defaults to 1).",
                    "default": 1,
                },
                "source": {
                    "type": "string",
                    "enum": ["ai_recommendation", "ai_upsell", "organic"],
                    "description": (
                        "Attribution source indicating how the product was surfaced: "
                        "ai_recommendation, ai_upsell, or organic."
                    ),
                },
            },
            "required": ["session_id", "product_id", "source"],
        },
    },
    # 5. get_complementary_products
    {
        "name": "get_complementary_products",
        "description": (
            "Get accessories and complementary products that are frequently "
            "purchased alongside the given product, ordered by popularity."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "integer",
                    "description": "ID of the anchor product to find complements for.",
                },
            },
            "required": ["product_id"],
        },
    },
    # 6. check_customer_owns
    {
        "name": "check_customer_owns",
        "description": (
            "Check whether the customer's cart already contains a product in "
            "the given accessory category. Use this before suggesting an upsell "
            "to avoid redundant recommendations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Active shopping session identifier.",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Accessory category to check, e.g. 'mouse', 'cooling_pad', "
                        "'bag', 'monitor', 'keyboard', 'headset'."
                    ),
                },
            },
            "required": ["session_id", "category"],
        },
    },
    # 7. initiate_checkout
    {
        "name": "initiate_checkout",
        "description": (
            "Calculate cart total and initiate the checkout/payment process. "
            "This action requires explicit user confirmation before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Active shopping session identifier.",
                },
            },
            "required": ["session_id"],
        },
    },
]

# ---------------------------------------------------------------------------
# Confirmation gate
# ---------------------------------------------------------------------------

TOOLS_REQUIRING_CONFIRMATION: dict[str, str] = {
    "add_to_cart": "Add {product_name} (×{quantity}) to your cart",
    "initiate_checkout": "Proceed to checkout and payment",
}
"""
Tools whose execution must be preceded by an explicit user confirmation step.

Values are human-readable action descriptions.  Use ``str.format()`` /
``str.format_map()`` at runtime to interpolate context-specific placeholders
such as ``{product_name}`` and ``{quantity}``.
"""
