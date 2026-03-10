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


def _settings(min_advantage=0.08, long_term_days=7, long_term_min_prob=0.80):
    """Return a minimal settings dict for should_trade tests."""
    return {
        "min_advantage": min_advantage,
        "long_term_days": long_term_days,
        "long_term_min_prob": long_term_min_prob,
    }


# --- existing behaviour still works ---

def test_should_trade_rejects_low_confidence():
    assert trader.should_trade(_analysis(0.9, edge=0.15, confidence="low"), _market(3), _settings()) is False


def test_should_trade_rejects_insufficient_edge():
    assert trader.should_trade(_analysis(0.9, edge=0.01), _market(3), _settings()) is False


def test_should_trade_accepts_short_term_normal():
    """Market closing in 3 days with good edge — should trade."""
    assert trader.should_trade(_analysis(0.5, edge=0.10), _market(3), _settings()) is True


# --- new long-term filter ---

def test_should_trade_rejects_long_term_low_prob():
    """Market closing in 10 days with probability 0.60 — reject."""
    assert trader.should_trade(_analysis(0.60, edge=0.15), _market(10), _settings()) is False


def test_should_trade_accepts_long_term_high_prob():
    """Market closing in 10 days with probability 0.85 — accept."""
    assert trader.should_trade(_analysis(0.85, edge=0.15), _market(10), _settings()) is True


def test_should_trade_boundary_exactly_7_days():
    """Market closing in exactly 7 days — not long-term, normal rules apply."""
    assert trader.should_trade(_analysis(0.50, edge=0.10), _market(7), _settings()) is True


def test_should_trade_boundary_exactly_08_days():
    """Market closing in 8 days with prob 0.79 — reject (just over threshold)."""
    assert trader.should_trade(_analysis(0.79, edge=0.15), _market(8), _settings()) is False


def test_should_trade_missing_end_date_permissive():
    """No end_date_iso — skip long-term check, let edge/confidence decide."""
    market = {"end_date_iso": "", "question": "No date?"}
    assert trader.should_trade(_analysis(0.50, edge=0.10), market, _settings()) is True


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
    trades = database.get_open_trades(mock_db, "paper")
    assert len(trades) == 1
    assert trades[0]["mode"] == "paper"
    assert trades[0]["size_usd"] == 15.0


def _analysis_with_contested(probability=0.5, edge=0.15, confidence="medium",
                               base_rate_estimate=0.45, contested=True):
    return {
        "probability": probability,
        "edge": edge,
        "confidence": confidence,
        "base_rate_estimate": base_rate_estimate,
        "contested": contested,
    }


def test_should_trade_rejects_contested_with_low_confidence():
    analysis = _analysis_with_contested(contested=True, confidence="medium", edge=0.12)
    assert trader.should_trade(analysis, _market(3), _settings()) is False


def test_should_trade_rejects_contested_with_insufficient_edge():
    analysis = _analysis_with_contested(contested=True, confidence="high", edge=0.10)
    assert trader.should_trade(analysis, _market(3), _settings()) is False


def test_should_trade_accepts_contested_high_conf_large_edge():
    analysis = _analysis_with_contested(contested=True, confidence="high", edge=0.20)
    assert trader.should_trade(analysis, _market(3), _settings()) is True


def test_should_trade_uncontested_normal_rules_apply():
    analysis = _analysis_with_contested(contested=False, confidence="medium", edge=0.10)
    assert trader.should_trade(analysis, _market(3), _settings()) is True


def test_scan_and_trade_three_phase_pipeline(mock_db):
    """Full pipeline: screener flags 1 market → research → probability → trade placed."""
    import json

    markets = [{
        "market_id": "mkt_pipeline",
        "question": "Will Chelsea win the league?",
        "category": "sports",
        "yes_price": 0.30,
        "no_price": 0.70,
        "volume": 50000,
        "end_date_iso": "2026-05-20T00:00:00Z",
        "yes_token_id": "t_yes",
        "no_token_id": "t_no",
    }]

    screen_result = [{"market_id": "mkt_pipeline", "initial_lean": "YES", "reason": "Undervalued"}]
    research_result = {
        "key_facts": ["Chelsea top of league"],
        "base_rate": "Leaders at this stage win ~70%",
        "recent_developments": "Won last 5",
        "uncertainty_factors": ["Still 10 games left"],
    }
    analysis_result = {
        "probability": 0.82,
        "side": "YES",
        "confidence": "high",
        "base_rate_estimate": 0.70,
        "contested": False,
        "reasoning": "Strong leader, base rate 70%, recent form confirms.",
        "edge": 0.52,
        "entry_price": 0.30,
        "token_id": "t_yes",
    }

    with patch("trader.polymarket_client.get_active_markets", return_value=markets), \
         patch("trader.gemini_agent.screen_markets", return_value=screen_result), \
         patch("trader.gemini_agent.research_market", return_value=research_result), \
         patch("trader.gemini_agent.assign_probability", return_value=analysis_result), \
         patch("trader.gemini_agent.calculate_position_size", return_value=15.0):
        count = trader.scan_and_trade(mock_db)

    assert count == 1
    import database
    trades = database.get_open_trades(mock_db, "paper")
    assert len(trades) == 1
    assert trades[0]["market_id"] == "mkt_pipeline"
    # research brief stored
    assert trades[0]["research_brief"] is not None
    stored = json.loads(trades[0]["research_brief"])
    assert stored["key_facts"] == ["Chelsea top of league"]


# --- get_skip_reason ---

def test_get_skip_reason_low_confidence():
    reason = trader.get_skip_reason(_analysis(0.9, edge=0.15, confidence="low"), _market(3), _settings())
    assert reason == "confidence: low"


def test_get_skip_reason_edge_too_small():
    reason = trader.get_skip_reason(_analysis(0.9, edge=0.01), _market(3), _settings())
    assert "edge too small" in reason
    assert "1.0%" in reason


def test_get_skip_reason_long_term_low_prob():
    reason = trader.get_skip_reason(_analysis(0.60, edge=0.15), _market(10), _settings())
    assert "long-term" in reason


def test_get_skip_reason_contested():
    analysis = _analysis_with_contested(contested=True, confidence="medium", edge=0.12)
    reason = trader.get_skip_reason(analysis, _market(3), _settings())
    assert "contested" in reason


def test_get_skip_reason_none_when_should_trade():
    reason = trader.get_skip_reason(_analysis(0.85, edge=0.15), _market(3), _settings())
    assert reason is None


def test_should_trade_delegates_to_get_skip_reason():
    """should_trade() must agree with get_skip_reason() on all cases."""
    cases = [
        (_analysis(0.9, edge=0.15, confidence="low"), _market(3)),
        (_analysis(0.9, edge=0.01), _market(3)),
        (_analysis(0.60, edge=0.15), _market(10)),
        (_analysis(0.85, edge=0.15), _market(3)),
    ]
    settings = _settings()
    for analysis, market in cases:
        skip_reason = trader.get_skip_reason(analysis, market, settings)
        expected = skip_reason is None
        assert trader.should_trade(analysis, market, settings) == expected


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
