"""
FastAPI routes for the chat/agent conversation.

Provides endpoints for sending messages, confirming/cancelling pending
actions, and listing active sessions.

Session state (conversation history + pending confirmations) is persisted
in the ``session_state`` database table so that it survives across Vercel
serverless cold starts.
"""

import base64
import json
import os
import logging
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from google.genai import types

from backend.database import get_db
from backend.models import ChatRequest, ConfirmActionRequest, ChatResponse
from backend.agent_loop import (
    run_agent,
    execute_confirmed_action,
    cancel_action,
    PendingConfirmation,
    PendingActionItem,
    AgentResponse,
    SYSTEM_PROMPT,
)
from backend.adapters.gemini_adapter import GeminiAdapter
from backend.adapters.key_pool import GeminiKeyPool

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api', tags=['chat'])

_adapter: GeminiAdapter | None = None
_key_pool: GeminiKeyPool | None = None


def get_adapter() -> GeminiAdapter:
    """Return (and lazily create) the module-level GeminiAdapter instance.

    Uses ``GeminiKeyPool`` for multi-key rotation when ``GEMINI_API_KEYS``
    is set; falls back to single ``GEMINI_API_KEY`` for backwards compat.

    Raises:
        RuntimeError: If no Gemini API key is configured.
    """
    global _adapter, _key_pool
    if _adapter is None:
        try:
            _key_pool = GeminiKeyPool.from_env()
            _adapter = GeminiAdapter(key_pool=_key_pool)
        except (ValueError, RuntimeError):
            # Fallback to single key
            api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                raise RuntimeError('GEMINI_API_KEY not set')
            _adapter = GeminiAdapter(api_key=api_key)
    return _adapter


# ---------------------------------------------------------------------------
# Serialisation helpers — Gemini Content ↔ JSON
# ---------------------------------------------------------------------------

def _serialize_part(part: types.Part) -> dict[str, Any]:
    """Convert a single ``types.Part`` to a JSON-safe dict.

    Preserves ``thought`` and ``thought_signature`` metadata that the
    Gemini API requires on function-call parts from thinking models.
    """
    data: dict[str, Any] = {}

    if part.text is not None:
        data["type"] = "text"
        data["text"] = part.text
    elif part.function_call is not None:
        data["type"] = "function_call"
        data["name"] = part.function_call.name
        data["args"] = dict(part.function_call.args) if part.function_call.args else {}
        fc_id = getattr(part.function_call, "id", None)
        if fc_id is not None:
            data["id"] = fc_id
    elif part.function_response is not None:
        data["type"] = "function_response"
        data["name"] = part.function_response.name
        data["response"] = dict(part.function_response.response) if part.function_response.response else {}
    else:
        data["type"] = "text"
        data["text"] = ""

    # Preserve thinking-model metadata (required by Gemini API)
    thought = getattr(part, "thought", None)
    if thought is not None:
        data["thought"] = thought

    thought_sig = getattr(part, "thought_signature", None)
    if thought_sig is not None:
        if isinstance(thought_sig, bytes):
            data["thought_signature"] = base64.b64encode(thought_sig).decode("ascii")
        else:
            data["thought_signature"] = str(thought_sig)

    return data


def _deserialize_part(data: dict[str, Any]) -> types.Part:
    """Reconstruct a ``types.Part`` from a serialised dict.

    Restores ``thought`` and ``thought_signature`` metadata required by
    the Gemini API for thinking-model conversation history.
    """
    ptype = data.get("type", "text")

    # Prepare thinking-model metadata kwargs
    thought_kwargs: dict[str, Any] = {}
    if "thought" in data and data["thought"] is not None:
        thought_kwargs["thought"] = data["thought"]
    if "thought_signature" in data and data["thought_signature"] is not None:
        sig = data["thought_signature"]
        if isinstance(sig, str):
            thought_kwargs["thought_signature"] = base64.b64decode(sig)
        else:
            thought_kwargs["thought_signature"] = sig

    if ptype == "text":
        return types.Part(text=data.get("text", ""), **thought_kwargs)
    if ptype == "function_call":
        fc_kwargs: dict[str, Any] = {
            "name": data["name"],
            "args": data.get("args", {}),
        }
        if data.get("id") is not None:
            fc_kwargs["id"] = data["id"]
        return types.Part(
            function_call=types.FunctionCall(**fc_kwargs),
            **thought_kwargs,
        )
    if ptype == "function_response":
        return types.Part(
            function_response=types.FunctionResponse(
                name=data["name"],
                response=data.get("response", {}),
            ),
            **thought_kwargs,
        )
    return types.Part(text=data.get("text", ""), **thought_kwargs)


def serialize_history(history: list) -> list[dict[str, Any]]:
    """Serialise a list of ``types.Content`` objects to JSON-safe dicts."""
    result: list[dict[str, Any]] = []
    for content in history:
        parts = []
        if hasattr(content, "parts") and content.parts:
            for part in content.parts:
                parts.append(_serialize_part(part))
        role = getattr(content, "role", "user") or "user"
        result.append({"role": role, "parts": parts})
    return result


def deserialize_history(data: list[dict[str, Any]]) -> list[types.Content]:
    """Reconstruct a list of ``types.Content`` objects from serialised dicts."""
    result: list[types.Content] = []
    for item in data:
        parts = [_deserialize_part(p) for p in item.get("parts", [])]
        result.append(types.Content(role=item.get("role", "user"), parts=parts))
    return result


def _serialize_pending(pending: PendingConfirmation) -> dict[str, Any]:
    """Serialise a ``PendingConfirmation`` to a JSON-safe dict."""
    actions_data = []
    for action in (pending.actions or []):
        actions_data.append({
            "tool_name": action.tool_name,
            "tool_args": action.tool_args,
            "description": action.description,
            "ai_action_log_id": action.ai_action_log_id,
            "call_id": action.call_id,
        })
    return {
        "action_id": pending.action_id,
        "tool_name": pending.tool_name,
        "tool_args": pending.tool_args,
        "description": pending.description,
        "ai_action_log_id": pending.ai_action_log_id,
        "actions": actions_data,
    }


def _deserialize_pending(data: dict[str, Any]) -> PendingConfirmation:
    """Reconstruct a ``PendingConfirmation`` from a serialised dict."""
    actions = []
    for a in data.get("actions", []):
        actions.append(PendingActionItem(
            tool_name=a["tool_name"],
            tool_args=a["tool_args"],
            description=a["description"],
            ai_action_log_id=a.get("ai_action_log_id"),
            call_id=a.get("call_id"),
        ))
    return PendingConfirmation(
        action_id=data["action_id"],
        tool_name=data["tool_name"],
        tool_args=data["tool_args"],
        description=data["description"],
        ai_action_log_id=data.get("ai_action_log_id"),
        actions=actions,
    )


# ---------------------------------------------------------------------------
# Database-backed session state
# ---------------------------------------------------------------------------

async def _load_session(conn: asyncpg.Connection, session_id: str) -> dict:
    """Load session state from the database, or return empty defaults."""
    row = await conn.fetchrow(
        "SELECT conversation_history, pending_action FROM session_state WHERE session_id = $1",
        session_id,
    )
    if row is None:
        return {"history": [], "pending": None}

    # conversation_history is stored as JSONB
    raw_history = row["conversation_history"]
    if isinstance(raw_history, str):
        raw_history = json.loads(raw_history)
    history = deserialize_history(raw_history) if raw_history else []

    # pending_action is stored as JSONB (nullable)
    raw_pending = row["pending_action"]
    pending = None
    if raw_pending:
        if isinstance(raw_pending, str):
            raw_pending = json.loads(raw_pending)
        pending = _deserialize_pending(raw_pending)

    return {"history": history, "pending": pending}


async def _save_session(
    conn: asyncpg.Connection,
    session_id: str,
    history: list,
    pending: Optional[PendingConfirmation],
) -> None:
    """Persist session state to the database."""
    history_json = json.dumps(serialize_history(history))
    pending_json = json.dumps(_serialize_pending(pending)) if pending else None

    await conn.execute(
        """
        INSERT INTO session_state (session_id, conversation_history, pending_action, updated_at)
        VALUES ($1, $2::jsonb, $3::jsonb, NOW())
        ON CONFLICT (session_id)
        DO UPDATE SET
            conversation_history = EXCLUDED.conversation_history,
            pending_action = EXCLUDED.pending_action,
            updated_at = NOW()
        """,
        session_id,
        history_json,
        pending_json,
    )


# ---------------------------------------------------------------------------
# Response conversion
# ---------------------------------------------------------------------------

def _to_chat_response(resp: AgentResponse) -> ChatResponse:
    """Convert an internal AgentResponse dataclass to a Pydantic ChatResponse."""
    pending_dict = None
    if resp.pending_action:
        pending_dict = {
            "action_id": resp.pending_action.action_id,
            "tool_name": resp.pending_action.tool_name,
            "tool_args": resp.pending_action.tool_args,
            "description": resp.pending_action.description,
        }
    return ChatResponse(
        type=resp.type,
        content=resp.content,
        pending_action=pending_dict,
        tool_calls_made=resp.tool_calls_made,
        tool_result=resp.tool_result,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post('/chat', response_model=ChatResponse)
async def chat(req: ChatRequest, conn: asyncpg.Connection = Depends(get_db)) -> ChatResponse:
    """Main chat endpoint.

    Accepts a user message, runs the agent loop, and returns the agent's
    response.  If the agent proposes a side-effecting action the response
    type will be ``pending_confirmation`` and the pending action is stored
    in the database so the frontend can confirm or cancel it.
    """
    session = await _load_session(conn, req.session_id)
    adapter = get_adapter()

    response = await run_agent(
        req.message,
        req.session_id,
        session['history'],
        conn,
        adapter,
    )

    # Persist updated state
    pending = response.pending_action if response.type == 'pending_confirmation' else None
    await _save_session(conn, req.session_id, response.conversation_history, pending)

    return _to_chat_response(response)


@router.post('/chat/confirm', response_model=ChatResponse)
async def confirm_action(
    req: ConfirmActionRequest,
    conn: asyncpg.Connection = Depends(get_db),
) -> ChatResponse:
    """Confirm a pending action proposed by the agent.

    The frontend must supply the ``session_id`` and ``action_id`` that were
    returned in the original ``pending_confirmation`` response.

    Raises:
        HTTPException 400: If the session does not exist.
        HTTPException 404: If there is no pending action or the action_id
            does not match.
    """
    session = await _load_session(conn, req.session_id)
    pending: PendingConfirmation | None = session.get('pending')

    if pending is None:
        raise HTTPException(status_code=404, detail='No pending action for this session')

    if pending.action_id != req.action_id:
        raise HTTPException(
            status_code=404,
            detail=f'Pending action_id mismatch: expected {pending.action_id}, got {req.action_id}',
        )

    adapter = get_adapter()

    response = await execute_confirmed_action(
        pending,
        req.session_id,
        session['history'],
        conn,
        adapter,
    )

    # Persist updated state
    new_pending = response.pending_action if response.type == 'pending_confirmation' else None
    await _save_session(conn, req.session_id, response.conversation_history, new_pending)

    return _to_chat_response(response)


@router.post('/chat/cancel', response_model=ChatResponse)
async def cancel_pending_action(
    req: ConfirmActionRequest,
    conn: asyncpg.Connection = Depends(get_db),
) -> ChatResponse:
    """Cancel a pending action proposed by the agent.

    Raises:
        HTTPException 400: If the session does not exist.
        HTTPException 404: If there is no pending action or the action_id
            does not match.
    """
    session = await _load_session(conn, req.session_id)
    pending: PendingConfirmation | None = session.get('pending')

    if pending is None:
        raise HTTPException(status_code=404, detail='No pending action for this session')

    if pending.action_id != req.action_id:
        raise HTTPException(
            status_code=404,
            detail=f'Pending action_id mismatch: expected {pending.action_id}, got {req.action_id}',
        )

    adapter = get_adapter()

    response = await cancel_action(
        pending,
        req.session_id,
        session['history'],
        conn,
        adapter,
    )

    # Persist updated state
    new_pending = response.pending_action if response.type == 'pending_confirmation' else None
    await _save_session(conn, req.session_id, response.conversation_history, new_pending)

    return _to_chat_response(response)


@router.get('/sessions')
async def list_sessions(conn: asyncpg.Connection = Depends(get_db)) -> dict:
    """List all active session IDs.

    Returns a JSON object with a single key ``sessions`` containing a list
    of session-id strings.  Useful for the admin / debug dashboard.
    """
    rows = await conn.fetch(
        "SELECT session_id FROM session_state ORDER BY updated_at DESC LIMIT 100"
    )
    return {'sessions': [row['session_id'] for row in rows]}
