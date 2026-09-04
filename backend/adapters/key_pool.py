"""
key_pool.py — Thread-safe multi-account Gemini API key rotation pool.

Distributes requests across multiple free-tier Gemini API keys using
round-robin scheduling.  When a key hits a 429 / RESOURCE_EXHAUSTED
quota limit the pool places it in a temporary cooldown and transparently
rotates to the next healthy key.

Usage:
    pool = GeminiKeyPool.from_env()
    client, key_id = pool.acquire()        # get a healthy genai.Client
    pool.report_success(key_id)            # after a successful call
    pool.report_quota_error(key_id)        # after a 429 — puts key on cooldown
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from google import genai

logger = logging.getLogger(__name__)

# Cooldown durations (seconds)
INITIAL_COOLDOWN_SECS = 60        # first 429 → 1 minute cooldown
ESCALATED_COOLDOWN_SECS = 600     # repeated 429 → 10 minute cooldown
CONSECUTIVE_FAILURES_TO_ESCALATE = 2  # escalate after this many back-to-back 429s


@dataclass
class _KeyState:
    """Internal bookkeeping for a single API key."""
    key: str
    masked: str                        # e.g. "...xY4q" — safe for logs
    client: genai.Client
    total_calls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0        # epoch timestamp; 0 = not cooling down

    @property
    def is_healthy(self) -> bool:
        return time.time() >= self.cooldown_until


class GeminiKeyPool:
    """Round-robin pool of Gemini API keys with automatic failover."""

    def __init__(self, api_keys: list[str]) -> None:
        if not api_keys:
            raise ValueError(
                "At least one Gemini API key is required. "
                "Set GEMINI_API_KEYS (comma-separated) or GEMINI_API_KEY in your .env file."
            )

        self._lock = threading.Lock()
        self._keys: list[_KeyState] = []
        self._index = 0  # round-robin pointer

        seen: set[str] = set()
        for raw_key in api_keys:
            key = raw_key.strip()
            if not key or key in seen:
                continue
            seen.add(key)

            masked = f"...{key[-4:]}" if len(key) > 4 else "****"
            client = genai.Client(api_key=key)
            self._keys.append(_KeyState(key=key, masked=masked, client=client))

        if not self._keys:
            raise ValueError("No valid API keys found after de-duplication.")

        logger.info(
            "GeminiKeyPool initialised with %d key(s): %s",
            len(self._keys),
            [k.masked for k in self._keys],
        )

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "GeminiKeyPool":
        """Build a pool from environment variables.

        Reads ``GEMINI_API_KEYS`` (comma-separated) first, falling back
        to ``GEMINI_API_KEY`` (single key) for backwards compatibility.
        """
        multi = os.getenv("GEMINI_API_KEYS", "")
        if multi.strip():
            keys = [k for k in multi.split(",") if k.strip()]
        else:
            single = os.getenv("GEMINI_API_KEY", "")
            keys = [single] if single.strip() else []

        return cls(keys)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self) -> tuple[genai.Client, int]:
        """Return ``(client, key_index)`` for the next healthy key.

        Raises ``RuntimeError`` if every key in the pool is on cooldown.
        """
        with self._lock:
            n = len(self._keys)
            for _ in range(n):
                state = self._keys[self._index]
                idx = self._index
                self._index = (self._index + 1) % n
                if state.is_healthy:
                    state.total_calls += 1
                    return state.client, idx

            # All keys exhausted
            soonest = min(k.cooldown_until for k in self._keys)
            wait = max(0, soonest - time.time())
            raise RuntimeError(
                f"All {n} Gemini API key(s) are on cooldown. "
                f"Earliest recovery in {wait:.0f}s. "
                f"Add more keys via GEMINI_API_KEYS to avoid this."
            )

    def report_success(self, key_index: int) -> None:
        """Mark a successful call — resets consecutive failure counter."""
        with self._lock:
            state = self._keys[key_index]
            state.total_successes += 1
            state.consecutive_failures = 0

    def report_quota_error(self, key_index: int) -> None:
        """Mark a 429 / RESOURCE_EXHAUSTED — puts key on cooldown."""
        with self._lock:
            state = self._keys[key_index]
            state.total_failures += 1
            state.consecutive_failures += 1

            if state.consecutive_failures >= CONSECUTIVE_FAILURES_TO_ESCALATE:
                cooldown = ESCALATED_COOLDOWN_SECS
            else:
                cooldown = INITIAL_COOLDOWN_SECS

            state.cooldown_until = time.time() + cooldown
            logger.warning(
                "Key %s placed on %ds cooldown (consecutive failures: %d, total: %d)",
                state.masked,
                cooldown,
                state.consecutive_failures,
                state.total_failures,
            )

    def healthy_count(self) -> int:
        """Return the number of keys currently available (not on cooldown)."""
        with self._lock:
            return sum(1 for k in self._keys if k.is_healthy)

    def total_count(self) -> int:
        """Return the total number of keys in the pool."""
        return len(self._keys)

    def stats(self) -> list[dict]:
        """Return per-key statistics (keys are masked)."""
        with self._lock:
            return [
                {
                    "masked_key": k.masked,
                    "total_calls": k.total_calls,
                    "successes": k.total_successes,
                    "failures": k.total_failures,
                    "healthy": k.is_healthy,
                    "cooldown_remaining": max(0, k.cooldown_until - time.time()),
                }
                for k in self._keys
            ]
