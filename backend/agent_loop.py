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

MAX_TOOL_CALLS_PER_TURN = 6

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
"""


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass
class PendingConfirmation:
    """A tool call that requires user confirmation before executing."""
    action_id: str
    tool_name: str
    tool_args: dict
    description: str
    ai_action_log_id: Optional[int] = None


@dataclass
class AgentResponse:
    """The result of running the agent loop for one user turn."""
    type: str  # "text", "pending_confirmation", "error"
    content: str  # The agent's text response or description
    pending_action: Optional[PendingConfirmation] = None
    conversation_history: list = field(default_factory=list)
    tool_calls_made: int = 0


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


def _get_confirmation_description(tool_name: str, tool_args: dict) -> str:
    """Build a human-readable description for a confirmation-gated action."""
    template = TOOLS_REQUIRING_CONFIRMATION.get(tool_name, "Perform action: {tool_name}")

    if tool_name == "add_to_cart":
        # We'll fill in product_name later, for now use product_id
        return template.format(
            product_name=f"Product #{tool_args.get('product_id', '?')}",
            quantity=tool_args.get("quantity", 1),
        )
    elif tool_name == "initiate_checkout":
        return template
    else:
        return f"Execute {tool_name}"


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
    2. Calls the LLM with the history and tool definitions.
    3. If the LLM returns a text response → return it.
    4. If the LLM returns tool calls:
       a. For confirmation-gated tools → pause and return pending_confirmation.
       b. For other tools → execute, log, append results, continue loop.
    5. Caps at MAX_TOOL_CALLS_PER_TURN to prevent runaway loops.

    Args:
        user_message: The user's chat message.
        session_id: Shopping session identifier.
        conversation_history: Mutable list of conversation turns (modified in place).
        conn: asyncpg database connection.
        adapter: The LLM adapter (e.g., GeminiAdapter).

    Returns:
        AgentResponse with the agent's reply or a pending confirmation request.
    """
    # Append user message to history
    user_content = adapter.build_user_message(user_message)
    conversation_history.append(user_content)

    tool_calls_count = 0

    for iteration in range(MAX_TOOL_CALLS_PER_TURN):
        # Call the LLM
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
            )

        # Append the model's response to history (for both text and tool calls)
        if llm_response.candidate_content:
            conversation_history.append(llm_response.candidate_content)
        elif llm_response.raw_parts:
            model_content = adapter.build_model_content(llm_response.raw_parts)
            conversation_history.append(model_content)

        # ── Case A: LLM returned a text response ──
        if llm_response.text and not llm_response.tool_calls:
            return AgentResponse(
                type="text",
                content=llm_response.text,
                conversation_history=conversation_history,
                tool_calls_made=tool_calls_count,
            )

        # ── Case B: LLM wants to call tools ──
        if not llm_response.tool_calls:
            # Edge case: no text and no tool calls
            return AgentResponse(
                type="text",
                content="I'm not sure how to help with that. Could you tell me more about what you're looking for in a laptop?",
                conversation_history=conversation_history,
                tool_calls_made=tool_calls_count,
            )

        tool_response_parts = []

        for tool_call in llm_response.tool_calls:
            tool_calls_count += 1
            tool_name = tool_call.name
            tool_args = dict(tool_call.args) if tool_call.args else {}

            # Inject session_id for tools that need it
            if "session_id" in _get_tool_param_names(tool_name):
                tool_args["session_id"] = session_id

            # ── Check if this tool requires confirmation ──
            if tool_name in TOOLS_REQUIRING_CONFIRMATION:
                description = _get_confirmation_description(tool_name, tool_args)
                action_id = str(uuid.uuid4())

                # Log the pending action (user_approved=None means awaiting)
                ai_log_id = await log_tool_call(
                    conn=conn,
                    session_id=session_id,
                    tool_name=tool_name,
                    tool_input=tool_args,
                    tool_output={"status": "awaiting_confirmation"},
                    decision=f"Agent wants to {description}. Awaiting user confirmation.",
                    user_approved=None,
                    success=True,
                )

                return AgentResponse(
                    type="pending_confirmation",
                    content=f"I'd like to **{description.lower()}**. Shall I go ahead?",
                    pending_action=PendingConfirmation(
                        action_id=action_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        description=description,
                        ai_action_log_id=ai_log_id,
                    ),
                    conversation_history=conversation_history,
                    tool_calls_made=tool_calls_count,
                )

            # ── Execute non-gated tool ──
            result = await _execute_tool(conn, tool_name, tool_args)
            success = "error" not in result

            # Log the tool call
            await log_tool_call(
                conn=conn,
                session_id=session_id,
                tool_name=tool_name,
                tool_input=tool_args,
                tool_output=result,
                decision=f"Called {tool_name} to gather information.",
                user_approved=None,  # No confirmation needed
                success=success,
            )

            # Build tool response part for the LLM
            tool_response_parts.append(
                adapter.build_tool_response(
                    tool_name=tool_name,
                    result=result,
                    call_id=tool_call.id,
                )
            )

        # Append all tool responses to history
        if tool_response_parts:
            tool_content = adapter.build_tool_response_content(tool_response_parts)
            conversation_history.append(tool_content)

        # Continue the loop — LLM will process tool results next iteration

    # Max iterations reached
    return AgentResponse(
        type="text",
        content=(
            "I've done extensive research for you! Let me summarize what I found. "
            "If you need more specific information, feel free to ask."
        ),
        conversation_history=conversation_history,
        tool_calls_made=tool_calls_count,
    )


def _get_tool_param_names(tool_name: str) -> set:
    """Get the parameter names for a tool from its definition."""
    for tool_def in TOOL_DEFINITIONS:
        if tool_def["name"] == tool_name:
            return set(tool_def["parameters"].get("properties", {}).keys())
    return set()


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
    Execute a previously-gated tool call after user confirmation.

    This runs the tool, logs the result, feeds it back to the LLM,
    and continues the agent loop until the LLM produces a text response.
    """
    # Execute the confirmed tool
    result = await _execute_tool(conn, pending.tool_name, pending.tool_args)
    success = "error" not in result and result.get("success", True)

    # Update the existing log entry
    if pending.ai_action_log_id:
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
            pending.ai_action_log_id,
        )

    # Feed the tool result back to the LLM
    tool_response_part = adapter.build_tool_response(
        tool_name=pending.tool_name,
        result=result,
        call_id=None,
    )
    tool_content = adapter.build_tool_response_content([tool_response_part])
    conversation_history.append(tool_content)

    # Continue the agent loop to get the LLM's response to the tool result
    # (It might want to call more tools, e.g., suggest accessories after add_to_cart)
    remaining_calls = MAX_TOOL_CALLS_PER_TURN - 1  # Used one for the confirmed action

    for iteration in range(remaining_calls):
        try:
            llm_response = await adapter.call_llm(
                conversation_history=conversation_history,
                tool_definitions=TOOL_DEFINITIONS,
                system_instruction=SYSTEM_PROMPT.format(session_id=session_id),
            )
        except Exception as e:
            return AgentResponse(
                type="error",
                content=f"Something went wrong processing your request. Error: {str(e)}",
                conversation_history=conversation_history,
            )

        if llm_response.candidate_content:
            conversation_history.append(llm_response.candidate_content)
        elif llm_response.raw_parts:
            model_content = adapter.build_model_content(llm_response.raw_parts)
            conversation_history.append(model_content)

        # Text response — done
        if llm_response.text and not llm_response.tool_calls:
            return AgentResponse(
                type="text",
                content=llm_response.text,
                conversation_history=conversation_history,
            )

        if not llm_response.tool_calls:
            return AgentResponse(
                type="text",
                content="Done! Is there anything else I can help you with?",
                conversation_history=conversation_history,
            )

        # Process tool calls
        tool_response_parts = []
        for tool_call in llm_response.tool_calls:
            t_name = tool_call.name
            t_args = dict(tool_call.args) if tool_call.args else {}

            if "session_id" in _get_tool_param_names(t_name):
                t_args["session_id"] = session_id

            # If another confirmation-gated tool comes up, pause again
            if t_name in TOOLS_REQUIRING_CONFIRMATION:
                description = _get_confirmation_description(t_name, t_args)
                action_id = str(uuid.uuid4())

                ai_log_id = await log_tool_call(
                    conn=conn,
                    session_id=session_id,
                    tool_name=t_name,
                    tool_input=t_args,
                    tool_output={"status": "awaiting_confirmation"},
                    decision=f"Agent wants to {description}. Awaiting user confirmation.",
                    user_approved=None,
                    success=True,
                )

                return AgentResponse(
                    type="pending_confirmation",
                    content=f"I'd also like to **{description.lower()}**. Shall I go ahead?",
                    pending_action=PendingConfirmation(
                        action_id=action_id,
                        tool_name=t_name,
                        tool_args=t_args,
                        description=description,
                        ai_action_log_id=ai_log_id,
                    ),
                    conversation_history=conversation_history,
                )

            # Execute non-gated tool
            t_result = await _execute_tool(conn, t_name, t_args)
            t_success = "error" not in t_result

            await log_tool_call(
                conn=conn,
                session_id=session_id,
                tool_name=t_name,
                tool_input=t_args,
                tool_output=t_result,
                decision=f"Called {t_name} after confirmed action.",
                user_approved=None,
                success=t_success,
            )

            tool_response_parts.append(
                adapter.build_tool_response(
                    tool_name=t_name,
                    result=t_result,
                    call_id=tool_call.id,
                )
            )

        if tool_response_parts:
            tool_content = adapter.build_tool_response_content(tool_response_parts)
            conversation_history.append(tool_content)

    return AgentResponse(
        type="text",
        content="All done! Let me know if you need anything else.",
        conversation_history=conversation_history,
    )


async def cancel_action(
    pending: PendingConfirmation,
    session_id: str,
    conversation_history: list,
    conn: asyncpg.Connection,
    adapter: GeminiAdapter,
) -> AgentResponse:
    """
    Handle user cancellation of a confirmation-gated action.

    Logs the cancellation, feeds a 'cancelled' tool result to the LLM,
    and gets the agent's response.
    """
    # Update the log entry
    if pending.ai_action_log_id:
        await conn.execute(
            """
            UPDATE ai_actions
            SET user_approved = FALSE,
                output = '{"status": "cancelled_by_user"}'::jsonb,
                decision = decision || ' → User declined.'
            WHERE id = $1
            """,
            pending.ai_action_log_id,
        )

    # Feed cancellation result to LLM
    cancel_result = {
        "status": "cancelled_by_user",
        "message": f"The user declined to {pending.description.lower()}.",
    }
    tool_response_part = adapter.build_tool_response(
        tool_name=pending.tool_name,
        result=cancel_result,
        call_id=None,
    )
    tool_content = adapter.build_tool_response_content([tool_response_part])
    conversation_history.append(tool_content)

    # Get the agent's response to the cancellation
    try:
        llm_response = await adapter.call_llm(
            conversation_history=conversation_history,
            tool_definitions=TOOL_DEFINITIONS,
            system_instruction=SYSTEM_PROMPT.format(session_id=session_id),
        )
    except Exception as e:
        return AgentResponse(
            type="text",
            content="No problem! Is there anything else I can help you with?",
            conversation_history=conversation_history,
        )

    if llm_response.candidate_content:
        conversation_history.append(llm_response.candidate_content)
    elif llm_response.raw_parts:
        model_content = adapter.build_model_content(llm_response.raw_parts)
        conversation_history.append(model_content)

    text = llm_response.text or "No problem! Let me know if you change your mind or need anything else."

    return AgentResponse(
        type="text",
        content=text,
        conversation_history=conversation_history,
    )
