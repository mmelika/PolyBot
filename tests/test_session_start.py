from unittest.mock import patch

import database
from hooks import session_start


def test_auto_redeem_marks_trade_redeemed(tmp_path):
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
            "current_price": 1.0,
            "status": "CLOSED",
            "mode": "real",
        },
    )
    database.close_trade(db_path, trade_id, "WIN", 1.0)
    with patch("hooks.session_start.polymarket_client.auto_redeem", return_value=True):
        count = session_start.auto_redeem_settled_trades(db_path, "real")
    assert count == 1
    assert database.get_settled_unredeemed_trades(db_path, mode="real") == []
