import sqlite3
from datetime import datetime
from typing import Optional


def init_db(db_path: str) -> None:
    """Create tables if they don't exist."""
    import os
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            question TEXT NOT NULL,
            category TEXT DEFAULT 'other',
            outcome TEXT NOT NULL,
            side TEXT NOT NULL,
            size_usd REAL NOT NULL,
            entry_price REAL NOT NULL,
            current_price REAL NOT NULL,
            pnl REAL DEFAULT 0.0,
            status TEXT DEFAULT 'FILLED',
            mode TEXT DEFAULT 'paper',
            gemini_probability REAL,
            gemini_reasoning TEXT,
            edge REAL,
            closes_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT,
            resolution TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            total_value REAL NOT NULL,
            cash_balance REAL NOT NULL,
            mode TEXT DEFAULT 'paper'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN research_brief TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    conn.close()


def _row_to_dict(cursor, row) -> dict:
    return {col[0]: val for col, val in zip(cursor.description, row)}


def insert_trade(db_path: str, trade: dict) -> int:
    conn = sqlite3.connect(db_path)
    row = {"research_brief": None, **trade}
    cursor = conn.execute("""
        INSERT INTO trades (market_id, question, category, outcome, side, size_usd,
            entry_price, current_price, pnl, status, mode, gemini_probability,
            gemini_reasoning, edge, closes_at, research_brief)
        VALUES (:market_id, :question, :category, :outcome, :side, :size_usd,
            :entry_price, :current_price, :pnl, :status, :mode, :gemini_probability,
            :gemini_reasoning, :edge, :closes_at, :research_brief)
    """, row)
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def update_trade_price(db_path: str, trade_id: int, current_price: float, pnl: float) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE trades SET current_price = ?, pnl = ? WHERE id = ?",
        (current_price, pnl, trade_id)
    )
    conn.commit()
    conn.close()


def close_trade(db_path: str, trade_id: int, resolution: str, resolved_price: float) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        UPDATE trades
        SET status = 'CLOSED', resolution = ?, resolved_at = datetime('now'),
            current_price = ?
        WHERE id = ?
    """, (resolution, resolved_price, trade_id))
    conn.commit()
    conn.close()


def get_open_trades(db_path: str, mode: str = "paper") -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM trades WHERE status IN ('FILLED', 'PENDING') AND mode = ? ORDER BY created_at DESC",
        (mode,)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_recent_trades(db_path: str, limit: int = 20, mode: str = "paper") -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM trades WHERE mode = ? ORDER BY created_at DESC LIMIT ?", (mode, limit)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def snapshot_portfolio(db_path: str, total_value: float, cash_balance: float, mode: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO portfolio_snapshots (total_value, cash_balance, mode) VALUES (?, ?, ?)",
        (total_value, cash_balance, mode)
    )
    conn.commit()
    conn.close()


def get_portfolio_snapshots(db_path: str, limit: int = 200, mode: str = "paper") -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE mode = ? ORDER BY timestamp DESC LIMIT ?",
        (mode, limit)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return list(reversed(rows))


def get_performance_by_category(db_path: str, mode: str = "paper") -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("""
        SELECT category,
               COUNT(*) as total,
               SUM(CASE WHEN resolution = 'WIN' THEN 1 ELSE 0 END) as wins,
               SUM(pnl) as total_pnl,
               AVG(edge) as avg_edge
        FROM trades
        WHERE status = 'CLOSED' AND resolution IS NOT NULL AND mode = ?
        GROUP BY category
    """, (mode,))
    result = {}
    for row in cursor.fetchall():
        d = dict(row)
        result[d["category"]] = {
            "total": d["total"],
            "wins": d["wins"],
            "win_rate": d["wins"] / d["total"] if d["total"] > 0 else 0,
            "total_pnl": d["total_pnl"] or 0,
            "avg_edge": d["avg_edge"] or 0,
        }
    conn.close()
    return result


def get_daily_stats(db_path: str, mode: str = "paper") -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("""
        SELECT
            COUNT(*) as daily_trades,
            SUM(pnl) as daily_pnl
        FROM trades
        WHERE date(created_at) = date('now') AND mode = ?
    """, (mode,))
    row = dict(cursor.fetchone())
    conn.close()
    return row


def get_total_pnl(db_path: str, mode: str = "paper") -> float:
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT SUM(pnl) FROM trades WHERE mode = ?", (mode,))
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0.0


def get_deployed_capital(db_path: str, mode: str = "paper") -> float:
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT SUM(size_usd) FROM trades WHERE status IN ('FILLED', 'PENDING') AND mode = ?",
        (mode,)
    )
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0.0


def set_app_state(db_path: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO app_state (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    """, (key, value))
    conn.commit()
    conn.close()


def get_app_state(db_path: str, key: str, default: str = None) -> Optional[str]:
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default


# Settings keys with their config defaults and type converters
_SETTINGS_META = {
    "paper_starting_capital": float,
    "real_starting_capital": float,
    "min_advantage": float,
    "max_position_size": float,
    "max_deployed_pct": float,
    "scan_interval_minutes": int,
    "min_market_volume": float,
    "long_term_days": int,
    "long_term_min_prob": float,
}


def _settings_defaults() -> dict:
    import config
    return {
        "paper_starting_capital": config.STARTING_CAPITAL,
        "real_starting_capital": config.STARTING_CAPITAL,
        "min_advantage": config.MIN_EDGE,
        "max_position_size": config.MAX_POSITION_SIZE,
        "max_deployed_pct": config.MAX_DEPLOYED_PCT,
        "scan_interval_minutes": config.SCAN_INTERVAL_MINUTES,
        "min_market_volume": float(config.MIN_MARKET_VOLUME),
        "long_term_days": config.LONG_TERM_DAYS,
        "long_term_min_prob": config.LONG_TERM_MIN_PROB,
    }


def get_settings(db_path: str) -> dict:
    result = _settings_defaults()
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT key, value FROM app_state WHERE key IN ({})".format(
            ",".join("?" * len(_SETTINGS_META))
        ),
        list(_SETTINGS_META.keys()),
    )
    for key, value in cursor.fetchall():
        result[key] = _SETTINGS_META[key](value)
    conn.close()
    return result


def save_settings(db_path: str, settings: dict) -> None:
    conn = sqlite3.connect(db_path)
    for key, value in settings.items():
        if key not in _SETTINGS_META:
            continue
        conn.execute("""
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (key, str(value)))
    conn.commit()
    conn.close()


def get_trade_by_market_id(db_path: str, market_id: str) -> Optional[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM trades WHERE market_id = ? AND status IN ('FILLED','PENDING') LIMIT 1",
        (market_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def reset_paper_trading(db_path: str, starting_capital: float) -> None:
    """Delete all paper-mode trades and snapshots, then seed one snapshot at starting_capital."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM trades WHERE mode = 'paper'")
        conn.execute("DELETE FROM portfolio_snapshots WHERE mode = 'paper'")
        conn.execute(
            "INSERT INTO portfolio_snapshots (total_value, cash_balance, mode) VALUES (?, ?, 'paper')",
            (starting_capital, starting_capital),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
