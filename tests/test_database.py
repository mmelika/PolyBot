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
    trades = database.get_open_trades(db, "paper")
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
    trades = database.get_open_trades(db, "paper")
    assert trades[0]["current_price"] == 0.25
    assert trades[0]["pnl"] == 2.0


def test_snapshot_portfolio(db):
    database.snapshot_portfolio(db, total_value=510.0, cash_balance=100.0, mode="paper")
    snapshots = database.get_portfolio_snapshots(db, limit=10, mode="paper")
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

    perf = database.get_performance_by_category(db, "paper")
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
    recent = database.get_recent_trades(db, limit=3, mode="paper")
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
    assert database.get_open_trades(db, "paper") == []
    snaps = database.get_portfolio_snapshots(db, mode="paper")
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
    real_trades = database.get_open_trades(db, "real")
    assert len(real_trades) == 1
    assert real_trades[0]["mode"] == "real"


def test_get_settings_returns_defaults(db):
    """With nothing saved, get_settings returns config defaults."""
    import config
    settings = database.get_settings(db)
    assert settings["paper_starting_capital"] == config.STARTING_CAPITAL
    assert settings["real_starting_capital"] == config.STARTING_CAPITAL
    assert settings["min_advantage"] == config.MIN_EDGE
    assert settings["max_position_size"] == config.MAX_POSITION_SIZE
    assert settings["max_deployed_pct"] == config.MAX_DEPLOYED_PCT
    assert settings["scan_interval_minutes"] == config.SCAN_INTERVAL_MINUTES
    assert settings["min_market_volume"] == config.MIN_MARKET_VOLUME
    assert settings["long_term_days"] == config.LONG_TERM_DAYS
    assert settings["long_term_min_prob"] == config.LONG_TERM_MIN_PROB


def test_save_and_get_settings(db):
    """save_settings persists values, get_settings reads them back."""
    database.save_settings(db, {
        "paper_starting_capital": 10000.0,
        "min_advantage": 0.12,
        "scan_interval_minutes": 5,
    })
    settings = database.get_settings(db)
    assert settings["paper_starting_capital"] == 10000.0
    assert settings["min_advantage"] == 0.12
    assert settings["scan_interval_minutes"] == 5


def test_save_settings_overwrite(db):
    """Saving the same key twice updates it."""
    database.save_settings(db, {"paper_starting_capital": 1000.0})
    database.save_settings(db, {"paper_starting_capital": 2500.0})
    settings = database.get_settings(db)
    assert settings["paper_starting_capital"] == 2500.0


def _make_trade(market_id, mode):
    return {
        "market_id": market_id,
        "question": f"Q {market_id}",
        "category": "crypto",
        "outcome": "YES",
        "side": "BUY",
        "size_usd": 10.0,
        "entry_price": 0.5,
        "current_price": 0.5,
        "pnl": 2.0,
        "status": "FILLED",
        "mode": mode,
        "gemini_probability": 0.6,
        "gemini_reasoning": "test",
        "edge": 0.1,
        "closes_at": "2026-06-01",
    }


def test_get_open_trades_filters_by_mode(db):
    database.insert_trade(db, _make_trade("m1", "paper"))
    database.insert_trade(db, _make_trade("m2", "real"))
    paper = database.get_open_trades(db, "paper")
    real = database.get_open_trades(db, "real")
    assert len(paper) == 1 and paper[0]["market_id"] == "m1"
    assert len(real) == 1 and real[0]["market_id"] == "m2"


def test_get_recent_trades_filters_by_mode(db):
    database.insert_trade(db, _make_trade("m1", "paper"))
    database.insert_trade(db, _make_trade("m2", "real"))
    paper = database.get_recent_trades(db, limit=10, mode="paper")
    assert all(t["mode"] == "paper" for t in paper)
    assert len(paper) == 1


def test_get_total_pnl_filters_by_mode(db):
    database.insert_trade(db, _make_trade("m1", "paper"))
    database.insert_trade(db, _make_trade("m2", "real"))
    assert database.get_total_pnl(db, "paper") == 2.0
    assert database.get_total_pnl(db, "real") == 2.0


def test_get_deployed_capital_filters_by_mode(db):
    database.insert_trade(db, _make_trade("m1", "paper"))
    database.insert_trade(db, _make_trade("m2", "real"))
    assert database.get_deployed_capital(db, "paper") == 10.0
    assert database.get_deployed_capital(db, "real") == 10.0


def test_get_portfolio_snapshots_filters_by_mode(db):
    database.snapshot_portfolio(db, 5000, 5000, "paper")
    database.snapshot_portfolio(db, 9000, 9000, "real")
    paper_snaps = database.get_portfolio_snapshots(db, limit=10, mode="paper")
    real_snaps = database.get_portfolio_snapshots(db, limit=10, mode="real")
    assert len(paper_snaps) == 1 and paper_snaps[0]["total_value"] == 5000
    assert len(real_snaps) == 1 and real_snaps[0]["total_value"] == 9000


def test_get_performance_by_category_filters_by_mode(db):
    """Paper WIN trade should not show in real mode performance."""
    t = {**_make_trade("m1", "paper"), "status": "CLOSED", "pnl": 5.0}
    trade_id = database.insert_trade(db, t)
    database.close_trade(db, trade_id, "WIN", 0.9)
    assert "crypto" in database.get_performance_by_category(db, "paper")
    assert database.get_performance_by_category(db, "real") == {}


def test_get_daily_stats_filters_by_mode(db):
    database.insert_trade(db, _make_trade("m1", "paper"))
    database.insert_trade(db, _make_trade("m2", "real"))
    paper_stats = database.get_daily_stats(db, "paper")
    real_stats = database.get_daily_stats(db, "real")
    assert paper_stats["daily_trades"] == 1
    assert real_stats["daily_trades"] == 1


def test_init_creates_skipped_markets_table(db):
    import sqlite3
    conn = sqlite3.connect(db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert "skipped_markets" in tables


def test_insert_and_get_skipped_market(db):
    record = {
        "market_id": "mkt_skip_001",
        "question": "Will X happen?",
        "category": "crypto",
        "side": "YES",
        "probability": 0.65,
        "edge": 0.05,
        "confidence": "medium",
        "contested": False,
        "skip_reason": "edge too small (5.0%)",
        "reasoning": "Some reasoning here",
        "mode": "paper",
    }
    database.insert_skipped_market(db, record)
    rows = database.get_skipped_markets(db, limit=10, mode="paper")
    assert len(rows) == 1
    assert rows[0]["market_id"] == "mkt_skip_001"
    assert rows[0]["skip_reason"] == "edge too small (5.0%)"


def test_get_skipped_markets_sorted_by_edge_desc(db):
    for edge in [0.05, 0.12, 0.03]:
        database.insert_skipped_market(db, {
            "market_id": f"mkt_{edge}",
            "question": f"Market edge={edge}",
            "category": "other",
            "side": "YES",
            "probability": 0.6,
            "edge": edge,
            "confidence": "medium",
            "contested": False,
            "skip_reason": "edge too small",
            "reasoning": "",
            "mode": "paper",
        })
    rows = database.get_skipped_markets(db, limit=10, mode="paper")
    edges = [r["edge"] for r in rows]
    assert edges == sorted(edges, reverse=True)


def test_get_skipped_markets_filters_by_mode(db):
    for mode in ["paper", "real"]:
        database.insert_skipped_market(db, {
            "market_id": f"mkt_{mode}",
            "question": "Q",
            "category": "other",
            "side": "YES",
            "probability": 0.6,
            "edge": 0.05,
            "confidence": "medium",
            "contested": False,
            "skip_reason": "low confidence",
            "reasoning": "",
            "mode": mode,
        })
    paper_rows = database.get_skipped_markets(db, limit=10, mode="paper")
    assert all(r["mode"] == "paper" for r in paper_rows)
    assert len(paper_rows) == 1


def test_get_trade_by_id(tmp_path):
    import database
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    trade = {
        "market_id": "mkt_1", "question": "Test?", "category": "other",
        "outcome": "YES", "side": "BUY", "size_usd": 10.0,
        "entry_price": 0.4, "current_price": 0.4, "pnl": 0.0,
        "status": "FILLED", "mode": "paper", "gemini_probability": 0.6,
        "gemini_reasoning": "test", "edge": 0.1, "closes_at": "2026-06-01",
    }
    trade_id = database.insert_trade(db_path, trade)
    result = database.get_trade_by_id(db_path, trade_id)
    assert result is not None
    assert result["market_id"] == "mkt_1"
    assert result["id"] == trade_id


def test_get_trade_by_id_missing(tmp_path):
    import database
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    assert database.get_trade_by_id(db_path, 9999) is None
