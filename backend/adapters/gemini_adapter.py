"""
Gemini-specific adapter for the ShopMind AI agent.

Converts neutral tool definitions to Gemini's FunctionDeclaration format,
manages LLM API calls via the modern google-genai SDK, and provides
helper methods for constructing conversation history entries.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """Represents a single tool/function call requested by the model."""

    name: str
    args: dict
    id: str | None = None


@dataclass
class LLMResponse:
    """Structured response returned from the Gemini LLM.

    Attributes:
        text: The textual reply from the model, if any.
        tool_calls: A list of tool calls the model wants to invoke.
        raw_parts: The raw ``types.Part`` objects from the response; kept
            so that the caller can faithfully reconstruct the model turn
            when appending to conversation history.
    """

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_parts: list = field(default_factory=list)
    candidate_content: Any = None


# ---------------------------------------------------------------------------
# Tool-definition conversion
# ---------------------------------------------------------------------------

def convert_tool_definitions(tool_defs: list[dict]) -> list[types.Tool]:
    """Convert neutral JSON tool definitions to Gemini ``types.Tool`` objects.

    Each neutral definition is expected to follow the shape emitted by
    ``tool_definitions.py``::

        {
            "name": "...",
            "description": "...",
            "parameters": {  # JSON-Schema style
                "type": "object",
                "properties": { ... },
                "required": [ ... ]
            }
        }

    Args:
        tool_defs: A list of neutral tool definition dictionaries.

    Returns:
        A list containing a single ``types.Tool`` that wraps all the
        converted ``FunctionDeclaration`` objects.
    """

    declarations: list[types.FunctionDeclaration] = []

    for td in tool_defs:
        params_schema = td.get("parameters")

        # Build a Schema object from the raw JSON-Schema dict if present.
        schema: dict[str, Any] | None = None
        if params_schema:
            schema = _convert_schema(params_schema)

        declaration = types.FunctionDeclaration(
            name=td["name"],
            description=td.get("description", ""),
            parameters=schema,
        )
        declarations.append(declaration)

    return [types.Tool(function_declarations=declarations)]


def _convert_schema(schema_dict: dict) -> dict[str, Any]:
    """Recursively convert a JSON-Schema dict into a form accepted by Gemini.

    The google-genai SDK accepts plain dicts that mirror the
    ``google.genai.types.Schema`` structure.  This helper normalises common
    JSON-Schema patterns (``type``, ``properties``, ``items``, ``enum``,
    ``required``) into that shape.

    Args:
        schema_dict: A JSON-Schema-style dictionary.

    Returns:
        A dict suitable for use as a ``parameters`` value in a
        ``FunctionDeclaration``.
    """

    result: dict[str, Any] = {}

    # --- type ---
    json_type = schema_dict.get("type", "object")
    type_mapping = {
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }
    result["type"] = type_mapping.get(json_type, "STRING")

    # --- description ---
    if "description" in schema_dict:
        result["description"] = schema_dict["description"]

    # --- enum ---
    if "enum" in schema_dict:
        result["enum"] = schema_dict["enum"]

    # --- properties (for objects) ---
    if "properties" in schema_dict:
        result["properties"] = {
            key: _convert_schema(val)
            for key, val in schema_dict["properties"].items()
        }

    # --- required ---
    if "required" in schema_dict:
        result["required"] = schema_dict["required"]

    # --- items (for arrays) ---
    if "items" in schema_dict:
        result["items"] = _convert_schema(schema_dict["items"])

    return result


# ---------------------------------------------------------------------------
# GeminiAdapter
# ---------------------------------------------------------------------------

class GeminiAdapter:
    """Adapter that wraps the Google Gemini (``google-genai``) SDK.

    Responsibilities:
    * Convert neutral tool definitions to Gemini format.
    * Perform LLM inference and parse the response into an ``LLMResponse``.
    * Provide static helpers for building conversation-history entries.
    """

    def __init__(self, api_key: str, model: str | None = None) -> None:
        """Initialise the adapter.

        Args:
            api_key: Google AI API key.
            model: The Gemini model identifier to use (defaults to GEMINI_MODEL env var or gemini-2.5-flash).
        """

        self.client = genai.Client(api_key=api_key)
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        logger.info("GeminiAdapter initialised with model=%s", self.model)

    # ----- core inference ---------------------------------------------------

    async def call_llm(
        self,
        conversation_history: list,
        tool_definitions: list[dict],
        system_instruction: str | None = None,
    ) -> LLMResponse:
        """Send a request to the Gemini model and return a structured response.

        The synchronous ``generate_content`` call is offloaded to a thread
        via ``asyncio.to_thread`` so that the event loop is never blocked.

        Args:
            conversation_history: List of ``types.Content`` objects
                representing the conversation so far.
            tool_definitions: Neutral tool definitions (from
                ``tool_definitions.py``) to make available to the model.
            system_instruction: Optional system prompt / instruction for the model.

        Returns:
            An ``LLMResponse`` with either ``text`` or ``tool_calls``
            populated, plus the ``raw_parts`` for history reconstruction.

        Raises:
            Exception: Propagates any errors from the Gemini API after
                logging them.
        """

        # Convert tools
        gemini_tools = convert_tool_definitions(tool_definitions)

        config = types.GenerateContentConfig(
            tools=gemini_tools,
            system_instruction=system_instruction,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,
            ),
            temperature=0.2,
        )

        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model,
                    contents=conversation_history,
                    config=config,
                )
                return self._parse_response(response)
            except Exception as e:
                err_str = str(e)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < 2:
                    logger.warning("Rate limit hit, retrying in %ds (attempt %d/3)...", 3 * (attempt + 1), attempt + 1)
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                logger.exception("Gemini API call failed")
                raise

    # ----- response parsing -------------------------------------------------

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """Parse a Gemini ``GenerateContentResponse`` into an ``LLMResponse``.

        Args:
            response: The raw response object returned by
                ``models.generate_content``.

        Returns:
            A populated ``LLMResponse``.
        """

        # Extract raw parts and candidate_content for history reconstruction
        raw_parts: list = []
        candidate_content: Any = None
        if response.candidates and response.candidates[0].content:
            candidate_content = response.candidates[0].content
            raw_parts = list(candidate_content.parts or [])

        # Check for function calls
        fn_calls = response.function_calls
        if fn_calls:
            tool_calls = [
                ToolCall(
                    name=fc.name,
                    args=dict(fc.args) if fc.args else {},
                    id=getattr(fc, "id", None),
                )
                for fc in fn_calls
            ]
            logger.info(
                "Model requested %d tool call(s): %s",
                len(tool_calls),
                [tc.name for tc in tool_calls],
            )
            return LLMResponse(
                text=None,
                tool_calls=tool_calls,
                raw_parts=raw_parts,
                candidate_content=candidate_content,
            )

        # Plain text response
        text = response.text if response.text else None
        logger.debug("Model returned text response (length=%d)", len(text or ""))
        return LLMResponse(
            text=text,
            tool_calls=[],
            raw_parts=raw_parts,
            candidate_content=candidate_content,
        )

    # ----- static helpers for history construction --------------------------

    @staticmethod
    def build_user_message(text: str) -> types.Content:
        """Create a ``user`` role content entry.

        Args:
            text: The user's message text.

        Returns:
            A ``types.Content`` with role ``'user'``.
        """

        return types.Content(
            role="user",
            parts=[types.Part.from_text(text=text)],
        )

    @staticmethod
    def build_tool_response(
        tool_name: str,
        result: dict,
        call_id: str | None = None,
    ) -> types.Part:
        """Create a function-response part for a single tool result.

        Args:
            tool_name: The name of the function that was called.
            result: The result dictionary to return to the model.
            call_id: Optional call identifier (reserved for future use).

        Returns:
            A ``types.Part`` representing the function response.
        """

        return types.Part.from_function_response(
            name=tool_name,
            response=result,
        )

    @staticmethod
    def build_tool_response_content(parts: list) -> types.Content:
        """Wrap one or more tool-response parts in a ``user`` role content.

        Note: Some Gemini model versions do not support ``role='tool'``.
        Using ``role='user'`` with function-response parts is compatible
        across all model versions.

        Args:
            parts: A list of ``types.Part`` objects (typically created by
                ``build_tool_response``).

        Returns:
            A ``types.Content`` with role ``'user'`` containing function responses.
        """

        return types.Content(role="user", parts=parts)

    @staticmethod
    def build_model_content(parts: list) -> types.Content:
        """Reconstruct a ``model`` role content from raw parts.

        Use this to re-add the model's turn (including any function-call
        parts) to the conversation history after processing.

        Args:
            parts: The ``raw_parts`` from an ``LLMResponse``.

        Returns:
            A ``types.Content`` with role ``'model'``.
        """

        return types.Content(role="model", parts=parts)
