"""
agent_loop.py — Core agent loop for ShopMind AI.

This module implements the LLM agent loop: given a user message and
conversation history, it calls the LLM, dispatches tool calls, handles
confirmation gating, logs every action, and repeats until the LLM
produces a final text response.

The loop is provider-agnostic — it delegates LLM calls to an adapter
(e.g., GeminiAdapter) and tool execution to the TOOL_DISPATCH registry.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import asyncpg

from backend.adapters.gemini_adapter import GeminiAdapter, LLMResponse
from backend.logging_middleware import log_tool_call
from backend.tool_definitions import TOOL_DEFINITIONS, TOOLS_REQUIRING_CONFIRMATION
from backend.tools import TOOL_DISPATCH

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

MAX_TOOL_CALLS_PER_TURN = 10

SYSTEM_PROMPT = """\
You are ShopMind AI, a helpful and knowledgeable laptop shopping assistant.
You help customers find the right laptop based on their needs, budget, and
preferences.

CRITICAL RULES — follow these exactly:

1. You have NO built-in knowledge of products, prices, stock levels, or
   inventory. You MUST call tools (search_products, compare_products, etc.)
   to learn about available products. NEVER invent or guess product names,
   prices, or specifications.

2. Only state facts that were returned by a tool call. If a tool returned
   no results, say so honestly — do not fabricate alternatives.

3. Before suggesting an upsell or complementary product (mouse, cooling pad,
   bag, etc.), ALWAYS call check_customer_owns first to see if the customer
   already has one in their cart. If they do, skip the upsell gracefully.

4. When recommending products, be conversational and explain WHY a product
   fits the customer's needs. Don't just list specs — connect them to the
   customer's stated use case.

5. When the customer wants to add something to cart, call add_to_cart with
   the correct source:
   - 'ai_recommendation' if you suggested the product
   - 'ai_upsell' if it's a complementary/accessory suggestion
   - 'organic' if the customer found/requested it themselves

6. When the customer wants to checkout or pay, call initiate_checkout.

7. If a tool call fails (e.g., out of stock), explain the situation clearly
   to the customer and proactively offer alternatives by calling
   search_products again with adjusted parameters.

8. Be concise, friendly, and helpful. Use ₹ for prices (Indian Rupees).

9. The session_id for this conversation is: {session_id}
   Always use this session_id when calling tools that require it.

10. NUMBERED LISTS & MULTIPLE PRODUCTS: When suggesting or listing multiple products, ALWAYS number them clearly (e.g., 1. [Product A], 2. [Product B]). If the customer responds by saying "add number 2", "add 1 and 2", or "add product 1, 2", YOU MUST correctly map each number to its exact `product_id` and call `add_to_cart` for EACH product requested. Never omit any requested products.

11. AUTOMATIC UPSELLS: IMMEDIATELY after successfully adding a laptop or main product to the cart, DO NOT ask "what else do you want?". Instead, AUTOMATICALLY call `get_complementary_products` to find related items (like bags, mice, cooling pads), check if they already have them (Rule 3), and directly suggest adding them to the cart in the same response.

12. CART REMOVAL & CLEAR: When a customer asks to remove an item from their cart, call remove_from_cart with the product_id. When they ask to clear or empty their entire cart, call clear_cart. These actions execute immediately without requiring confirmation.

13. PRE-CHECKOUT SUGGESTIONS: When the customer requests checkout/payment:
   a) If they also asked to add products, add those first.
   b) ONLY call `get_pre_checkout_suggestions` the FIRST time the customer requests checkout in this conversation. If you have already called `get_pre_checkout_suggestions` earlier in this conversation (whether the customer added items from the suggestions or skipped them), do NOT call it again — proceed directly to `initiate_checkout`.
   c) If suggestions are returned (count > 0), present them as a numbered list with prices and briefly explain why each fits the customer's cart. Ask: "Would you like to add any of these? Just say the number(s), or say 'no thanks' to proceed to checkout."
   d) If the user picks items, add them with source='ai_upsell', then call `initiate_checkout`.
   e) If the user says skip/no/no thanks, call `initiate_checkout` directly.
   f) If `get_pre_checkout_suggestions` returns 0 suggestions, proceed directly to `initiate_checkout`.

14. PRE-CHECKOUT PRESENTATION STYLE: When presenting pre-checkout suggestions:
   - Number each suggestion clearly (1, 2, 3...)
   - Show the price in ₹
   - Briefly explain why it's relevant (e.g., "pairs great with your gaming laptop for precise control")
   - Always give the user the option to skip ("or say 'no thanks' to proceed to checkout")
   - Keep it helpful and conversational, never pushy or aggressive
"""


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass
class PendingActionItem:
    """A single tool call that requires user confirmation before executing."""
    tool_name: str
    tool_args: dict
    description: str
    ai_action_log_id: Optional[int] = None
    call_id: Optional[str] = None


@dataclass
class PendingConfirmation:
    """A tool call (or batch of tool calls) that requires user confirmation before executing."""
    action_id: str
    tool_name: str
    tool_args: dict
    description: str
    ai_action_log_id: Optional[int] = None
    actions: List[PendingActionItem] = field(default_factory=list)


@dataclass
class AgentResponse:
    """The result of running the agent loop for one user turn."""
    type: str  # "text", "pending_confirmation", "error"
    content: str  # The agent's text response or description
    pending_action: Optional[PendingConfirmation] = None
    conversation_history: list = field(default_factory=list)
    tool_calls_made: int = 0
    tool_result: Optional[dict] = None  # Result from a confirmed tool execution


# ──────────────────────────────────────────────
# Helper: execute a tool
# ──────────────────────────────────────────────

async def _execute_tool(
    conn: asyncpg.Connection,
    tool_name: str,
    tool_args: dict,
) -> dict:
    """Execute a tool function from TOOL_DISPATCH and return the result."""
    func = TOOL_DISPATCH.get(tool_name)
    if func is None:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        # All tool functions expect conn as first arg, then keyword args
        result = await func(conn, **tool_args)
        return result
    except Exception as e:
        return {"error": f"Tool execution failed: {str(e)}"}


async def _get_confirmation_description(
    conn: Optional[asyncpg.Connection],
    tool_name: str,
    tool_args: dict,
) -> str:
    """Build a human-readable description for a confirmation-gated action."""
    template = TOOLS_REQUIRING_CONFIRMATION.get(tool_name, "Perform action: {tool_name}")

    if tool_name == "add_to_cart":
        pid = tool_args.get("product_id")
        pname = None
        if pid is not None and conn is not None:
            try:
                pname = await conn.fetchval(
                    "SELECT name FROM products WHERE id = $1",
                    int(pid),
                )
            except Exception:
                pname = None
        product_label = pname if pname else f"Product #{pid or '?'}"
        return template.format(
            product_name=product_label,
            quantity=tool_args.get("quantity", 1),
        )
    elif tool_name == "initiate_checkout":
        return template
    else:
        return f"Execute {tool_name}"


def _combine_descriptions(items: List[PendingActionItem]) -> str:
    """Combine descriptions for multiple confirmation-gated actions into a single coherent phrase."""
    if not items:
        return "proceed with requested action"
    if len(items) == 1:
        return items[0].description

    all_add = all(item.tool_name == "add_to_cart" for item in items)
    if all_add:
        product_labels = []
        for item in items:
            desc = item.description
            if desc.startswith("Add ") and desc.endswith(" to your cart"):
                product_labels.append(desc[4:-13])
            else:
                product_labels.append(desc)
        if len(product_labels) == 2:
            combined = f"{product_labels[0]} and {product_labels[1]}"
        else:
            combined = f"{', '.join(product_labels[:-1])}, and {product_labels[-1]}"
        return f"Add {combined} to your cart"

    descs = [item.description for item in items]
    if len(descs) == 2:
        return f"{descs[0]} and {descs[1]}"
    return f"{', '.join(descs[:-1])}, and {descs[-1]}"


def _get_tool_param_names(tool_name: str) -> set:
    """Get the parameter names for a tool from its definition."""
    for tool_def in TOOL_DEFINITIONS:
        if tool_def["name"] == tool_name:
            return set(tool_def["parameters"].get("properties", {}).keys())
    return set()


# ──────────────────────────────────────────────
# Core unified agent loop
# ──────────────────────────────────────────────

async def _run_loop(
    conversation_history: list,
    session_id: str,
    conn: asyncpg.Connection,
    adapter: GeminiAdapter,
    tool_calls_count: int = 0,
    confirmed_result: Optional[dict] = None,
    is_chained: bool = False,
) -> AgentResponse:
    """
    Execute turns of the agent loop until the LLM returns text or pauses for confirmation.
    Handles multiple simultaneous tool calls, confirmation gates, and error handling.
    """
    while tool_calls_count < MAX_TOOL_CALLS_PER_TURN:
        try:
            llm_response: LLMResponse = await adapter.call_llm(
                conversation_history=conversation_history,
                tool_definitions=TOOL_DEFINITIONS,
                system_instruction=SYSTEM_PROMPT.format(session_id=session_id),
            )
        except Exception as e:
            return AgentResponse(
                type="error",
                content=f"I'm having trouble connecting to my AI backend. Please try again in a moment. (Error: {str(e)})",
                conversation_history=conversation_history,
                tool_calls_made=tool_calls_count,
                tool_result=confirmed_result,
            )

        # Append model's response turn to history
        if llm_response.candidate_content:
            conversation_history.append(llm_response.candidate_content)
        elif llm_response.raw_parts:
            model_content = adapter.build_model_content(llm_response.raw_parts)
            conversation_history.append(model_content)

        # Case A: Text response and no tool calls
        if llm_response.text and not llm_response.tool_calls:
            return AgentResponse(
                type="text",
                content=llm_response.text,
                conversation_history=conversation_history,
                tool_calls_made=tool_calls_count,
                tool_result=confirmed_result,
            )

        # Case B: No text and no tool calls
        if not llm_response.tool_calls:
            return AgentResponse(
                type="text",
                content="I'm not sure how to help with that. Could you tell me more about what you're looking for in a laptop?",
                conversation_history=conversation_history,
                tool_calls_made=tool_calls_count,
                tool_result=confirmed_result,
            )

        # Case C: Tool calls
        gated_calls = []
        non_gated_calls = []

        for tool_call in llm_response.tool_calls:
            t_name = tool_call.name
            t_args = dict(tool_call.args) if tool_call.args else {}
            if "session_id" in _get_tool_param_names(t_name):
                t_args["session_id"] = session_id

            if t_name in TOOLS_REQUIRING_CONFIRMATION:
                gated_calls.append((tool_call, t_name, t_args))
            else:
                non_gated_calls.append((tool_call, t_name, t_args))

        # If any tool in this turn requires confirmation, pause and collect all
        if gated_calls:
            action_id = str(uuid.uuid4())
            pending_items: List[PendingActionItem] = []

            for tool_call, t_name, t_args in gated_calls:
                desc = await _get_confirmation_description(conn, t_name, t_args)
                ai_log_id = await log_tool_call(
                    conn=conn,
                    session_id=session_id,
                    tool_name=t_name,
                    tool_input=t_args,
                    tool_output={"status": "awaiting_confirmation"},
                    decision=f"Agent wants to {desc}. Awaiting user confirmation.",
                    user_approved=None,
                    success=True,
                )
                pending_items.append(
                    PendingActionItem(
                        tool_name=t_name,
                        tool_args=t_args,
                        description=desc,
                        ai_action_log_id=ai_log_id,
                        call_id=tool_call.id,
                    )
                )

            # Include any non-gated calls in this turn into the pending batch
            for tool_call, t_name, t_args in non_gated_calls:
                pending_items.append(
                    PendingActionItem(
                        tool_name=t_name,
                        tool_args=t_args,
                        description=f"Execute {t_name}",
                        ai_action_log_id=None,
                        call_id=tool_call.id,
                    )
                )

            gated_only = [item for item in pending_items if item.tool_name in TOOLS_REQUIRING_CONFIRMATION]
            combined_desc = _combine_descriptions(gated_only)
            primary = gated_only[0]

            pending_action = PendingConfirmation(
                action_id=action_id,
                tool_name=primary.tool_name,
                tool_args=primary.tool_args,
                description=combined_desc,
                ai_action_log_id=primary.ai_action_log_id,
                actions=pending_items,
            )

            prompt_prefix = "I'd also like to" if is_chained else "I'd like to"
            content = f"{prompt_prefix} **{combined_desc.lower()}**. Shall I go ahead?"

            return AgentResponse(
                type="pending_confirmation",
                content=content,
                pending_action=pending_action,
                conversation_history=conversation_history,
                tool_calls_made=tool_calls_count + len(pending_items),
                tool_result=confirmed_result,
            )

        # All tool calls in this turn are non-gated
        tool_response_parts = []
        for tool_call, t_name, t_args in non_gated_calls:
            if tool_calls_count >= MAX_TOOL_CALLS_PER_TURN:
                return AgentResponse(
                    type="text",
                    content="I've reached my research limit for this request. Please narrow it down or ask a follow-up.",
                    conversation_history=conversation_history,
                    tool_calls_made=tool_calls_count,
                    tool_result=confirmed_result,
                )
            tool_calls_count += 1

            result = await _execute_tool(conn, t_name, t_args)
            success = "error" not in result

            await log_tool_call(
                conn=conn,
                session_id=session_id,
                tool_name=t_name,
                tool_input=t_args,
                tool_output=result,
                decision=f"Called {t_name} to gather information.",
                user_approved=None,
                success=success,
            )

            tool_response_parts.append(
                adapter.build_tool_response(
                    tool_name=t_name,
                    result=result,
                    call_id=tool_call.id,
                )
            )

        if tool_response_parts:
            tool_content = adapter.build_tool_response_content(tool_response_parts)
            conversation_history.append(tool_content)

    return AgentResponse(
        type="text",
        content=(
            "I've done extensive research for you! Let me summarize what I found. "
            "If you need more specific information, feel free to ask."
        ),
        conversation_history=conversation_history,
        tool_calls_made=tool_calls_count,
        tool_result=confirmed_result,
    )


# ──────────────────────────────────────────────
# Main agent loop
# ──────────────────────────────────────────────

async def run_agent(
    user_message: str,
    session_id: str,
    conversation_history: list,
    conn: asyncpg.Connection,
    adapter: GeminiAdapter,
) -> AgentResponse:
    """
    Run the agent loop for a single user turn.

    1. Appends the user message to conversation history.
    2. Runs the loop: calls LLM, handles tool calls & confirmation gates.
    3. Returns AgentResponse with final text or pending confirmation.
    """
    user_content = adapter.build_user_message(user_message)
    conversation_history.append(user_content)

    return await _run_loop(
        conversation_history=conversation_history,
        session_id=session_id,
        conn=conn,
        adapter=adapter,
        tool_calls_count=0,
        is_chained=False,
    )


# ──────────────────────────────────────────────
# Confirmation handling
# ──────────────────────────────────────────────

async def execute_confirmed_action(
    pending: PendingConfirmation,
    session_id: str,
    conversation_history: list,
    conn: asyncpg.Connection,
    adapter: GeminiAdapter,
) -> AgentResponse:
    """
    Execute previously-gated tool calls after user confirmation.

    Runs each confirmed tool, updates audit logs in ai_actions, feeds
    all tool results back to the LLM turn, and continues the loop.
    """
    actions_to_execute = pending.actions if pending.actions else [
        PendingActionItem(
            tool_name=pending.tool_name,
            tool_args=pending.tool_args,
            description=pending.description,
            ai_action_log_id=pending.ai_action_log_id,
        )
    ]

    tool_response_parts = []
    confirmed_result = None

    for item in actions_to_execute:
        result = await _execute_tool(conn, item.tool_name, item.tool_args)
        success = "error" not in result and result.get("success", True)

        if item.tool_name == "initiate_checkout" or confirmed_result is None:
            confirmed_result = {"tool_name": item.tool_name, **result}

        # Update existing log entry for gated tools
        if item.ai_action_log_id:
            await conn.execute(
                """
                UPDATE ai_actions
                SET user_approved = TRUE,
                    output = $1,
                    success = $2,
                    decision = decision || ' → User confirmed. ' || $3
                WHERE id = $4
                """,
                json.dumps(result),
                success,
                f"Result: {'success' if success else 'failed'}",
                item.ai_action_log_id,
            )
        else:
            # Non-gated tool that was batched
            await log_tool_call(
                conn=conn,
                session_id=session_id,
                tool_name=item.tool_name,
                tool_input=item.tool_args,
                tool_output=result,
                decision=f"Called {item.tool_name} after confirmed action.",
                user_approved=None,
                success=success,
            )

        tool_response_parts.append(
            adapter.build_tool_response(
                tool_name=item.tool_name,
                result=result,
                call_id=item.call_id,
            )
        )

    # Append all tool responses to history
    tool_content = adapter.build_tool_response_content(tool_response_parts)
    conversation_history.append(tool_content)

    # Continue the loop
    return await _run_loop(
        conversation_history=conversation_history,
        session_id=session_id,
        conn=conn,
        adapter=adapter,
        tool_calls_count=len(actions_to_execute),
        confirmed_result=confirmed_result,
        is_chained=True,
    )


async def cancel_action(
    pending: PendingConfirmation,
    session_id: str,
    conversation_history: list,
    conn: asyncpg.Connection,
    adapter: GeminiAdapter,
) -> AgentResponse:
    """
    Handle user cancellation of confirmation-gated actions.

    Logs cancellation to ai_actions, feeds cancellation tool results
    back to the LLM, and gets the agent's friendly follow-up response.
    """
    actions_to_cancel = pending.actions if pending.actions else [
        PendingActionItem(
            tool_name=pending.tool_name,
            tool_args=pending.tool_args,
            description=pending.description,
            ai_action_log_id=pending.ai_action_log_id,
        )
    ]

    tool_response_parts = []
    for item in actions_to_cancel:
        if item.ai_action_log_id:
            await conn.execute(
                """
                UPDATE ai_actions
                SET user_approved = FALSE,
                    output = '{"status": "cancelled_by_user"}'::jsonb,
                    decision = decision || ' → User declined.'
                WHERE id = $1
                """,
                item.ai_action_log_id,
            )

        cancel_result = {
            "status": "cancelled_by_user",
            "message": f"The user declined to {item.description.lower()}.",
        }
        tool_response_parts.append(
            adapter.build_tool_response(
                tool_name=item.tool_name,
                result=cancel_result,
                call_id=item.call_id,
            )
        )

    tool_content = adapter.build_tool_response_content(tool_response_parts)
    conversation_history.append(tool_content)

    # Ask the LLM for a follow-up response
    try:
        llm_response = await adapter.call_llm(
            conversation_history=conversation_history,
            tool_definitions=TOOL_DEFINITIONS,
            system_instruction=SYSTEM_PROMPT.format(session_id=session_id),
        )
        if llm_response.candidate_content:
            conversation_history.append(llm_response.candidate_content)
        elif llm_response.raw_parts:
            model_content = adapter.build_model_content(llm_response.raw_parts)
            conversation_history.append(model_content)
        text = llm_response.text or "No problem! Let me know if you change your mind or need anything else."
    except Exception:
        text = "No problem! Is there anything else I can help you with?"

    return AgentResponse(
        type="text",
        content=text,
        conversation_history=conversation_history,
    )

