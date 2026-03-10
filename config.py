import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
POLYMARKET_HOST = "https://clob.polymarket.com"
POLYMARKET_CHAIN_ID = 137

STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "5000"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "20"))
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.08"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
MAX_DEPLOYED_PCT = float(os.getenv("MAX_DEPLOYED_PCT", "0.80"))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "10"))
TRADING_MODE = os.getenv("TRADING_MODE", "paper")  # "paper" or "real"

MIN_MARKET_VOLUME = 1000  # USD
MIN_HOURS_TO_CLOSE = 24
MAX_MARKETS_TO_SCAN = 50
DB_PATH = "data/polybot.db"

LONG_TERM_DAYS = 7          # markets closing beyond this require high probability
LONG_TERM_MIN_PROB = 0.80   # minimum probability to trade a long-term market
