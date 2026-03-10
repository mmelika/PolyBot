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
    cursor = conn.execute("""
        INSERT INTO trades (market_id, question, category, outcome, side, size_usd,
            entry_price, current_price, pnl, status, mode, gemini_probability,
            gemini_reasoning, edge, closes_at)
        VALUES (:market_id, :question, :category, :outcome, :side, :size_usd,
            :entry_price, :current_price, :pnl, :status, :mode, :gemini_probability,
            :gemini_reasoning, :edge, :closes_at)
    """, trade)
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


def get_open_trades(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM trades WHERE status IN ('FILLED', 'PENDING') ORDER BY created_at DESC"
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_recent_trades(db_path: str, limit: int = 20) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
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


def get_portfolio_snapshots(db_path: str, limit: int = 200) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return list(reversed(rows))


def get_performance_by_category(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("""
        SELECT category,
               COUNT(*) as total,
               SUM(CASE WHEN resolution = 'WIN' THEN 1 ELSE 0 END) as wins,
               SUM(pnl) as total_pnl,
               AVG(edge) as avg_edge
        FROM trades
        WHERE status = 'CLOSED' AND resolution IS NOT NULL
        GROUP BY category
    """)
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


def get_daily_stats(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("""
        SELECT
            COUNT(*) as daily_trades,
            SUM(pnl) as daily_pnl
        FROM trades
        WHERE date(created_at) = date('now')
    """)
    row = dict(cursor.fetchone())
    conn.close()
    return row


def get_total_pnl(db_path: str) -> float:
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT SUM(pnl) FROM trades")
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0.0


def get_deployed_capital(db_path: str) -> float:
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT SUM(size_usd) FROM trades WHERE status IN ('FILLED', 'PENDING')"
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
