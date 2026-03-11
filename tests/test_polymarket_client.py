from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import polymarket_client as pc


def _make_gamma_market(condition_id, question, volume, end_date, category="politics", active=True, closed=False):
    return {
        "conditionId": condition_id,
        "question": question,
        "volume": str(volume),
        "endDate": end_date,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.6", "0.4"]',
        "clobTokenIds": '["t_yes", "t_no"]',
        "active": active,
        "closed": closed,
        "events": [{"id": "1", "category": category, "slug": f"event-{condition_id}"}],
    }


def _book(*levels):
    return SimpleNamespace(
        bids=[],
        asks=[SimpleNamespace(price=price, size=size) for price, size in levels],
    )


def test_normalize_market():
    raw = _make_gamma_market("abc123", "Will ETH hit $5k?", 10000, "2026-06-01T00:00:00Z", category="crypto")
    result = pc.normalize_market(raw)
    assert result["market_id"] == "abc123"
    assert result["yes_price"] == 0.6
    assert result["no_price"] == 0.4
    assert result["yes_token_id"] == "t_yes"
    assert result["category"] == "crypto"
    assert result["event_slug"] == "event-abc123"


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


def test_find_expiry_candidates_filters_and_sorts():
    raw_markets = [
        _make_gamma_market("1", "Q1", 5000, "2026-03-12T00:00:00Z", category="politics"),
        _make_gamma_market("2", "Q2", 5000, "2026-03-12T00:00:00Z", category="sports"),
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = raw_markets
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.get_order_book.side_effect = [
        _book((0.94, 100), (0.95, 100)),
        _book((0.97, 100)),
    ]
    with patch("polymarket_client.requests.get", return_value=mock_resp), patch("polymarket_client._get_client", return_value=mock_client):
        candidates = pc.find_expiry_candidates(max_days=3, min_volume=1000, min_discount=0.02)
    assert len(candidates) == 1
    assert candidates[0]["market_id"] == "1"
    assert candidates[0]["discount"] == pytest.approx(0.06)


def test_walk_order_book_calculates_average_fill():
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = _book((0.90, 5), (0.95, 10))
    with patch("polymarket_client._get_client", return_value=mock_client):
        avg_price, limit_price = pc.walk_order_book("token", notional=9.0)
    assert round(avg_price, 4) == pytest.approx(0.9243, rel=1e-4)
    assert limit_price == 0.95


def test_walk_order_book_walks_multiple_levels():
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = _book((0.90, 5), (0.95, 10))
    with patch("polymarket_client._get_client", return_value=mock_client):
        avg_price, limit_price = pc.walk_order_book("token", notional=10.0)
    assert round(avg_price, 4) == round(10.0 / ((0.90 * 5) / 0.90 + (5.5 / 0.95)), 4)
    assert limit_price == 0.95


def test_calculate_pnl():
    pnl = pc.calculate_pnl(side="BUY", outcome="YES", entry_price=0.4, current_price=0.6, size_usd=10.0)
    assert abs(pnl - 5.0) < 0.01
