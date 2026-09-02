"""Provider-agnostic JSON tool definitions for the ShopMind AI shopping assistant."""

from typing import Any

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [

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

    {
        "name": "remove_from_cart",
        "description": (
            "Remove a specific product from the customer's shopping cart."
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
                    "description": "ID of the product to remove from the cart.",
                },
            },
            "required": ["session_id", "product_id"],
        },
    },

    {
        "name": "clear_cart",
        "description": (
            "Remove all items from the customer's shopping cart, emptying it completely."
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

    {
        "name": "get_pre_checkout_suggestions",
        "description": (
            "Get personalized accessory and complementary product suggestions "
            "based on the current cart contents. Call this BEFORE initiating "
            "checkout to suggest relevant add-ons the customer might want. "
            "Returns suggestions filtered to exclude items already in the cart."
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

