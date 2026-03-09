import pytest
from unittest.mock import MagicMock, patch
import gemini_agent as ga


def test_build_performance_context_empty():
    context = ga.build_performance_context({})
    assert "no closed trades" in context.lower() or "no data" in context.lower()


def test_build_performance_context_with_data():
    perf = {
        "crypto": {"win_rate": 0.65, "total": 20, "wins": 13, "total_pnl": 45.0, "avg_edge": 0.12},
        "sports": {"win_rate": 0.40, "total": 10, "wins": 4, "total_pnl": -12.0, "avg_edge": 0.09},
    }
    context = ga.build_performance_context(perf)
    assert "crypto" in context.lower()
    assert "65%" in context or "0.65" in context
    assert "sports" in context.lower()


def test_parse_gemini_response_valid_json():
    raw = '{"probability": 0.72, "side": "YES", "confidence": "high", "reasoning": "Strong trend"}'
    result = ga.parse_gemini_response(raw)
    assert result["probability"] == 0.72
    assert result["side"] == "YES"
    assert result["confidence"] == "high"


def test_parse_gemini_response_json_in_markdown():
    raw = '```json\n{"probability": 0.55, "side": "NO", "confidence": "medium", "reasoning": "Uncertain"}\n```'
    result = ga.parse_gemini_response(raw)
    assert result["probability"] == 0.55
    assert result["side"] == "NO"


def test_parse_gemini_response_invalid_returns_none():
    raw = "I cannot analyze this market."
    result = ga.parse_gemini_response(raw)
    assert result is None


def test_calculate_position_size_kelly():
    size = ga.calculate_position_size(
        probability=0.65,
        entry_price=0.5,
        portfolio_value=500.0,
        max_position=20.0,
        kelly_fraction=0.25,
    )
    assert 0 < size <= 20.0


def test_calculate_position_size_capped():
    size = ga.calculate_position_size(
        probability=0.99,
        entry_price=0.01,
        portfolio_value=500.0,
        max_position=20.0,
        kelly_fraction=0.25,
    )
    assert size <= 20.0
