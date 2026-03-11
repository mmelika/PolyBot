import os
import sqlite3
from typing import Optional


TRADE_EXTRA_COLUMNS = {
    "research_brief": "TEXT",
    "stop_loss_price": "REAL",
    "strategy": "TEXT DEFAULT 'expiry_convergence'",
    "token_id": "TEXT",
    "event_slug": "TEXT",
    "redeemed_at": "TEXT",
}


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
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
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            total_value REAL NOT NULL,
            cash_balance REAL NOT NULL,
            mode TEXT DEFAULT 'paper'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skipped_markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            question TEXT NOT NULL,
            category TEXT DEFAULT 'other',
            side TEXT NOT NULL,
            probability REAL,
            edge REAL,
            confidence TEXT,
            contested INTEGER DEFAULT 0,
            skip_reason TEXT NOT NULL,
            reasoning TEXT,
            mode TEXT DEFAULT 'paper',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    for column_name, definition in TRADE_EXTRA_COLUMNS.items():
        try:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {column_name} {definition}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def insert_trade(db_path: str, trade: dict) -> int:
    row = {
        "category": "other",
        "pnl": 0.0,
        "status": "FILLED",
        "mode": "paper",
        "gemini_probability": None,
        "gemini_reasoning": None,
        "edge": None,
        "closes_at": "",
        "research_brief": None,
        "stop_loss_price": None,
        "strategy": "expiry_convergence",
        "token_id": None,
        "event_slug": None,
        **trade,
    }
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        """
        INSERT INTO trades (
            market_id, question, category, outcome, side, size_usd,
            entry_price, current_price, pnl, status, mode, gemini_probability,
            gemini_reasoning, edge, closes_at, research_brief, stop_loss_price,
            strategy, token_id, event_slug
        )
        VALUES (
            :market_id, :question, :category, :outcome, :side, :size_usd,
            :entry_price, :current_price, :pnl, :status, :mode, :gemini_probability,
            :gemini_reasoning, :edge, :closes_at, :research_brief, :stop_loss_price,
            :strategy, :token_id, :event_slug
        )
        """,
        row,
    )
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def insert_skipped_market(db_path: str, record: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO skipped_markets
            (market_id, question, category, side, probability, edge, confidence,
             contested, skip_reason, reasoning, mode)
        VALUES
            (:market_id, :question, :category, :side, :probability, :edge, :confidence,
             :contested, :skip_reason, :reasoning, :mode)
        """,
        {**record, "contested": int(record.get("contested") or 0)},
    )
    conn.commit()
    conn.close()


def get_skipped_markets(db_path: str, limit: int = 20, mode: str = "paper") -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM skipped_markets
        WHERE mode = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (mode, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_trade_price(db_path: str, trade_id: int, current_price: float, pnl: float) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE trades SET current_price = ?, pnl = ? WHERE id = ?",
        (current_price, pnl, trade_id),
    )
    conn.commit()
    conn.close()


def close_trade(db_path: str, trade_id: int, resolution: str, resolved_price: float) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        UPDATE trades
        SET status = 'CLOSED',
            resolution = ?,
            resolved_at = datetime('now'),
            current_price = ?
        WHERE id = ?
        """,
        (resolution, resolved_price, trade_id),
    )
    conn.commit()
    conn.close()


def mark_trade_redeemed(db_path: str, trade_id: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE trades SET redeemed_at = datetime('now') WHERE id = ?",
        (trade_id,),
    )
    conn.commit()
    conn.close()


def get_open_trades(db_path: str, mode: str = "paper") -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM trades
        WHERE status IN ('FILLED', 'PENDING') AND mode = ?
        ORDER BY created_at DESC
        """,
        (mode,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_trades(db_path: str, limit: int = 20, mode: str = "paper") -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trades WHERE mode = ? ORDER BY created_at DESC LIMIT ?",
        (mode, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_trade_by_market_id(db_path: str, market_id: str) -> Optional[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT * FROM trades
        WHERE market_id = ? AND status IN ('FILLED', 'PENDING')
        LIMIT 1
        """,
        (market_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_trade_by_id(db_path: str, trade_id: int) -> Optional[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM trades WHERE id = ?",
        (trade_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_settled_unredeemed_trades(db_path: str, mode: Optional[str] = None) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = """
        SELECT * FROM trades
        WHERE status = 'CLOSED'
          AND resolution IN ('WIN', 'LOSS')
          AND redeemed_at IS NULL
    """
    params = []
    if mode is not None:
        query += " AND mode = ?"
        params.append(mode)
    query += " ORDER BY resolved_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def snapshot_portfolio(db_path: str, total_value: float, cash_balance: float, mode: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO portfolio_snapshots (total_value, cash_balance, mode) VALUES (?, ?, ?)",
        (total_value, cash_balance, mode),
    )
    conn.commit()
    conn.close()


def get_portfolio_snapshots(db_path: str, limit: int = 200, mode: str = "paper") -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM portfolio_snapshots
        WHERE mode = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (mode, limit),
    ).fetchall()
    conn.close()
    return list(reversed([dict(row) for row in rows]))


def get_performance_by_category(db_path: str, mode: str = "paper") -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            category,
            COUNT(*) AS total,
            SUM(CASE WHEN resolution = 'WIN' THEN 1 ELSE 0 END) AS wins,
            SUM(pnl) AS total_pnl,
            AVG(edge) AS avg_edge
        FROM trades
        WHERE status = 'CLOSED' AND resolution IS NOT NULL AND mode = ?
        GROUP BY category
        """,
        (mode,),
    ).fetchall()
    conn.close()
    result = {}
    for row in rows:
        item = dict(row)
        total = item["total"] or 0
        wins = item["wins"] or 0
        result[item["category"]] = {
            "total": total,
            "wins": wins,
            "win_rate": wins / total if total else 0.0,
            "total_pnl": item["total_pnl"] or 0.0,
            "avg_edge": item["avg_edge"] or 0.0,
        }
    return result


def get_daily_stats(db_path: str, mode: str = "paper") -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT COUNT(*) AS daily_trades, SUM(pnl) AS daily_pnl
        FROM trades
        WHERE date(created_at) = date('now') AND mode = ?
        """,
        (mode,),
    ).fetchone()
    conn.close()
    return dict(row)


def get_total_pnl(db_path: str, mode: str = "paper") -> float:
    conn = sqlite3.connect(db_path)
    value = conn.execute(
        "SELECT SUM(pnl) FROM trades WHERE mode = ?",
        (mode,),
    ).fetchone()[0]
    conn.close()
    return value or 0.0


def get_deployed_capital(db_path: str, mode: str = "paper") -> float:
    conn = sqlite3.connect(db_path)
    value = conn.execute(
        """
        SELECT SUM(size_usd) FROM trades
        WHERE status IN ('FILLED', 'PENDING') AND mode = ?
        """,
        (mode,),
    ).fetchone()[0]
    conn.close()
    return value or 0.0


def set_app_state(db_path: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO app_state (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value),
    )
    conn.commit()
    conn.close()


def get_app_state(db_path: str, key: str, default: str = None) -> Optional[str]:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


_SETTINGS_META = {
    "paper_starting_capital": float,
    "real_starting_capital": float,
    "max_position_size": float,
    "max_deployed_pct": float,
    "scan_interval_minutes": int,
    "min_market_volume": float,
    "min_discount": float,
    "stop_loss_pct": float,
    "max_expiry_days": int,
    "max_position_pct": float,
    "max_buy_price": float,
}


def _settings_defaults() -> dict:
    import config

    return {
        "paper_starting_capital": config.STARTING_CAPITAL,
        "real_starting_capital": config.STARTING_CAPITAL,
        "max_position_size": config.MAX_POSITION_SIZE,
        "max_deployed_pct": config.MAX_DEPLOYED_PCT,
        "scan_interval_minutes": config.SCAN_INTERVAL_MINUTES,
        "min_market_volume": config.MIN_MARKET_VOLUME,
        "min_discount": config.MIN_DISCOUNT,
        "stop_loss_pct": config.STOP_LOSS_PCT,
        "max_expiry_days": config.MAX_EXPIRY_DAYS,
        "max_position_pct": config.MAX_POSITION_PCT,
        "max_buy_price": config.MAX_BUY_PRICE,
    }


def get_settings(db_path: str) -> dict:
    result = _settings_defaults()
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT key, value FROM app_state WHERE key IN ({})".format(
            ",".join("?" * len(_SETTINGS_META))
        ),
        list(_SETTINGS_META.keys()),
    ).fetchall()
    conn.close()
    for key, value in rows:
        result[key] = _SETTINGS_META[key](value)
    return result


def save_settings(db_path: str, settings: dict) -> None:
    conn = sqlite3.connect(db_path)
    for key, value in settings.items():
        if key not in _SETTINGS_META:
            continue
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, str(value)),
        )
    conn.commit()
    conn.close()


def reset_paper_trading(db_path: str, starting_capital: float) -> None:
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
