"""Ollama provider — local or cloud, via plain HTTP (no extra SDK dependency)."""

from __future__ import annotations

import requests

from app.config import Config
from app.llm.base import LLMError, LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self) -> None:
        if not Config.OLLAMA_HOST:
            raise LLMError("OLLAMA_HOST is not set in .env")
        self._host = Config.OLLAMA_HOST.rstrip("/")
        self._model = Config.OLLAMA_MODEL

    def narrate(self, system_prompt: str, user_prompt: str) -> str:
        headers = {}
        if Config.OLLAMA_API_KEY:
            headers["Authorization"] = f"Bearer {Config.OLLAMA_API_KEY}"
        try:
            resp = requests.post(
                f"{self._host}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                },
                headers=headers,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "")
        except Exception as exc:
            raise LLMError(f"Ollama API call failed: {exc}") from exc
