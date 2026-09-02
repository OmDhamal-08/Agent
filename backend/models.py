"""
Pydantic request/response models for all ShopMind AI API endpoints.

These models handle validation and serialization for chat interactions,
payment verification, and action confirmation flows.
"""

from pydantic import BaseModel
from typing import Optional, Any


class ChatRequest(BaseModel):
    """Request model for the chat endpoint."""
    message: str
    session_id: str


class ConfirmActionRequest(BaseModel):
    """Request model for confirming a pending tool action."""
    session_id: str
    action_id: str


class ChatResponse(BaseModel):
    """Response model returned from chat and confirmation endpoints."""
    type: str  # 'text', 'pending_confirmation', 'error'
    content: str
    pending_action: Optional[dict] = None  # {action_id, tool_name, tool_args, description}
    tool_calls_made: int = 0
    tool_result: Optional[dict] = None  # Result from a confirmed tool execution


class PaymentVerifyRequest(BaseModel):
    """Request model for verifying a completed Razorpay payment."""
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class PaymentFailedRequest(BaseModel):
    """Request model for recording a failed Razorpay payment attempt."""
    session_id: str
    order_id: int
    razorpay_order_id: Optional[str] = None
    error_code: Optional[str] = None
    error_description: Optional[str] = None
    error_reason: Optional[str] = None
