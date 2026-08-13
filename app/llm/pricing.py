"""Rough $/token pricing for AI Assist's usage/cost tracking.

These rates need occasional manual updates when providers reprice — this
is a "rough cost" estimate (per the design decision), not a billing-grade
figure. Rates are $ per 1M tokens, current as of this table's last edit.
Ollama is always $0 (local inference; a cloud Ollama deployment with its
own billing is not modeled here).
"""

from __future__ import annotations

# {model: (input $/1M tokens, output $/1M tokens)}
_RATES: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-5": (15.00, 75.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
}


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """Rough $ cost for one call. Unrecognized/Ollama models return 0.0
    rather than guessing — an unpriced call should read as free, not
    silently wrong."""
    if provider == "ollama":
        return 0.0
    rates = _RATES.get(model)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
