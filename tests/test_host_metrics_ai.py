"""Tests for app.host_metrics_ai.compute_trend and build_trend_narrative."""
import json
from unittest.mock import patch


def test_compute_trend_rising_series():
    from app.host_metrics_ai import compute_trend

    # 7 days of data, one point per day, roughly +2%/day
    series = [{"ts": i * 86400, "v": 50.0 + i * 2} for i in range(7)]
    trend = compute_trend(series, threshold=90.0)

    assert trend["start"] == 50.0
    assert trend["end"] == 62.0
    assert trend["pct_change"] == 24.0  # (62-50)/50 * 100
    assert trend["slope_per_day"] == 2.0
    # From 62% rising at 2%/day, threshold 90% is (90-62)/2 = 14 days away
    assert trend["days_to_threshold"] == 14.0


def test_compute_trend_flat_series_no_projection():
    from app.host_metrics_ai import compute_trend

    series = [{"ts": i * 86400, "v": 40.0} for i in range(5)]
    trend = compute_trend(series, threshold=90.0)

    assert trend["slope_per_day"] == 0.0
    assert trend["days_to_threshold"] is None


def test_compute_trend_insufficient_data_returns_none_fields():
    from app.host_metrics_ai import compute_trend

    trend = compute_trend([{"ts": 0, "v": 50.0}], threshold=90.0)
    assert trend == {
        "start": None, "end": None, "pct_change": None,
        "slope_per_day": None, "days_to_threshold": None,
    }


def test_compute_trend_ignores_null_values():
    from app.host_metrics_ai import compute_trend

    series = [
        {"ts": 0, "v": None},
        {"ts": 86400, "v": 50.0},
        {"ts": 172800, "v": None},
        {"ts": 259200, "v": 60.0},
    ]
    trend = compute_trend(series, threshold=90.0)
    assert trend["start"] == 50.0
    assert trend["end"] == 60.0


def test_compute_trend_zero_start_value_guards_division():
    """Regression test: start_v = 0.0 should not cause ZeroDivisionError."""
    from app.host_metrics_ai import compute_trend

    series = [{"ts": 0, "v": 0.0}, {"ts": 86400, "v": 10.0}]
    trend = compute_trend(series, threshold=90.0)

    # pct_change should be 0.0 (guarded by 'if start_v else 0.0')
    assert trend["start"] == 0.0
    assert trend["end"] == 10.0
    assert trend["pct_change"] == 0.0
    # slope_per_day = (10.0 - 0.0) / 1.0 = 10.0
    assert trend["slope_per_day"] == 10.0


def test_compute_trend_falling_series_no_threshold_projection():
    """Regression test: falling series should have negative slope and no threshold projection."""
    from app.host_metrics_ai import compute_trend

    # 3 points declining from 80 to 60 over 2 days
    series = [
        {"ts": 0, "v": 80.0},
        {"ts": 86400, "v": 70.0},
        {"ts": 172800, "v": 60.0},
    ]
    trend = compute_trend(series, threshold=90.0)

    assert trend["start"] == 80.0
    assert trend["end"] == 60.0
    assert trend["pct_change"] == -25.0  # (60-80)/80 * 100
    assert trend["slope_per_day"] == -10.0  # (60-80)/2 = -10.0
    # days_to_threshold must be None (slope_per_day > 0 guard is False)
    assert trend["days_to_threshold"] is None


def test_compute_trend_at_or_above_threshold_returns_zero_days():
    """Regression test: a series already at/above the threshold should
    report days_to_threshold == 0.0, not None, so it's distinguishable
    from a healthy/falling series."""
    from app.host_metrics_ai import compute_trend

    series = [
        {"ts": 0, "v": 85.0},
        {"ts": 86400, "v": 92.0},
    ]
    trend = compute_trend(series, threshold=90.0)

    assert trend["end"] == 92.0
    assert trend["days_to_threshold"] == 0.0


def test_build_trend_narrative_calls_provider():
    from app.host_metrics_ai import build_trend_narrative

    trends = {
        "cpu": {"start": 20.0, "end": 22.0, "pct_change": 10.0, "slope_per_day": 0.28, "days_to_threshold": None},
        "mem": {"start": 60.0, "end": 75.0, "pct_change": 25.0, "slope_per_day": 2.1, "days_to_threshold": 7.1},
        "disk": {"start": 40.0, "end": 40.0, "pct_change": 0.0, "slope_per_day": 0.0, "days_to_threshold": None},
    }
    ai_usage_summary = {"total_calls": 42, "total_cost_usd": 3.15, "total_failures": 1}

    with patch("app.llm.get_provider") as mock_get_provider:
        mock_get_provider.return_value.narrate.return_value = "Memory usage is climbing and will hit 90% in about a week."
        narrative = build_trend_narrative(trends, ai_usage_summary)

    assert "90%" in narrative
    user_prompt = mock_get_provider.return_value.narrate.call_args.kwargs["user_prompt"]
    sent = json.loads(user_prompt)
    assert sent["trends"]["mem"]["days_to_threshold"] == 7.1
    assert sent["ai_usage"]["total_cost_usd"] == 3.15
