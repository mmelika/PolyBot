import pytest
from unittest.mock import MagicMock, patch
import trader


@pytest.fixture
def mock_db(tmp_path):
    import database
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    return db_path


def test_should_trade_with_sufficient_edge(mock_db):
    analysis = {"edge": 0.15, "confidence": "high", "probability": 0.65}
    result = trader.should_trade(analysis, min_edge=0.08)
    assert result is True


def test_should_not_trade_with_low_edge(mock_db):
    analysis = {"edge": 0.03, "confidence": "high", "probability": 0.53}
    result = trader.should_trade(analysis, min_edge=0.08)
    assert result is False


def test_should_not_trade_low_confidence():
    analysis = {"edge": 0.20, "confidence": "low", "probability": 0.70}
    result = trader.should_trade(analysis, min_edge=0.08)
    assert result is False


def test_execute_paper_trade(mock_db):
    market = {
        "market_id": "mkt_test",
        "question": "Will X happen?",
        "category": "politics",
        "yes_price": 0.4,
        "no_price": 0.6,
        "yes_token_id": "t1",
        "no_token_id": "t2",
        "end_date_iso": "2026-06-01T00:00:00Z",
    }
    analysis = {
        "side": "YES",
        "probability": 0.60,
        "edge": 0.20,
        "confidence": "high",
        "reasoning": "Strong evidence",
        "entry_price": 0.4,
        "token_id": "t1",
    }
    trade_id = trader.execute_trade(mock_db, market, analysis, size_usd=15.0, mode="paper")
    assert trade_id is not None

    import database
    trades = database.get_open_trades(mock_db)
    assert len(trades) == 1
    assert trades[0]["mode"] == "paper"
    assert trades[0]["size_usd"] == 15.0


def test_skip_already_open_market(mock_db):
    import database
    trade = {
        "market_id": "mkt_existing",
        "question": "Already open",
        "category": "crypto",
        "outcome": "YES",
        "side": "BUY",
        "size_usd": 10.0,
        "entry_price": 0.5,
        "current_price": 0.5,
        "pnl": 0.0,
        "status": "FILLED",
        "mode": "paper",
        "gemini_probability": 0.6,
        "gemini_reasoning": "test",
        "edge": 0.1,
        "closes_at": "2026-06-01",
    }
    database.insert_trade(mock_db, trade)
    assert trader.is_market_already_open(mock_db, "mkt_existing") is True
    assert trader.is_market_already_open(mock_db, "mkt_new") is False
