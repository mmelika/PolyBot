# tests/test_app_helpers.py
import pytest


def test_max_profit_basic():
    """Buying $10 at 0.50 → potential profit = $10."""
    from app import max_profit
    assert max_profit(10.0, 0.50) == pytest.approx(10.0)


def test_max_profit_low_price():
    """Buying $10 at 0.20 → potential profit = $40."""
    from app import max_profit
    assert max_profit(10.0, 0.20) == pytest.approx(40.0)


def test_max_profit_high_price():
    """Buying $10 at 0.80 → potential profit = $2.50."""
    from app import max_profit
    assert max_profit(10.0, 0.80) == pytest.approx(2.50)


def test_render_open_positions_has_max_profit_column():
    """Open positions table header includes MAX PROFIT."""
    from app import render_open_positions
    result = render_open_positions([{
        "id": 1,
        "question": "Will X happen?",
        "outcome": "YES",
        "gemini_probability": 0.70,
        "size_usd": 10.0,
        "entry_price": 0.40,
        "current_price": 0.45,
        "pnl": 1.25,
        "closes_at": "2026-06-01",
    }])
    assert "MAX PROFIT" in str(result)


def test_render_open_positions_max_profit_value():
    """MAX PROFIT cell shows correct computed value for a $10 trade at 0.40."""
    from app import render_open_positions, max_profit
    trade = {
        "id": 1,
        "question": "Will X happen?",
        "outcome": "YES",
        "gemini_probability": 0.70,
        "size_usd": 10.0,
        "entry_price": 0.40,
        "current_price": 0.45,
        "pnl": 1.25,
        "closes_at": "2026-06-01",
    }
    result = render_open_positions([trade])
    assert "+$15.00" in str(result)


def test_render_open_positions_has_buy_more_button():
    """Each open position row has a + buy-more button."""
    from app import render_open_positions
    result = render_open_positions([{
        "id": 42,
        "question": "Will X happen?",
        "outcome": "YES",
        "gemini_probability": 0.70,
        "size_usd": 10.0,
        "entry_price": 0.40,
        "current_price": 0.45,
        "pnl": 1.25,
        "closes_at": "2026-06-01",
    }])
    result_str = str(result)
    assert "buy-more-btn" in result_str
    assert "42" in result_str
