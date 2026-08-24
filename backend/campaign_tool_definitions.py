"""
campaign_tool_definitions.py
=============================
Provider-agnostic JSON tool definitions for the Campaign Orchestrator agent.

Follows the same JSON-Schema convention used by ``tool_definitions.py``
so the definitions can be converted to any LLM provider format via the
adapter layer.

Exports
-------
CAMPAIGN_TOOL_DEFINITIONS : list[dict]
    Complete schema for the three tools the campaign agent may invoke.
"""

from typing import Any

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

CAMPAIGN_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # 1. find_abandoned_carts
    {
        "name": "find_abandoned_carts",
        "description": (
            "Search for shopping carts that appear abandoned — carts with items "
            "that have not progressed to an order within a configurable time window. "
            "Returns candidate carts with their value, age, items, and customer info."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "min_age_minutes": {
                    "type": "integer",
                    "description": (
                        "Minimum number of minutes since the last cart activity "
                        "to consider a cart abandoned. Default: 30."
                    ),
                    "default": 30,
                },
                "min_cart_value": {
                    "type": "number",
                    "description": (
                        "Minimum total cart value (in INR) to include. "
                        "Carts below this value will be excluded. Default: 0."
                    ),
                    "default": 0,
                },
            },
            "required": [],
        },
    },
    # 2. get_cart_context
    {
        "name": "get_cart_context",
        "description": (
            "Get detailed cart contents, total value, and customer context for "
            "a specific session. Includes whether the customer is a first-time "
            "or returning buyer, which is useful for deciding nudge strategy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID whose cart to inspect.",
                },
            },
            "required": ["session_id"],
        },
    },
    # 3. record_campaign_decision
    {
        "name": "record_campaign_decision",
        "description": (
            "Record the campaign orchestrator's decision for a specific abandoned "
            "cart. This logs the decision to the audit trail and persists it in "
            "the campaign_actions table. Call this for EVERY cart evaluated — "
            "including when the decision is to take no action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID this decision applies to.",
                },
                "cart_snapshot": {
                    "type": "object",
                    "description": (
                        "A snapshot of the cart items at decision time. "
                        "Include the items array from get_cart_context."
                    ),
                },
                "cart_value": {
                    "type": "number",
                    "description": "Total cart value in INR.",
                },
                "cart_age_minutes": {
                    "type": "integer",
                    "description": "Minutes since the cart was last updated.",
                },
                "decision": {
                    "type": "string",
                    "description": (
                        "Your reasoning for this decision in plain English. "
                        "This is shown to the merchant in the dashboard audit "
                        "trail, so make it genuinely readable and specific — "
                        "e.g. 'Cart worth ₹84,990, abandoned 3 hours ago, "
                        "first-time customer — offering a small nudge without "
                        "discount since cart value doesn't yet justify margin loss.'"
                    ),
                },
                "action_taken": {
                    "type": "string",
                    "enum": ["no_action", "reminder", "discount_offer"],
                    "description": (
                        "The action to take: 'no_action' (skip this cart), "
                        "'reminder' (send a plain reminder), or "
                        "'discount_offer' (send a message with a discount)."
                    ),
                },
                "discount_percent": {
                    "type": "number",
                    "description": (
                        "Discount percentage to offer (only when action_taken "
                        "is 'discount_offer'). Must be between 1 and 15. "
                        "Leave null/omit for 'reminder' or 'no_action'."
                    ),
                },
                "simulated_channel": {
                    "type": "string",
                    "enum": ["email", "sms"],
                    "description": (
                        "The channel to use for the simulated nudge. "
                        "Use 'email' if the customer has an email on file, "
                        "'sms' if only phone is available. "
                        "Omit for 'no_action'."
                    ),
                },
            },
            "required": [
                "session_id",
                "cart_value",
                "cart_age_minutes",
                "decision",
                "action_taken",
            ],
        },
    },
]
