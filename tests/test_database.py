import pytest
import os
import tempfile
import database


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    return db_path


def test_init_creates_tables(db):
    import sqlite3
    conn = sqlite3.connect(db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert "trades" in tables
    assert "portfolio_snapshots" in tables
    assert "app_state" in tables


def test_insert_and_get_trade(db):
    trade = {
        "market_id": "mkt_001",
        "question": "Will Bitcoin hit $100k?",
        "category": "crypto",
        "outcome": "YES",
        "side": "BUY",
        "size_usd": 15.0,
        "entry_price": 0.45,
        "current_price": 0.45,
        "pnl": 0.0,
        "status": "FILLED",
        "mode": "paper",
        "gemini_probability": 0.60,
        "gemini_reasoning": "Based on current trends...",
        "edge": 0.15,
        "closes_at": "2026-04-01",
    }
    trade_id = database.insert_trade(db, trade)
    assert trade_id is not None
    trades = database.get_open_trades(db)
    assert len(trades) == 1
    assert trades[0]["question"] == "Will Bitcoin hit $100k?"


def test_update_trade_price(db):
    trade = {
        "market_id": "mkt_002",
        "question": "Will it rain?",
        "category": "weather",
        "outcome": "NO",
        "side": "BUY",
        "size_usd": 10.0,
        "entry_price": 0.30,
        "current_price": 0.30,
        "pnl": 0.0,
        "status": "FILLED",
        "mode": "paper",
        "gemini_probability": 0.20,
        "gemini_reasoning": "Low probability",
        "edge": 0.10,
        "closes_at": "2026-03-15",
    }
    trade_id = database.insert_trade(db, trade)
    database.update_trade_price(db, trade_id, 0.25, pnl=2.0)
    trades = database.get_open_trades(db)
    assert trades[0]["current_price"] == 0.25
    assert trades[0]["pnl"] == 2.0


def test_snapshot_portfolio(db):
    database.snapshot_portfolio(db, total_value=510.0, cash_balance=100.0, mode="paper")
    snapshots = database.get_portfolio_snapshots(db, limit=10)
    assert len(snapshots) == 1
    assert snapshots[0]["total_value"] == 510.0


def test_get_performance_by_category(db):
    for i, (cat, resolution) in enumerate([("crypto", "WIN"), ("sports", "LOSS"), ("crypto", "WIN")]):
        trade = {
            "market_id": f"mkt_{i}",
            "question": f"Question {i}",
            "category": cat,
            "outcome": "YES",
            "side": "BUY",
            "size_usd": 10.0,
            "entry_price": 0.5,
            "current_price": 0.5,
            "pnl": 5.0 if resolution == "WIN" else -5.0,
            "status": "CLOSED",
            "mode": "paper",
            "gemini_probability": 0.6,
            "gemini_reasoning": "test",
            "edge": 0.1,
            "closes_at": "2026-03-15",
        }
        trade_id = database.insert_trade(db, trade)
        database.close_trade(db, trade_id, resolution=resolution, resolved_price=0.9 if resolution == "WIN" else 0.1)

    perf = database.get_performance_by_category(db)
    assert perf["crypto"]["win_rate"] == 1.0
    assert perf["sports"]["win_rate"] == 0.0


def test_get_set_app_state(db):
    database.set_app_state(db, "trading_mode", "real")
    val = database.get_app_state(db, "trading_mode")
    assert val == "real"


def test_get_recent_trades(db):
    for i in range(5):
        trade = {
            "market_id": f"mkt_{i}",
            "question": f"Question {i}",
            "category": "sports",
            "outcome": "NO",
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
            "closes_at": "2026-04-01",
        }
        database.insert_trade(db, trade)
    recent = database.get_recent_trades(db, limit=3)
    assert len(recent) == 3


def test_research_brief_column_exists(tmp_path):
    import database
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(trades)")
    cols = [row[1] for row in cursor.fetchall()]
    conn.close()
    assert "research_brief" in cols


def test_reset_paper_trading_wipes_paper_data(tmp_path):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    database.insert_trade(db, {
        "market_id": "m1", "question": "Q?", "category": "other",
        "outcome": "YES", "side": "BUY", "size_usd": 50.0,
        "entry_price": 0.5, "current_price": 0.5, "pnl": 0.0,
        "status": "FILLED", "mode": "paper",
        "gemini_probability": 0.6, "gemini_reasoning": "r",
        "edge": 0.1, "closes_at": "2026-04-01",
    })
    database.snapshot_portfolio(db, 4800.0, 4750.0, "paper")
    database.reset_paper_trading(db, starting_capital=5000.0)
    assert database.get_open_trades(db) == []
    snaps = database.get_portfolio_snapshots(db)
    assert len(snaps) == 1
    assert snaps[0]["total_value"] == 5000.0
    assert snaps[0]["cash_balance"] == 5000.0


def test_reset_paper_trading_preserves_real_data(tmp_path):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    database.insert_trade(db, {
        "market_id": "m2", "question": "Q2?", "category": "other",
        "outcome": "YES", "side": "BUY", "size_usd": 100.0,
        "entry_price": 0.6, "current_price": 0.6, "pnl": 0.0,
        "status": "FILLED", "mode": "real",
        "gemini_probability": 0.7, "gemini_reasoning": "r",
        "edge": 0.1, "closes_at": "2026-04-01",
    })
    database.snapshot_portfolio(db, 10000.0, 9900.0, "real")
    database.reset_paper_trading(db, starting_capital=5000.0)
    real_trades = database.get_open_trades(db)
    assert len(real_trades) == 1
    assert real_trades[0]["mode"] == "real"
