"""
campaign_agent.py — Campaign Orchestrator batch reasoning loop.
A proactive agent that scans for abandoned carts and uses LLM reasoning to decide whether/how to send recovery nudges.
"""

from __future__ import annotations


import json
import logging
from typing import Any, Dict, List

import asyncpg

from backend.adapters.gemini_adapter import GeminiAdapter, LLMResponse
from backend.campaign_tool_definitions import CAMPAIGN_TOOL_DEFINITIONS
from backend.campaign_tools import (
    CAMPAIGN_AGENT_NAME,
    CAMPAIGN_TOOL_DISPATCH,
    find_abandoned_carts,
    get_cart_context,
    record_campaign_decision,
)
from backend.logging_middleware import log_tool_call

logger = logging.getLogger(__name__)

MAX_CARTS_PER_RUN = 20
"""Maximum number of abandoned carts to evaluate per orchestrator run."""

MAX_DISCOUNT_PERCENT = 15
"""Hard cap on discount percentage the orchestrator may offer."""

DEFAULT_MIN_AGE_MINUTES = 30
"""Default minimum cart age (in minutes) to consider abandoned."""

DEFAULT_MIN_CART_VALUE = 0
"""Default minimum cart value threshold."""

CAMPAIGN_SYSTEM_PROMPT = """\
You are the ShopMind Campaign Orchestrator — a proactive revenue-recovery
agent for an online laptop store.  You have been given the details of an
abandoned shopping cart and must decide whether to send a recovery nudge
to the customer.

YOUR TASK:
Analyze the cart context below and make ONE decision.  You MUST call
the record_campaign_decision tool with your decision.

DECISION FRAMEWORK:
1. EVALUATE the cart:
   - What is the total value?  High-value carts (₹40,000+) are worth more effort.
   - How long has it been abandoned?  Longer = more urgent but also less likely to convert.
   - What's in the cart?  A gaming laptop is a considered purchase; accessories are impulse buys.
   - Is this a first-time customer or a returning one?  First-timers may need more encouragement.

2. DECIDE one of:
   a) "no_action" — Skip.  Reasons: cart value too low (under ₹2,000), customer was nudged
      recently, cart age is very short (under 45 min), or the cart only has low-margin accessories.
   b) "reminder" — Send a plain reminder email/SMS.  Appropriate when the cart has
      moderate value and the customer might just have forgotten.
   c) "discount_offer" — Send a discount offer.  Only for high-value carts where the
      margin can absorb a small discount (1–15%, never more).  Use smaller discounts
      (3–5%) for returning customers (they're already inclined) and larger ones
      (8–12%) for first-time customers on high-value carts where conversion lift
      justifies the margin loss.

3. CHANNEL selection:
   - Use "email" if the customer has an email address on file.
   - Use "sms" if only a phone number is available.
   - If neither is available, you can still decide on an action — the system will
     log what WOULD have been sent.

4. REASONING:
   Your "decision" text must be a genuine, readable explanation of your thinking —
   the merchant will see this in their dashboard.  Reference specific facts:
   cart value, items, customer type, cart age.  Do NOT use generic filler.

CONSTRAINTS:
- discount_percent must be between 1 and 15 (inclusive).  Never exceed 15%.
- You MUST call record_campaign_decision exactly once.  Do not skip it.
- You may call get_cart_context first if you need more details.

CART INFORMATION:
{cart_context}
"""


async def _evaluate_single_cart(
    conn: asyncpg.Connection,
    adapter: GeminiAdapter,
    cart_summary: Dict[str, Any],
) -> Dict[str, Any]:
    """Use the LLM to decide on a single abandoned cart.

    1. Optionally calls get_cart_context for richer info.
    2. Sends the context to the LLM with the campaign system prompt.
    3. Dispatches the tool call the LLM makes (record_campaign_decision).
    4. Returns the decision record.
    """
    session_id = cart_summary["session_id"]

    # Get enriched context
    cart_ctx = await get_cart_context(conn, session_id)

    # Log the context-gathering step
    await log_tool_call(
        conn=conn,
        session_id=session_id,
        tool_name="get_cart_context",
        tool_input={"session_id": session_id},
        tool_output=cart_ctx,
        decision=f"Gathering cart context for abandoned cart evaluation (cart Rs.{cart_summary['cart_value']:,.0f}, age {cart_summary['cart_age_minutes']}min).",
        user_approved=None,
        success=True,
        agent_name=CAMPAIGN_AGENT_NAME,
    )

    # Build the context string for the LLM
    context_info = {
        "session_id":       session_id,
        "cart_value":       cart_summary["cart_value"],
        "cart_age_minutes": cart_summary["cart_age_minutes"],
        "cart_items":       cart_ctx["cart"]["items"],
        "item_count":       cart_ctx["cart"]["item_count"],
        "customer_type":    cart_ctx["customer_type"],
        "past_orders":      cart_ctx["past_orders_count"],
        "customer_email":   cart_summary.get("customer_email"),
        "customer_phone":   cart_summary.get("customer_phone"),
        "customer_name":    cart_summary.get("customer_name"),
    }

    system_prompt = CAMPAIGN_SYSTEM_PROMPT.format(
        cart_context=json.dumps(context_info, indent=2, default=str)
    )

    # Single LLM call — ask it to call record_campaign_decision
    conversation = [
        adapter.build_user_message(
            "Evaluate this abandoned cart and decide whether to send a recovery nudge. "
            "You MUST call record_campaign_decision with your decision."
        )
    ]

    try:
        llm_response: LLMResponse = await adapter.call_llm(
            conversation_history=conversation,
            tool_definitions=CAMPAIGN_TOOL_DEFINITIONS,
            system_instruction=system_prompt,
        )
    except Exception as e:
        logger.error(f"LLM call failed for session {session_id}: {e}")
        # Record a fallback no_action decision
        result = await record_campaign_decision(
            conn=conn,
            session_id=session_id,
            cart_snapshot=cart_ctx["cart"]["items"],
            cart_value=cart_summary["cart_value"],
            cart_age_minutes=cart_summary["cart_age_minutes"],
            decision=f"LLM call failed ({str(e)[:100]}). Defaulting to no_action.",
            action_taken="no_action",
        )
        return {
            "session_id": session_id,
            "action_taken": "no_action",
            "decision": f"LLM error: {str(e)[:100]}",
            **result,
        }

    # Process tool calls from the LLM response
    if llm_response.tool_calls:
        for tool_call in llm_response.tool_calls:
            if tool_call.name == "record_campaign_decision":
                args = dict(tool_call.args) if tool_call.args else {}

                # Ensure required fields are populated from cart context
                args.setdefault("session_id", session_id)
                args.setdefault("cart_value", cart_summary["cart_value"])
                args.setdefault("cart_age_minutes", cart_summary["cart_age_minutes"])
                args.setdefault("decision", "No reasoning provided by LLM.")
                args.setdefault("action_taken", "no_action")

                # Provide cart snapshot if LLM didn't include it
                if "cart_snapshot" not in args or args["cart_snapshot"] is None:
                    args["cart_snapshot"] = cart_ctx["cart"]["items"]

                # Enforce discount cap
                if args.get("discount_percent") is not None:
                    args["discount_percent"] = min(
                        float(args["discount_percent"]),
                        MAX_DISCOUNT_PERCENT,
                    )

                # Execute the tool
                result = await record_campaign_decision(conn=conn, **args)

                return {
                    "session_id":       session_id,
                    "cart_value":       args["cart_value"],
                    "cart_age_minutes": args["cart_age_minutes"],
                    "action_taken":     args["action_taken"],
                    "decision":         args["decision"],
                    "discount_percent": args.get("discount_percent"),
                    "simulated_channel": args.get("simulated_channel"),
                    **result,
                }

            elif tool_call.name == "get_cart_context":
                # LLM asked for more context — we already have it, feed it back
                # and make another LLM call
                conversation.append(llm_response.candidate_content or adapter.build_model_content(llm_response.raw_parts))
                tool_resp = adapter.build_tool_response(
                    tool_name="get_cart_context",
                    result=cart_ctx,
                    call_id=tool_call.id,
                )
                conversation.append(adapter.build_tool_response_content([tool_resp]))

                try:
                    llm_response2 = await adapter.call_llm(
                        conversation_history=conversation,
                        tool_definitions=CAMPAIGN_TOOL_DEFINITIONS,
                        system_instruction=system_prompt,
                    )
                except Exception as e:
                    logger.error(f"Second LLM call failed for session {session_id}: {e}")
                    result = await record_campaign_decision(
                        conn=conn,
                        session_id=session_id,
                        cart_snapshot=cart_ctx["cart"]["items"],
                        cart_value=cart_summary["cart_value"],
                        cart_age_minutes=cart_summary["cart_age_minutes"],
                        decision=f"LLM follow-up call failed. Defaulting to no_action.",
                        action_taken="no_action",
                    )
                    return {"session_id": session_id, "action_taken": "no_action", **result}

                if llm_response2.tool_calls:
                    for tc2 in llm_response2.tool_calls:
                        if tc2.name == "record_campaign_decision":
                            args = dict(tc2.args) if tc2.args else {}
                            args.setdefault("session_id", session_id)
                            args.setdefault("cart_value", cart_summary["cart_value"])
                            args.setdefault("cart_age_minutes", cart_summary["cart_age_minutes"])
                            args.setdefault("decision", "No reasoning provided by LLM.")
                            args.setdefault("action_taken", "no_action")
                            if "cart_snapshot" not in args or args["cart_snapshot"] is None:
                                args["cart_snapshot"] = cart_ctx["cart"]["items"]
                            if args.get("discount_percent") is not None:
                                args["discount_percent"] = min(float(args["discount_percent"]), MAX_DISCOUNT_PERCENT)

                            result = await record_campaign_decision(conn=conn, **args)
                            return {
                                "session_id": session_id,
                                "cart_value": args["cart_value"],
                                "cart_age_minutes": args["cart_age_minutes"],
                                "action_taken": args["action_taken"],
                                "decision": args["decision"],
                                "discount_percent": args.get("discount_percent"),
                                "simulated_channel": args.get("simulated_channel"),
                                **result,
                            }

    # Fallback: LLM returned text instead of a tool call, or didn't call record_campaign_decision
    fallback_decision = llm_response.text or "LLM did not produce a structured decision."
    logger.warning(f"LLM did not call record_campaign_decision for {session_id}. Text: {fallback_decision[:200]}")

    result = await record_campaign_decision(
        conn=conn,
        session_id=session_id,
        cart_snapshot=cart_ctx["cart"]["items"],
        cart_value=cart_summary["cart_value"],
        cart_age_minutes=cart_summary["cart_age_minutes"],
        decision=f"[Fallback] {fallback_decision[:500]}",
        action_taken="no_action",
    )

    return {
        "session_id":   session_id,
        "action_taken": "no_action",
        "decision":     fallback_decision[:500],
        **result,
    }


async def run_campaign_scan(
    conn: asyncpg.Connection,
    adapter: GeminiAdapter,
    min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
    min_cart_value: float = DEFAULT_MIN_CART_VALUE,
) -> Dict[str, Any]:
    """Run one full campaign orchestrator pass.

    1. Discovers abandoned carts via ``find_abandoned_carts``.
    2. For each candidate (up to ``MAX_CARTS_PER_RUN``), asks the LLM
       to evaluate and decide.
    3. Returns a summary of all decisions made.

    Args:
        conn: Database connection.
        adapter: The LLM adapter (e.g., GeminiAdapter).
        min_age_minutes: Minimum cart age to consider.
        min_cart_value: Minimum cart value to consider.

    Returns:
        Summary dict with counts and per-cart decisions.
    """
    print(f"\n[Campaign Orchestrator] Starting scan (min_age={min_age_minutes}min, min_value=Rs.{min_cart_value:,.0f})...")

    # Step 1: Discover candidates
    discovery = await find_abandoned_carts(
        conn,
        min_age_minutes=min_age_minutes,
        min_cart_value=min_cart_value,
    )

    candidates = discovery["abandoned_carts"][:MAX_CARTS_PER_RUN]
    total_found = discovery["count"]

    # Log the discovery step
    await log_tool_call(
        conn=conn,
        session_id="campaign_orchestrator_run",
        tool_name="find_abandoned_carts",
        tool_input={
            "min_age_minutes": min_age_minutes,
            "min_cart_value":  min_cart_value,
        },
        tool_output={
            "total_found":     total_found,
            "evaluating":      len(candidates),
            "capped_at":       MAX_CARTS_PER_RUN,
        },
        decision=f"Found {total_found} abandoned cart(s). Evaluating top {len(candidates)} (capped at {MAX_CARTS_PER_RUN}).",
        user_approved=None,
        success=True,
        agent_name=CAMPAIGN_AGENT_NAME,
    )

    if not candidates:
        print("[Campaign Orchestrator] No abandoned carts found. Scan complete.")
        return {
            "carts_scanned": 0,
            "nudges_sent":   0,
            "carts_skipped": 0,
            "decisions":     [],
            "message":       "No abandoned carts found matching criteria.",
        }

    # Step 2: Evaluate each candidate
    decisions: List[Dict[str, Any]] = []
    nudges_sent = 0
    carts_skipped = 0

    for i, cart in enumerate(candidates):
        print(f"[Campaign Orchestrator] Evaluating cart {i+1}/{len(candidates)}: "
              f"session={cart['session_id'][:8]}..., value=Rs.{cart['cart_value']:,.0f}, "
              f"age={cart['cart_age_minutes']}min")

        try:
            decision = await _evaluate_single_cart(conn, adapter, cart)
            decisions.append(decision)

            if decision.get("action_taken") in ("reminder", "discount_offer"):
                nudges_sent += 1
            else:
                carts_skipped += 1

        except Exception as e:
            logger.error(f"Error evaluating cart {cart['session_id']}: {e}")
            carts_skipped += 1
            decisions.append({
                "session_id":   cart["session_id"],
                "action_taken": "no_action",
                "decision":     f"Error during evaluation: {str(e)[:200]}",
                "error":        True,
            })

    summary = {
        "carts_scanned": len(candidates),
        "nudges_sent":   nudges_sent,
        "carts_skipped": carts_skipped,
        "decisions":     decisions,
        "message": (
            f"Campaign scan complete. Evaluated {len(candidates)} cart(s): "
            f"{nudges_sent} nudge(s) sent, {carts_skipped} skipped."
        ),
    }

    print(f"[Campaign Orchestrator] {summary['message']}")
    return summary
