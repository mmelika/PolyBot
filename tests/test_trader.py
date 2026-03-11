from unittest.mock import patch

import database
import trader


def _candidate(**overrides):
    base = {
        "market_id": "mkt_1",
        "question": "Will this resolve soon?",
        "category": "politics",
        "event_slug": "event-1",
        "yes_ask_price": 0.96,
        "no_ask_price": 0.08,
        "discount": 0.04,
        "best_outcome": "YES",
        "token_ids": {"YES": "yes_token", "NO": "no_token"},
        "end_date_iso": "2026-03-12T00:00:00Z",
    }
    return {**base, **overrides}


def _verification(**overrides):
    base = {
        "verified": True,
        "outcome": "YES",
        "confidence": 0.9,
        "reasoning": "Official reporting confirms the outcome.",
    }
    return {**base, **overrides}


def _settings():
    return {
        "paper_starting_capital": 500.0,
        "real_starting_capital": 500.0,
        "max_position_size": 20.0,
        "max_deployed_pct": 0.8,
        "scan_interval_minutes": 10,
        "min_market_volume": 1000.0,
        "min_discount": 0.02,
        "stop_loss_pct": 0.2,
        "max_expiry_days": 3,
        "max_position_pct": 0.2,
        "max_buy_price": 0.99,
    }


def test_get_skip_reason_requires_verified():
    reason = trader.get_skip_reason(_candidate(), _verification(verified=False), _settings(), 500.0, [])
    assert reason == "outcome not verified"


def test_get_skip_reason_requires_confidence_threshold():
    reason = trader.get_skip_reason(_candidate(), _verification(confidence=0.8), _settings(), 500.0, [])
    assert "confidence too low" in reason


def test_get_skip_reason_checks_concentration_limit():
    open_trades = [{"size_usd": 95.0, "event_slug": "event-1"}]
    reason = trader.get_skip_reason(_candidate(), _verification(), _settings(), 500.0, open_trades)
    assert reason == "event concentration limit reached"


def test_execute_trade_paper_mode(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    with patch("trader.polymarket_client.walk_order_book", return_value=(0.965, 0.97)):
        trade_id = trader.execute_trade(
            db_path,
            _candidate(),
            _verification(),
            size_usd=15.0,
            mode="paper",
            settings=_settings(),
        )
    assert trade_id is not None
    open_trades = database.get_open_trades(db_path, "paper")
    assert len(open_trades) == 1
    assert open_trades[0]["stop_loss_price"] == 0.772
    assert open_trades[0]["strategy"] == "expiry_convergence"


def test_check_stop_losses_closes_trade(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    trade_id = database.insert_trade(
        db_path,
        {
            "market_id": "m1",
            "question": "Q?",
            "category": "politics",
            "outcome": "YES",
            "side": "BUY",
            "size_usd": 10.0,
            "entry_price": 0.95,
            "current_price": 0.95,
            "mode": "paper",
            "stop_loss_price": 0.75,
            "token_id": "token-1",
            "event_slug": "event-1",
        },
    )
    with patch("trader.polymarket_client.get_market_price", return_value=0.70):
        count = trader.check_stop_losses(db_path, "paper", _settings())
    assert count == 1
    trade = database.get_trade_by_id(db_path, trade_id)
    assert trade["status"] == "CLOSED"
    assert trade["resolution"] == "STOPPED_OUT"


def test_scan_and_trade_places_trade(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    database.snapshot_portfolio(db_path, 500.0, 500.0, "paper")
    database.set_app_state(db_path, "trading_mode", "paper")

    with patch("trader.database.get_settings", return_value=_settings()), patch(
        "trader.session_start.run", return_value={"redeemed": 0, "stopped_out": 0}
    ), patch(
        "trader.polymarket_client.find_expiry_candidates", return_value=[_candidate()]
    ), patch(
        "trader.gemini_agent.verify_outcome", return_value=_verification()
    ), patch(
        "trader.polymarket_client.walk_order_book", return_value=(0.96, 0.96)
    ):
        count = trader.scan_and_trade(db_path)

    assert count == 1
    open_trades = database.get_open_trades(db_path, "paper")
    assert len(open_trades) == 1
    assert open_trades[0]["market_id"] == "mkt_1"


def test_scan_and_trade_records_skip(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    database.snapshot_portfolio(db_path, 500.0, 500.0, "paper")
    database.set_app_state(db_path, "trading_mode", "paper")

    with patch("trader.database.get_settings", return_value=_settings()), patch(
        "trader.session_start.run", return_value={"redeemed": 0, "stopped_out": 0}
    ), patch(
        "trader.polymarket_client.find_expiry_candidates", return_value=[_candidate()]
    ), patch(
        "trader.gemini_agent.verify_outcome", return_value=_verification(verified=False)
    ):
        count = trader.scan_and_trade(db_path)

    assert count == 0
    skipped = database.get_skipped_markets(db_path, mode="paper")
    assert len(skipped) == 1
    assert skipped[0]["skip_reason"] == "outcome not verified"
