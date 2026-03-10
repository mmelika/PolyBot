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


def test_parse_screen_response_valid():
    raw = '[{"market_id": "abc", "initial_lean": "YES", "reason": "Strong trend"}]'
    result = ga.parse_screen_response(raw)
    assert len(result) == 1
    assert result[0]["market_id"] == "abc"
    assert result[0]["initial_lean"] == "YES"


def test_parse_screen_response_empty_array():
    result = ga.parse_screen_response("[]")
    assert result == []


def test_parse_screen_response_invalid_returns_empty():
    result = ga.parse_screen_response("not json at all")
    assert result == []


def test_parse_screen_response_filters_bad_entries():
    # entries missing required fields are dropped
    raw = '[{"market_id": "a", "initial_lean": "YES", "reason": "ok"}, {"market_id": "b"}]'
    result = ga.parse_screen_response(raw)
    assert len(result) == 1
    assert result[0]["market_id"] == "a"


def test_screen_markets_returns_flagged(monkeypatch):
    markets = [
        {"market_id": "m1", "question": "Will X win?", "yes_price": 0.4,
         "no_price": 0.6, "volume": 5000, "end_date_iso": "2026-04-01T00:00:00Z", "category": "sports"},
        {"market_id": "m2", "question": "Will Y happen?", "yes_price": 0.6,
         "no_price": 0.4, "volume": 2000, "end_date_iso": "2026-04-01T00:00:00Z", "category": "politics"},
    ]
    open_ids = set()
    mock_response = MagicMock()
    mock_response.text = '[{"market_id": "m1", "initial_lean": "YES", "reason": "Good edge"}]'

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("gemini_agent.genai") as mock_genai:
        mock_genai.Client.return_value = mock_client
        result = ga.screen_markets(markets, open_ids)

    assert len(result) == 1
    assert result[0]["market_id"] == "m1"


def test_screen_markets_excludes_open_positions(monkeypatch):
    markets = [
        {"market_id": "m1", "question": "Q1?", "yes_price": 0.4, "no_price": 0.6,
         "volume": 5000, "end_date_iso": "2026-04-01T00:00:00Z", "category": "sports"},
    ]
    open_ids = {"m1"}  # already open
    mock_response = MagicMock()
    mock_response.text = '[{"market_id": "m1", "initial_lean": "YES", "reason": "ok"}]'

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("gemini_agent.genai") as mock_genai:
        mock_genai.Client.return_value = mock_client
        result = ga.screen_markets(markets, open_ids)

    # m1 is already open — must be filtered out even if Gemini flagged it
    assert all(f["market_id"] != "m1" for f in result)


import json as _json


def test_parse_research_response_valid():
    raw = _json.dumps({
        "key_facts": ["Forest are 17th", "3 teams on 28pts"],
        "base_rate": "Teams in 17th at this stage relegated ~45% historically",
        "recent_developments": "Lost last 3 games, striker injured",
        "uncertainty_factors": ["3-way tie", "9 games left"]
    })
    result = ga.parse_research_response(raw)
    assert result is not None
    assert len(result["key_facts"]) == 2
    assert "base_rate" in result


def test_parse_research_response_missing_field_returns_none():
    raw = _json.dumps({"key_facts": ["fact1"]})  # missing other required fields
    result = ga.parse_research_response(raw)
    assert result is None


def test_parse_research_response_invalid_json_returns_none():
    result = ga.parse_research_response("not json")
    assert result is None


def test_research_market_returns_brief(monkeypatch):
    market = {
        "market_id": "m1",
        "question": "Will Nottingham Forest be relegated?",
        "yes_price": 0.35,
        "no_price": 0.65,
        "volume": 20000,
        "end_date_iso": "2026-05-20T00:00:00Z",
        "category": "sports",
    }
    mock_response = MagicMock()
    mock_response.text = _json.dumps({
        "key_facts": ["Forest 17th, 1pt above drop zone"],
        "base_rate": "~40% historically",
        "recent_developments": "Lost 3 in a row",
        "uncertainty_factors": ["3-way battle"]
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("gemini_agent.genai") as mock_genai:
        mock_genai.Client.return_value = mock_client
        result = ga.research_market(market)

    assert result is not None
    assert "key_facts" in result
    assert "base_rate" in result
    # verify config (with search grounding tool) was passed
    call_kwargs = mock_client.models.generate_content.call_args
    gen_config = call_kwargs.kwargs.get("config")
    assert gen_config is not None
