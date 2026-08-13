"""Provider-agnostic interface every LLM narration backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(Exception):
    """Raised when a provider is misconfigured or a completion call fails."""


class LLMProvider(ABC):
    @abstractmethod
    def narrate(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's text completion for one single-shot prompt.

        Raises LLMError on any failure (missing key, network error, non-2xx
        response) — callers must catch this and degrade gracefully rather
        than let it propagate to the user as a raw exception.
        """
