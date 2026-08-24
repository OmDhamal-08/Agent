"""
FastAPI routes for the chat/agent conversation.

Provides endpoints for sending messages, confirming/cancelling pending
actions, and listing active sessions.
"""

import os
import json
from fastapi import APIRouter, Depends, HTTPException
import asyncpg

from backend.database import get_db
from backend.models import ChatRequest, ConfirmActionRequest, ChatResponse
from backend.agent_loop import (
    run_agent,
    execute_confirmed_action,
    cancel_action,
    PendingConfirmation,
    AgentResponse,
    SYSTEM_PROMPT,
)
from backend.adapters.gemini_adapter import GeminiAdapter

router = APIRouter(prefix='/api', tags=['chat'])

# ---------------------------------------------------------------------------
# In-memory session store (for hackathon; production would use Redis)
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}
# _sessions[session_id] = {
#     'history': [...],  # conversation history (Gemini Content objects)
#     'pending': PendingConfirmation | None
# }

# ---------------------------------------------------------------------------
# Singleton GeminiAdapter
# ---------------------------------------------------------------------------
_adapter: GeminiAdapter | None = None


def get_adapter() -> GeminiAdapter:
    """Return (and lazily create) the module-level GeminiAdapter instance.

    Raises:
        RuntimeError: If the ``GEMINI_API_KEY`` environment variable is not set.
    """
    global _adapter
    if _adapter is None:
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise RuntimeError('GEMINI_API_KEY not set')
        _adapter = GeminiAdapter(api_key=api_key)
    return _adapter


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _get_session(session_id: str) -> dict:
    """Return the session dict for *session_id*, creating it if needed."""
    if session_id not in _sessions:
        _sessions[session_id] = {
            'history': [],
            'pending': None,
        }
    return _sessions[session_id]


# ---------------------------------------------------------------------------
# Routes
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


@router.post('/chat', response_model=ChatResponse)
async def chat(req: ChatRequest, conn: asyncpg.Connection = Depends(get_db)) -> ChatResponse:
    """Main chat endpoint.

    Accepts a user message, runs the agent loop, and returns the agent's
    response.  If the agent proposes a side-effecting action the response
    type will be ``pending_confirmation`` and the pending action is stored
    in the session so the frontend can confirm or cancel it.
    """
    session = _get_session(req.session_id)
    adapter = get_adapter()

    response = await run_agent(
        req.message,
        req.session_id,
        session['history'],
        conn,
        adapter,
    )

    # Stash pending confirmation so /confirm and /cancel can pick it up
    if response.type == 'pending_confirmation':
        session['pending'] = response.pending_action
    else:
        # Any non-confirmation response clears a stale pending action
        session['pending'] = None

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
    if req.session_id not in _sessions:
        raise HTTPException(status_code=400, detail='Session not found')

    session = _sessions[req.session_id]
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

    # Clear the pending action after execution
    session['pending'] = None

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
    if req.session_id not in _sessions:
        raise HTTPException(status_code=400, detail='Session not found')

    session = _sessions[req.session_id]
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

    # Clear the pending action after cancellation
    session['pending'] = None

    return _to_chat_response(response)


@router.get('/sessions')
async def list_sessions() -> dict:
    """List all active session IDs.

    Returns a JSON object with a single key ``sessions`` containing a list
    of session-id strings.  Useful for the admin / debug dashboard.
    """
    return {'sessions': list(_sessions.keys())}
