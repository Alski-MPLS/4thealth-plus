"""Tests for app.llm.pricing.estimate_cost."""
from app.llm.pricing import estimate_cost


def test_estimate_cost_known_model():
    cost = estimate_cost("claude", "claude-sonnet-4-5", 1_000_000, 1_000_000)
    assert cost == 3.00 + 15.00


def test_estimate_cost_ollama_always_zero():
    assert estimate_cost("ollama", "llama3.1", 1_000_000, 1_000_000) == 0.0


def test_estimate_cost_unknown_model_returns_zero():
    assert estimate_cost("claude", "some-future-model", 1_000_000, 1_000_000) == 0.0


def test_estimate_cost_scales_with_tokens():
    cost = estimate_cost("codex", "gpt-5", 500_000, 0)
    assert cost == 0.625
