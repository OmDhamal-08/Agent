"""AI Actions audit trail logging middleware."""

import json
from typing import Any

import asyncpg


async def log_tool_call(
    conn: asyncpg.Connection,
    session_id: str,
    tool_name: str,
    tool_input: dict,
    tool_output: dict,
    decision: str,
    user_approved: bool | None,
    success: bool,
    agent_name: str = "shopmind_v1",
) -> int:
    """Log a tool call to the ai_actions audit trail."""
    row = await conn.fetchrow(
        """
        INSERT INTO ai_actions
            (session_id, agent_name, action_type, tool_name,
             input, output, decision, user_approved, success, timestamp)
        VALUES ($1, $2, 'tool_call', $3, $4, $5, $6, $7, $8, NOW())
        RETURNING id
        """,
        session_id,
        agent_name,
        tool_name,
        json.dumps(tool_input),
        json.dumps(tool_output),
        decision,
        user_approved,
        success,
    )
    return row["id"]


async def log_confirmation_result(
    conn: asyncpg.Connection,
    action_id: int,
    approved: bool,
    decision: str | None = None,
) -> None:
    """Update an existing ai_actions row with the user's confirmation result."""
    if decision is not None:
        await conn.execute(
            """
            UPDATE ai_actions
            SET user_approved = $1,
                decision      = decision || ' | ' || $2
            WHERE id = $3
            """,
            approved,
            decision,
            action_id,
        )
    else:
        await conn.execute(
            """
            UPDATE ai_actions
            SET user_approved = $1
            WHERE id = $2
            """,
            approved,
            action_id,
        )


async def get_session_actions(
    conn: asyncpg.Connection,
    session_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return ai_actions rows, optionally filtered by session."""
    if session_id is not None:
        rows = await conn.fetch(
            """
            SELECT id, session_id, agent_name, action_type, tool_name,
                   input, output, decision, user_approved, success, timestamp
            FROM ai_actions
            WHERE session_id = $1
            ORDER BY timestamp DESC
            LIMIT $2
            """,
            session_id,
            limit,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT id, session_id, agent_name, action_type, tool_name,
                   input, output, decision, user_approved, success, timestamp
            FROM ai_actions
            ORDER BY timestamp DESC
            LIMIT $1
            """,
            limit,
        )

    return [dict(row) for row in rows]
