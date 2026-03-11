import json
from unittest.mock import MagicMock, patch

import gemini_agent as ga


def test_parse_verify_response_valid():
    raw = json.dumps(
        {
            "verified": True,
            "outcome": "YES",
            "confidence": 0.91,
            "reasoning": "The event has already happened.",
        }
    )
    result = ga.parse_verify_response(raw)
    assert result == {
        "verified": True,
        "outcome": "YES",
        "confidence": 0.91,
        "reasoning": "The event has already happened.",
    }


def test_parse_verify_response_rejects_invalid_confidence():
    raw = json.dumps(
        {
            "verified": True,
            "outcome": "YES",
            "confidence": 1.5,
            "reasoning": "bad",
        }
    )
    assert ga.parse_verify_response(raw) is None


def test_parse_verify_response_requires_all_fields():
    assert ga.parse_verify_response(json.dumps({"verified": True})) is None


def test_verify_outcome_returns_parsed_result():
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "verified": True,
            "outcome": "NO",
            "confidence": 0.88,
            "reasoning": "The official result is already published.",
        }
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("gemini_agent.genai") as mock_genai, patch("gemini_agent.genai_types") as mock_types, patch("gemini_agent.GENAI_AVAILABLE", True):
        mock_genai.Client.return_value = mock_client
        mock_types.GenerateContentConfig.return_value = object()
        mock_types.Tool.return_value = object()
        mock_types.GoogleSearch.return_value = object()
        result = ga.verify_outcome(
            "Has the event happened?",
            {"end_date_iso": "2026-03-12T00:00:00Z", "category": "politics"},
        )

    assert result["verified"] is True
    assert result["outcome"] == "NO"
    assert result["confidence"] == 0.88
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    assert "config" in call_kwargs


def test_verify_outcome_logs_and_returns_none_when_api_call_fails(caplog):
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("invalid api key")

    with patch("gemini_agent.config.GEMINI_API_KEY", "test-key"), patch("gemini_agent.genai") as mock_genai, patch("gemini_agent.genai_types") as mock_types, patch("gemini_agent.GENAI_AVAILABLE", True):
        mock_genai.Client.return_value = mock_client
        mock_types.GenerateContentConfig.return_value = object()
        mock_types.Tool.return_value = object()
        mock_types.GoogleSearch.return_value = object()
        result = ga.verify_outcome("Has the event happened?")

    assert result is None
    assert "Verification request failed: invalid api key" in caplog.text


def test_verify_outcome_logs_and_returns_none_when_api_key_missing(caplog):
    with patch("gemini_agent.config.GEMINI_API_KEY", ""), patch("gemini_agent.GENAI_AVAILABLE", True):
        result = ga.verify_outcome("Has the event happened?")

    assert result is None
    assert "GEMINI_API_KEY is empty" in caplog.text
