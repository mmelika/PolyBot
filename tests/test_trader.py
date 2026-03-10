import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import config
import trader


@pytest.fixture
def mock_db(tmp_path):
    import database
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    return db_path


def _market(days_out: float) -> dict:
    """Helper: a market closing `days_out` days from now."""
    close = datetime.now(timezone.utc) + timedelta(days=days_out)
    return {"end_date_iso": close.isoformat(), "question": "Test market?"}


def _analysis(probability: float, edge: float = 0.10, confidence: str = "medium") -> dict:
    return {"probability": probability, "edge": edge, "confidence": confidence}


# --- existing behaviour still works ---

def test_should_trade_rejects_low_confidence():
    assert trader.should_trade(_analysis(0.9, edge=0.15, confidence="low"), _market(3)) is False


def test_should_trade_rejects_insufficient_edge():
    assert trader.should_trade(_analysis(0.9, edge=0.01), _market(3)) is False


def test_should_trade_accepts_short_term_normal():
    """Market closing in 3 days with good edge — should trade."""
    assert trader.should_trade(_analysis(0.5, edge=0.10), _market(3)) is True


# --- new long-term filter ---

def test_should_trade_rejects_long_term_low_prob():
    """Market closing in 10 days with probability 0.60 — reject."""
    assert trader.should_trade(_analysis(0.60, edge=0.15), _market(10)) is False


def test_should_trade_accepts_long_term_high_prob():
    """Market closing in 10 days with probability 0.85 — accept."""
    assert trader.should_trade(_analysis(0.85, edge=0.15), _market(10)) is True


def test_should_trade_boundary_exactly_7_days():
    """Market closing in exactly 7 days — not long-term, normal rules apply."""
    assert trader.should_trade(_analysis(0.50, edge=0.10), _market(7)) is True


def test_should_trade_boundary_exactly_08_days():
    """Market closing in 8 days with prob 0.79 — reject (just over threshold)."""
    assert trader.should_trade(_analysis(0.79, edge=0.15), _market(8)) is False


def test_should_trade_missing_end_date_permissive():
    """No end_date_iso — skip long-term check, let edge/confidence decide."""
    market = {"end_date_iso": "", "question": "No date?"}
    assert trader.should_trade(_analysis(0.50, edge=0.10), market) is True


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
