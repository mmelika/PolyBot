from datetime import datetime, timezone, timedelta
import json as _json
from typing import Optional
import requests
import config

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.constants import POLYGON
    CLOB_AVAILABLE = True
except ImportError:
    class ClobClient:
        pass
    OrderArgs = None
    OrderType = None
    POLYGON = None
    CLOB_AVAILABLE = False

GAMMA_API = "https://gamma-api.polymarket.com"


def _get_client():
    if not CLOB_AVAILABLE:
        raise RuntimeError("py_clob_client not installed")
    return ClobClient(
        host=config.POLYMARKET_HOST,
        key=config.POLYMARKET_PRIVATE_KEY,
        chain_id=config.POLYMARKET_CHAIN_ID,
        signature_type=2,
        funder=config.POLYMARKET_PROXY_ADDRESS,
    )


def _parse_json_str(val):
    """Parse a JSON string field from Gamma API (outcomes, prices, tokenIds)."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return _json.loads(val)
        except (_json.JSONDecodeError, ValueError):
            return []
    return []


def normalize_market(raw: dict) -> dict:
    """Convert raw Gamma API market dict to our standard format.

    Handles the Gamma API field names: conditionId, endDate,
    outcomes (JSON string), outcomePrices (JSON string),
    clobTokenIds (JSON string).
    """
    outcomes = _parse_json_str(raw.get("outcomes", "[]"))
    prices = _parse_json_str(raw.get("outcomePrices", "[]"))
    token_ids = _parse_json_str(raw.get("clobTokenIds", "[]"))

    # Map outcome index 0 → "yes", index 1 → "no" for binary markets
    yes_price = float(prices[0]) if len(prices) > 0 else 0.5
    no_price = float(prices[1]) if len(prices) > 1 else 0.5
    yes_token_id = token_ids[0] if len(token_ids) > 0 else ""
    no_token_id = token_ids[1] if len(token_ids) > 1 else ""

    # Detect category from events if available
    events = raw.get("events", [])
    category = "other"
    if events and isinstance(events, list) and isinstance(events[0], dict):
        category = events[0].get("category", "other") or "other"

    return {
        "market_id": raw.get("conditionId", "") or raw.get("condition_id", ""),
        "condition_id": raw.get("conditionId", "") or raw.get("condition_id", ""),
        "question": raw.get("question", ""),
        "category": category,
        "volume": float(raw.get("volume", 0)),
        "end_date_iso": raw.get("endDate", "") or raw.get("end_date_iso", ""),
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "active": raw.get("active", True),
        "closed": raw.get("closed", False),
    }


def get_active_markets(
    min_volume: float = None,
    min_hours_to_close: int = None,
    max_markets: int = None,
) -> list:
    """Fetch and filter active markets from Polymarket's public API."""
    if min_volume is None:
        min_volume = config.MIN_MARKET_VOLUME
    if min_hours_to_close is None:
        min_hours_to_close = config.MIN_HOURS_TO_CLOSE
    if max_markets is None:
        max_markets = config.MAX_MARKETS_TO_SCAN

    # Use the free public Gamma API — no auth needed
    resp = requests.get(f"{GAMMA_API}/markets", params={
        "closed": "false",
        "active": "true",
        "limit": 100,
        "order": "volume",
        "ascending": "false",
    }, timeout=15)
    resp.raise_for_status()
    raw_markets = resp.json()

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=min_hours_to_close)
    results = []

    for raw in raw_markets:
        if raw.get("closed") or not raw.get("active"):
            continue
        try:
            volume = float(raw.get("volume", 0))
        except (ValueError, TypeError):
            continue
        if volume < min_volume:
            continue
        # Gamma API uses "endDate" not "end_date_iso"
        end_date_str = raw.get("endDate", "") or raw.get("end_date_iso", "")
        if not end_date_str:
            continue
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if end_date < cutoff:
            continue
        results.append(normalize_market(raw))
        if len(results) >= max_markets:
            break

    return results


def get_market_price(token_id: str) -> Optional[float]:
    """Get the current midpoint price for a token."""
    client = _get_client()
    try:
        book = client.get_order_book(token_id)
        bids = book.bids or []
        asks = book.asks or []
        if bids and asks:
            best_bid = float(bids[0].price)
            best_ask = float(asks[0].price)
            return (best_bid + best_ask) / 2
        elif bids:
            return float(bids[0].price)
        elif asks:
            return float(asks[0].price)
    except Exception:
        pass
    return None


def place_order(token_id: str, side: str, price: float, size_usd: float) -> Optional[str]:
    """Place a real limit order. Returns order ID or None on failure."""
    client = _get_client()
    try:
        from py_clob_client.clob_types import BUY, SELL
        order_args = OrderArgs(
            price=round(price, 4),
            size=round(size_usd / price, 2),
            side=BUY if side == "BUY" else SELL,
            token_id=token_id,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        return resp.get("orderID") if isinstance(resp, dict) else str(resp)
    except Exception as e:
        print(f"[polymarket_client] place_order error: {e}")
        return None


def calculate_pnl(side: str, outcome: str, entry_price: float, current_price: float, size_usd: float) -> float:
    """Calculate unrealized P&L for a position."""
    return ((current_price - entry_price) / entry_price) * size_usd
