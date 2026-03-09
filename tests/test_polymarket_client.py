import pytest
from unittest.mock import MagicMock, patch
import polymarket_client as pc


def test_get_active_markets_filters_low_volume():
    raw_markets = [
        {"condition_id": "1", "question": "Q1", "volume": "5000", "end_date_iso": "2026-04-01T00:00:00Z",
         "tokens": [{"token_id": "t1", "outcome": "Yes", "price": 0.6}, {"token_id": "t2", "outcome": "No", "price": 0.4}],
         "category": "sports", "active": True, "closed": False},
        {"condition_id": "2", "question": "Q2", "volume": "500", "end_date_iso": "2026-04-01T00:00:00Z",
         "tokens": [{"token_id": "t3", "outcome": "Yes", "price": 0.5}, {"token_id": "t4", "outcome": "No", "price": 0.5}],
         "category": "crypto", "active": True, "closed": False},
    ]
    with patch("polymarket_client.ClobClient") as mock_clob:
        mock_clob.return_value.get_markets.return_value.data = raw_markets
        markets = pc.get_active_markets(min_volume=1000)
    assert len(markets) == 1
    assert markets[0]["condition_id"] == "1"


def test_get_active_markets_filters_expired():
    raw_markets = [
        {"condition_id": "1", "question": "Q1", "volume": "5000", "end_date_iso": "2020-01-01T00:00:00Z",
         "tokens": [{"token_id": "t1", "outcome": "Yes", "price": 0.6}, {"token_id": "t2", "outcome": "No", "price": 0.4}],
         "category": "sports", "active": True, "closed": False},
    ]
    with patch("polymarket_client.ClobClient") as mock_clob:
        mock_clob.return_value.get_markets.return_value.data = raw_markets
        markets = pc.get_active_markets(min_volume=1000, min_hours_to_close=24)
    assert len(markets) == 0


def test_normalize_market():
    raw = {
        "condition_id": "abc123",
        "question": "Will ETH hit $5k?",
        "volume": "10000",
        "end_date_iso": "2026-06-01T00:00:00Z",
        "tokens": [
            {"token_id": "t1", "outcome": "Yes", "price": 0.35},
            {"token_id": "t2", "outcome": "No", "price": 0.65},
        ],
        "category": "crypto",
        "active": True,
        "closed": False,
    }
    result = pc.normalize_market(raw)
    assert result["market_id"] == "abc123"
    assert result["yes_price"] == 0.35
    assert result["no_price"] == 0.65
    assert result["yes_token_id"] == "t1"
    assert result["volume"] == 10000.0


def test_calculate_pnl_long_yes():
    # Bought YES at 0.4, now at 0.6, size $10
    pnl = pc.calculate_pnl(side="BUY", outcome="YES", entry_price=0.4, current_price=0.6, size_usd=10.0)
    assert abs(pnl - 5.0) < 0.01  # (0.6-0.4)/0.4 * 10 = 5.0


def test_calculate_pnl_long_no():
    # Bought NO at 0.5, now at 0.3, size $10
    pnl = pc.calculate_pnl(side="BUY", outcome="NO", entry_price=0.5, current_price=0.3, size_usd=10.0)
    assert abs(pnl - (-4.0)) < 0.01  # (0.3-0.5)/0.5 * 10 = -4.0
