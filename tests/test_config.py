import os
from unittest.mock import patch


def test_config_loads_defaults():
    env = {
        "GEMINI_API_KEY": "test-key",
        "POLYMARKET_PRIVATE_KEY": "0xabc",
        "POLYMARKET_PROXY_ADDRESS": "0xdef",
    }
    with patch.dict(os.environ, env, clear=True):
        import importlib
        import config

        importlib.reload(config)
        assert config.STARTING_CAPITAL == 500
        assert config.MAX_POSITION_SIZE == 20
        assert config.MIN_DISCOUNT == 0.02
        assert config.STOP_LOSS_PCT == 0.20
        assert config.MAX_EXPIRY_DAYS == 3
        assert config.MAX_BUY_PRICE == 0.99
        assert config.MAX_POSITION_PCT == 0.20
        assert config.TRADING_MODE == "paper"


def test_config_loads_env_overrides():
    env = {
        "GEMINI_API_KEY": "test-key",
        "POLYMARKET_PRIVATE_KEY": "0xabc",
        "POLYMARKET_PROXY_ADDRESS": "0xdef",
        "STARTING_CAPITAL": "1000",
        "MAX_POSITION_SIZE": "50",
        "MIN_DISCOUNT": "0.05",
        "STOP_LOSS_PCT": "0.15",
        "TRADING_MODE": "real",
    }
    with patch.dict(os.environ, env, clear=True):
        import importlib
        import config

        importlib.reload(config)
        assert config.STARTING_CAPITAL == 1000
        assert config.MAX_POSITION_SIZE == 50
        assert config.MIN_DISCOUNT == 0.05
        assert config.STOP_LOSS_PCT == 0.15
        assert config.TRADING_MODE == "real"
