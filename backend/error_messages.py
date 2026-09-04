"""
error_messages.py — Centralised client-friendly error translation.

Maps technical backend exceptions to polished, non-technical messages
for both customer-facing chat and merchant dashboard contexts.
All raw error details are logged server-side only.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error classification keywords
# ---------------------------------------------------------------------------

_QUOTA_KEYWORDS = ("429", "RESOURCE_EXHAUSTED", "quota", "rate limit", "rate_limit")
_NETWORK_KEYWORDS = ("503", "UNAVAILABLE", "RemoteProtocolError", "ConnectionError",
                     "TimeoutError", "timeout", "DEADLINE_EXCEEDED", "connect")
_CONTEXT_KEYWORDS = ("context length", "token limit", "too long", "max_tokens",
                     "INVALID_ARGUMENT", "content too large")
_AUTH_KEYWORDS = ("401", "403", "PERMISSION_DENIED", "API_KEY_INVALID",
                  "UNAUTHENTICATED")


def _classify_error(exc: Exception) -> str:
    """Classify an exception into a category based on its message."""
    err_str = str(exc).lower()
    exc_type = type(exc).__name__.lower()
    combined = f"{err_str} {exc_type}"

    if any(kw.lower() in combined for kw in _QUOTA_KEYWORDS):
        return "quota"
    if any(kw.lower() in combined for kw in _NETWORK_KEYWORDS):
        return "network"
    if any(kw.lower() in combined for kw in _CONTEXT_KEYWORDS):
        return "context"
    if any(kw.lower() in combined for kw in _AUTH_KEYWORDS):
        return "auth"
    return "unknown"


# ---------------------------------------------------------------------------
# Customer-facing messages (chat UI)
# ---------------------------------------------------------------------------

_CUSTOMER_MESSAGES = {
    "quota":   "I'm currently experiencing high demand. Please give me just a few moments and try your message again.",
    "network": "I'm having a brief connection issue. Please send your message again in a moment.",
    "context": "Our conversation has gotten quite long! Could you try starting a fresh session or rephrasing your question?",
    "auth":    "I'm temporarily unable to process requests. The team has been notified — please try again shortly.",
    "unknown": "I had trouble processing that request. Could you please try again or rephrase your question?",
}


def get_customer_error_message(exc: Exception) -> str:
    """Return a friendly, non-technical error message for the chat UI.

    The raw exception is logged at ERROR level for developer debugging.
    """
    category = _classify_error(exc)
    logger.error(
        "Chat error [%s]: %s: %s",
        category,
        type(exc).__name__,
        str(exc),
        exc_info=True,
    )
    return _CUSTOMER_MESSAGES[category]


# ---------------------------------------------------------------------------
# Admin / Dashboard-facing messages
# ---------------------------------------------------------------------------

_ADMIN_MESSAGES = {
    "quota":   "The AI service reached temporary capacity. Automatic key rotation was attempted. Please retry shortly.",
    "network": "Temporary connection issue with the AI service. Please re-run the operation.",
    "context": "The conversation context exceeded the model's capacity. Consider starting a new session.",
    "auth":    "AI service authentication failed. Please verify the API key configuration.",
    "unknown": "The AI assistant encountered an unexpected issue. Check server logs for details.",
}


def get_admin_error_message(exc: Exception) -> str:
    """Return a professional error message for the merchant dashboard.

    The raw exception is logged at ERROR level for developer debugging.
    """
    category = _classify_error(exc)
    logger.error(
        "Dashboard error [%s]: %s: %s",
        category,
        type(exc).__name__,
        str(exc),
        exc_info=True,
    )
    return _ADMIN_MESSAGES[category]


# ---------------------------------------------------------------------------
# Campaign-specific messages (stored in campaign_actions.decision)
# ---------------------------------------------------------------------------

def get_campaign_fallback_decision(exc: Exception) -> str:
    """Return a clean decision string for campaign_actions when LLM fails.

    Avoids leaking raw error strings into the merchant-visible
    campaign history table.
    """
    category = _classify_error(exc)
    logger.error(
        "Campaign LLM error [%s]: %s: %s",
        category,
        type(exc).__name__,
        str(exc),
        exc_info=True,
    )
    messages = {
        "quota":   "Automated evaluation deferred due to high AI service traffic. Cart remains saved for the next scan.",
        "network": "Evaluation skipped due to a temporary connection issue with the AI service. Will be retried.",
        "context": "Cart context exceeded evaluation capacity. Manual review recommended.",
        "auth":    "AI service authentication issue prevented evaluation. Please check API key configuration.",
        "unknown": "Automated evaluation could not be completed. Cart remains saved for manual review or the next scan.",
    }
    return messages[category]
