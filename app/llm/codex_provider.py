"""OpenAI (Codex/GPT) provider."""

from __future__ import annotations

from app.config import Config
from app.llm.base import LLMError, LLMProvider


class CodexProvider(LLMProvider):
    def __init__(self) -> None:
        if not Config.OPENAI_API_KEY:
            raise LLMError("OPENAI_API_KEY is not set in .env")
        self._model = Config.OPENAI_MODEL

    def narrate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            import openai
        except ImportError as exc:
            raise LLMError("the 'openai' package is not installed") from exc
        try:
            client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=self._model,
                max_completion_tokens=2048,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or ""
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"OpenAI API call failed: {exc}") from exc
