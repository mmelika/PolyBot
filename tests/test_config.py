import pytest
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
        assert config.MIN_EDGE == 0.08
        assert config.KELLY_FRACTION == 0.25
        assert config.MAX_DEPLOYED_PCT == 0.80
        assert config.SCAN_INTERVAL_MINUTES == 10
        assert config.TRADING_MODE == "paper"


def test_config_loads_env_overrides():
    env = {
        "GEMINI_API_KEY": "test-key",
        "POLYMARKET_PRIVATE_KEY": "0xabc",
        "POLYMARKET_PROXY_ADDRESS": "0xdef",
        "STARTING_CAPITAL": "1000",
        "MAX_POSITION_SIZE": "50",
        "MIN_EDGE": "0.10",
        "TRADING_MODE": "real",
    }
    with patch.dict(os.environ, env, clear=True):
        import importlib
        import config
        importlib.reload(config)
        assert config.STARTING_CAPITAL == 1000
        assert config.MAX_POSITION_SIZE == 50
        assert config.MIN_EDGE == 0.10
        assert config.TRADING_MODE == "real"


def test_config_long_term_defaults():
    env = {
        "GEMINI_API_KEY": "test-key",
        "POLYMARKET_PRIVATE_KEY": "0xabc",
        "POLYMARKET_PROXY_ADDRESS": "0xdef",
    }
    with patch.dict(os.environ, env, clear=True):
        import importlib
        import config
        importlib.reload(config)
        assert config.LONG_TERM_DAYS == 7
        assert config.LONG_TERM_MIN_PROB == 0.80
