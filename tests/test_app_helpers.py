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
