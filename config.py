import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "")

STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "500"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "20"))
MAX_DEPLOYED_PCT = float(os.getenv("MAX_DEPLOYED_PCT", "0.80"))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "10"))
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

MIN_MARKET_VOLUME = float(os.getenv("MIN_MARKET_VOLUME", "1000"))
MIN_DISCOUNT = float(os.getenv("MIN_DISCOUNT", "0.02"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.20"))
MAX_EXPIRY_DAYS = int(os.getenv("MAX_EXPIRY_DAYS", "3"))
MAX_BUY_PRICE = float(os.getenv("MAX_BUY_PRICE", "0.99"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.20"))
MAX_MARKETS_TO_SCAN = int(os.getenv("MAX_MARKETS_TO_SCAN", "50"))
ORDER_FILL_TIMEOUT_SECONDS = int(os.getenv("ORDER_FILL_TIMEOUT_SECONDS", "60"))

DB_PATH = os.getenv("DB_PATH", "data/polybot.db")
