"""Multi-provider LLM narration for AI-assisted Rule Validation.

The LLM here never computes a verdict — app.planner.engine.plan_change()
already has. This package only turns that structured result into
human-readable prose (report + peer-review package text) via a single
completion call, using whichever provider is configured in AI_PROVIDER.
"""

from __future__ import annotations

from app.config import Config
from app.llm.base import LLMError, LLMProvider

__all__ = ["get_provider", "LLMError", "LLMProvider"]


def get_provider() -> LLMProvider:
    """Return the configured LLM provider instance."""
    provider = (Config.AI_PROVIDER or "claude").lower()
    if provider == "claude":
        from app.llm.claude_provider import ClaudeProvider

        return ClaudeProvider()
    if provider == "codex":
        from app.llm.codex_provider import CodexProvider

        return CodexProvider()
    if provider == "ollama":
        from app.llm.ollama_provider import OllamaProvider

        return OllamaProvider()
    raise LLMError(
        f"Unknown AI_PROVIDER {provider!r} — expected claude, codex, or ollama"
    )
