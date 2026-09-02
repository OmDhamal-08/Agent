"""
Gemini-specific adapter for the ShopMind AI agent.
Converts neutral tool definitions and manages LLM API calls.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

@dataclass
class ToolCall:
    """Represents a single tool/function call requested by the model."""

    name: str
    args: dict
    id: str | None = None


@dataclass
class LLMResponse:
    """Structured response returned from the Gemini LLM."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_parts: list = field(default_factory=list)
    candidate_content: Any = None

def convert_tool_definitions(tool_defs: list[dict]) -> list[types.Tool]:
    """Convert neutral JSON tool definitions to Gemini ``types.Tool`` objects."""

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
    """Recursively convert a JSON-Schema dict into a form accepted by Gemini."""

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

class GeminiAdapter:
    """Adapter that wraps the Google Gemini (``google-genai``) SDK."""

    def __init__(self, api_key: str, model: str | None = None) -> None:
        """Initialise the adapter.

        Args:
            api_key: Google AI API key.
            model: The Gemini model identifier to use (defaults to GEMINI_MODEL env var or gemini-2.5-flash).
        """

        self.client = genai.Client(api_key=api_key)
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        logger.info("GeminiAdapter initialised with model=%s", self.model)

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
                if any(k in err_str for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "RemoteProtocolError")) and attempt < 2:
                    logger.warning("Temporary Gemini error or rate limit hit, retrying in %ds (attempt %d/3)...", 3 * (attempt + 1), attempt + 1)
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                logger.exception("Gemini API call failed")
                raise

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """Parse a Gemini ``GenerateContentResponse`` into an ``LLMResponse``."""

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

    @staticmethod
    def build_user_message(text: str) -> types.Content:
        """Create a ``user`` role content entry."""

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
        """Create a function-response part for a single tool result."""

        return types.Part.from_function_response(
            name=tool_name,
            response=result,
        )

    @staticmethod
    def build_tool_response_content(parts: list) -> types.Content:
        """Wrap one or more tool-response parts in a ``user`` role content."""

        return types.Content(role="user", parts=parts)

    @staticmethod
    def build_model_content(parts: list) -> types.Content:
        """Reconstruct a ``model`` role content from raw parts."""

        return types.Content(role="model", parts=parts)
