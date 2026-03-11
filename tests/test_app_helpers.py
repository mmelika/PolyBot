import pytest


def test_max_profit_basic():
    from app import max_profit

    assert max_profit(10.0, 0.50) == pytest.approx(10.0)


def test_render_open_positions_has_stop_loss_column():
    from app import render_open_positions

    result = render_open_positions(
        [
            {
                "id": 1,
                "question": "Will X happen?",
                "outcome": "YES",
                "gemini_probability": 0.90,
                "size_usd": 10.0,
                "entry_price": 0.95,
                "current_price": 0.96,
                "stop_loss_price": 0.76,
                "pnl": 0.11,
                "closes_at": "2026-03-12",
            }
        ]
    )
    rendered = str(result)
    assert "STOP LOSS" in rendered
    assert "0.7600" in rendered


def test_render_open_positions_has_buy_more_button():
    from app import render_open_positions

    result = render_open_positions(
        [
            {
                "id": 42,
                "question": "Will X happen?",
                "outcome": "YES",
                "gemini_probability": 0.90,
                "size_usd": 10.0,
                "entry_price": 0.95,
                "current_price": 0.96,
                "stop_loss_price": 0.76,
                "pnl": 0.11,
                "closes_at": "2026-03-12",
            }
        ]
    )
    rendered = str(result)
    assert "buy-more-btn" in rendered
    assert "42" in rendered
