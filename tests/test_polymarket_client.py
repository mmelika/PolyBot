import pytest
from unittest.mock import MagicMock, patch
import polymarket_client as pc


def _make_gamma_market(condition_id, question, volume, end_date, outcomes=None, prices=None, token_ids=None, active=True, closed=False):
    """Helper to build a market dict matching the Gamma API format."""
    return {
        "conditionId": condition_id,
        "question": question,
        "volume": str(volume),
        "endDate": end_date,
        "outcomes": outcomes or '["Yes", "No"]',
        "outcomePrices": prices or '["0.6", "0.4"]',
        "clobTokenIds": token_ids or '["t1", "t2"]',
        "active": active,
        "closed": closed,
    }


def test_get_active_markets_filters_low_volume():
    raw_markets = [
        _make_gamma_market("1", "Q1", 5000, "2026-04-01T00:00:00Z"),
        _make_gamma_market("2", "Q2", 500, "2026-04-01T00:00:00Z"),
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = raw_markets
    mock_resp.raise_for_status = MagicMock()
    with patch("polymarket_client.requests.get", return_value=mock_resp):
        markets = pc.get_active_markets(min_volume=1000)
    assert len(markets) == 1
    assert markets[0]["condition_id"] == "1"


def test_get_active_markets_filters_expired():
    raw_markets = [
        _make_gamma_market("1", "Q1", 5000, "2020-01-01T00:00:00Z"),
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = raw_markets
    mock_resp.raise_for_status = MagicMock()
    with patch("polymarket_client.requests.get", return_value=mock_resp):
        markets = pc.get_active_markets(min_volume=1000, min_hours_to_close=24)
    assert len(markets) == 0


def test_normalize_market():
    raw = {
        "conditionId": "abc123",
        "question": "Will ETH hit $5k?",
        "volume": "10000",
        "endDate": "2026-06-01T00:00:00Z",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.35", "0.65"]',
        "clobTokenIds": '["t1", "t2"]',
        "active": True,
        "closed": False,
    }
    result = pc.normalize_market(raw)
    assert result["market_id"] == "abc123"
    assert result["yes_price"] == 0.35
    assert result["no_price"] == 0.65
    assert result["yes_token_id"] == "t1"
    assert result["volume"] == 10000.0


def test_normalize_market_with_events_category():
    raw = {
        "conditionId": "xyz",
        "question": "Who wins?",
        "volume": "5000",
        "endDate": "2026-06-01T00:00:00Z",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]',
        "clobTokenIds": '["t1", "t2"]',
        "active": True,
        "closed": False,
        "events": [{"id": "1", "category": "crypto"}],
    }
    result = pc.normalize_market(raw)
    assert result["category"] == "crypto"


def test_calculate_pnl_long_yes():
    # Bought YES at 0.4, now at 0.6, size $10
    pnl = pc.calculate_pnl(side="BUY", outcome="YES", entry_price=0.4, current_price=0.6, size_usd=10.0)
    assert abs(pnl - 5.0) < 0.01  # (0.6-0.4)/0.4 * 10 = 5.0


def test_calculate_pnl_long_no():
    # Bought NO at 0.5, now at 0.3, size $10
    pnl = pc.calculate_pnl(side="BUY", outcome="NO", entry_price=0.5, current_price=0.3, size_usd=10.0)
    assert abs(pnl - (-4.0)) < 0.01  # (0.3-0.5)/0.5 * 10 = -4.0


def test_parse_json_str_handles_list():
    assert pc._parse_json_str(["a", "b"]) == ["a", "b"]


def test_parse_json_str_handles_string():
    assert pc._parse_json_str('["a", "b"]') == ["a", "b"]


def test_parse_json_str_handles_invalid():
    assert pc._parse_json_str("not json") == []
    assert pc._parse_json_str(None) == []
