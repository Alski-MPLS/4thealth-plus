"""Anthropic Claude provider — the default AI Assist backend."""

from __future__ import annotations

from app.config import Config
from app.llm.base import LLMError, LLMProvider


class ClaudeProvider(LLMProvider):
    def __init__(self) -> None:
        if not Config.ANTHROPIC_API_KEY:
            raise LLMError("ANTHROPIC_API_KEY is not set in .env")
        self._model = Config.ANTHROPIC_MODEL

    def narrate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("the 'anthropic' package is not installed") from exc
        try:
            client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Claude API call failed: {exc}") from exc
