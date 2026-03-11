import sqlite3

import database
import config


def _trade(market_id="m1", mode="paper", status="FILLED"):
    return {
        "market_id": market_id,
        "question": f"Question {market_id}",
        "category": "crypto",
        "outcome": "YES",
        "side": "BUY",
        "size_usd": 15.0,
        "entry_price": 0.95,
        "current_price": 0.95,
        "pnl": 0.0,
        "status": status,
        "mode": mode,
        "gemini_probability": 0.9,
        "gemini_reasoning": "Verified outcome.",
        "edge": 0.05,
        "closes_at": "2026-03-12",
        "stop_loss_price": 0.76,
        "strategy": "expiry_convergence",
        "token_id": "token-1",
        "event_slug": "event-1",
    }


def test_init_creates_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"trades", "portfolio_snapshots", "app_state", "skipped_markets"} <= tables


def test_insert_trade_persists_new_columns(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    trade_id = database.insert_trade(db_path, _trade())
    trade = database.get_trade_by_id(db_path, trade_id)
    assert trade["stop_loss_price"] == 0.76
    assert trade["strategy"] == "expiry_convergence"
    assert trade["token_id"] == "token-1"


def test_get_settled_unredeemed_trades(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    trade_id = database.insert_trade(db_path, _trade(status="CLOSED"))
    database.close_trade(db_path, trade_id, "WIN", 1.0)
    rows = database.get_settled_unredeemed_trades(db_path, mode="paper")
    assert len(rows) == 1
    database.mark_trade_redeemed(db_path, trade_id)
    assert database.get_settled_unredeemed_trades(db_path, mode="paper") == []


def test_get_settings_returns_new_defaults(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    settings = database.get_settings(db_path)
    assert settings["min_discount"] == config.MIN_DISCOUNT
    assert settings["stop_loss_pct"] == config.STOP_LOSS_PCT
    assert settings["max_expiry_days"] == config.MAX_EXPIRY_DAYS
    assert settings["max_position_pct"] == config.MAX_POSITION_PCT
    assert settings["max_buy_price"] == config.MAX_BUY_PRICE


def test_save_and_get_settings(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    database.save_settings(
        db_path,
        {
            "paper_starting_capital": 1000.0,
            "min_discount": 0.05,
            "stop_loss_pct": 0.1,
            "max_expiry_days": 2,
        },
    )
    settings = database.get_settings(db_path)
    assert settings["paper_starting_capital"] == 1000.0
    assert settings["min_discount"] == 0.05
    assert settings["stop_loss_pct"] == 0.1
    assert settings["max_expiry_days"] == 2


def test_reset_paper_trading_preserves_real_data(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    database.insert_trade(db_path, _trade(mode="paper"))
    database.insert_trade(db_path, _trade(market_id="m2", mode="real"))
    database.snapshot_portfolio(db_path, 500.0, 485.0, "paper")
    database.snapshot_portfolio(db_path, 1000.0, 985.0, "real")
    database.reset_paper_trading(db_path, 500.0)
    assert database.get_open_trades(db_path, "paper") == []
    assert len(database.get_open_trades(db_path, "real")) == 1
