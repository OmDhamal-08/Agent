"""
Pydantic request/response models for all ShopMind AI API endpoints.

These models handle validation and serialization for chat interactions,
payment verification, and action confirmation flows.
"""

from pydantic import BaseModel
from typing import Optional, Any


class ChatRequest(BaseModel):
    """Request model for the chat endpoint.

    Represents an incoming user message within a specific session.

    Attributes:
        message: The user's chat message text.
        session_id: Unique identifier for the conversation session.
    """
    message: str
    session_id: str


class ConfirmActionRequest(BaseModel):
    """Request model for confirming a pending tool action.

    When the AI proposes a sensitive action (e.g. creating a payment),
    the frontend sends this to confirm execution.

    Attributes:
        session_id: Unique identifier for the conversation session.
        action_id: Unique identifier for the pending action to confirm.
    """
    session_id: str
    action_id: str


class ChatResponse(BaseModel):
    """Response model returned from chat and confirmation endpoints.

    Conveys the AI's reply, any pending actions awaiting user confirmation,
    and metadata about tool usage during the interaction.

    Attributes:
        type: Response category — one of 'text', 'pending_confirmation', or 'error'.
        content: The textual content of the response (AI reply or error message).
        pending_action: Optional dict describing an action awaiting confirmation,
            with keys: action_id, tool_name, tool_args, description.
        tool_calls_made: Number of tool invocations made during this interaction.
    """
    type: str  # 'text', 'pending_confirmation', 'error'
    content: str
    pending_action: Optional[dict] = None  # {action_id, tool_name, tool_args, description}
    tool_calls_made: int = 0


class PaymentVerifyRequest(BaseModel):
    """Request model for verifying a completed Razorpay payment.

    Contains the three parameters returned by Razorpay's checkout flow,
    used to cryptographically verify the payment signature.

    Attributes:
        razorpay_order_id: The Razorpay order ID returned after checkout.
        razorpay_payment_id: The Razorpay payment ID for the transaction.
        razorpay_signature: HMAC-SHA256 signature for server-side verification.
    """
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class PaymentFailedRequest(BaseModel):
    """Request model for recording a failed Razorpay payment attempt.

    Captures error details from the Razorpay checkout when a payment
    is declined or otherwise fails, so the order status can be updated.

    Attributes:
        session_id: Unique identifier for the conversation session.
        order_id: Internal database order ID.
        razorpay_order_id: Optional Razorpay order ID, if one was created.
        error_code: Optional Razorpay error code (e.g. 'BAD_REQUEST_ERROR').
        error_description: Optional human-readable error description.
        error_reason: Optional machine-readable error reason.
    """
    session_id: str
    order_id: int
    razorpay_order_id: Optional[str] = None
    error_code: Optional[str] = None
    error_description: Optional[str] = None
    error_reason: Optional[str] = None
