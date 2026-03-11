from datetime import datetime, timedelta, timezone
import json as _json
from typing import Optional

import requests

import config

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType

    CLOB_AVAILABLE = True
except ImportError:
    ClobClient = None
    OrderArgs = None
    OrderType = None
    CLOB_AVAILABLE = False

try:
    from web3 import Web3

    WEB3_AVAILABLE = True
except ImportError:
    Web3 = None
    WEB3_AVAILABLE = False

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
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return _json.loads(val)
        except (_json.JSONDecodeError, ValueError):
            return []
    return []


def _parse_end_date(end_date_str: str) -> Optional[datetime]:
    if not end_date_str:
        return None
    try:
        return datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def normalize_market(raw: dict) -> dict:
    outcomes = _parse_json_str(raw.get("outcomes", "[]"))
    prices = _parse_json_str(raw.get("outcomePrices", "[]"))
    token_ids = _parse_json_str(raw.get("clobTokenIds", "[]"))
    events = raw.get("events", []) or []
    event = events[0] if events and isinstance(events[0], dict) else {}
    category = (event.get("category") or raw.get("category") or "other").lower()
    slug = event.get("slug") or raw.get("slug") or raw.get("marketSlug") or raw.get("conditionId", "")

    yes_price = float(prices[0]) if len(prices) > 0 else 0.5
    no_price = float(prices[1]) if len(prices) > 1 else 0.5
    yes_token_id = token_ids[0] if len(token_ids) > 0 else ""
    no_token_id = token_ids[1] if len(token_ids) > 1 else ""

    return {
        "market_id": raw.get("conditionId", "") or raw.get("condition_id", ""),
        "condition_id": raw.get("conditionId", "") or raw.get("condition_id", ""),
        "question": raw.get("question", ""),
        "category": category,
        "volume": float(raw.get("volume", 0) or 0),
        "end_date_iso": raw.get("endDate", "") or raw.get("end_date_iso", ""),
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "token_ids": {"YES": yes_token_id, "NO": no_token_id},
        "active": raw.get("active", True),
        "closed": raw.get("closed", False),
        "event_slug": slug,
        "neg_risk": bool(raw.get("negRisk") or raw.get("neg_risk")),
        "outcomes": outcomes,
        "collateral_token": raw.get("collateralToken") or raw.get("collateral_token"),
    }


def get_active_markets(
    min_volume: float = None,
    min_hours_to_close: int = None,
    max_markets: int = None,
) -> list:
    min_volume = config.MIN_MARKET_VOLUME if min_volume is None else min_volume
    min_hours_to_close = 24 if min_hours_to_close is None else min_hours_to_close
    max_markets = config.MAX_MARKETS_TO_SCAN if max_markets is None else max_markets

    response = requests.get(
        f"{GAMMA_API}/markets",
        params={
            "closed": "false",
            "active": "true",
            "limit": 100,
            "order": "volume",
            "ascending": "false",
        },
        timeout=15,
    )
    response.raise_for_status()
    raw_markets = response.json()

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=min_hours_to_close)
    results = []
    for raw in raw_markets:
        market = normalize_market(raw)
        if market["closed"] or not market["active"] or market["volume"] < min_volume:
            continue
        end_date = _parse_end_date(market["end_date_iso"])
        if end_date is None or end_date < cutoff:
            continue
        results.append(market)
        if len(results) >= max_markets:
            break
    return results


def _best_ask_price(book) -> Optional[float]:
    asks = getattr(book, "asks", None) or []
    if not asks:
        return None
    return float(asks[0].price)


def _book_levels(book):
    for level in getattr(book, "asks", None) or []:
        try:
            price = float(level.price)
            size = float(level.size)
        except (AttributeError, TypeError, ValueError):
            continue
        yield price, size


def get_market_price(token_id: str) -> Optional[float]:
    client = _get_client()
    try:
        book = client.get_order_book(token_id)
    except Exception:
        return None
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if bids and asks:
        return (float(bids[0].price) + float(asks[0].price)) / 2
    if bids:
        return float(bids[0].price)
    if asks:
        return float(asks[0].price)
    return None


def get_midpoint_price(token_id: str) -> Optional[float]:
    return get_market_price(token_id)


def find_expiry_candidates(
    max_days: int,
    min_volume: float,
    min_discount: float,
    excluded_market_ids: Optional[set] = None,
) -> list:
    excluded_market_ids = excluded_market_ids or set()
    response = requests.get(
        f"{GAMMA_API}/markets",
        params={
            "closed": "false",
            "active": "true",
            "limit": 100,
            "order": "volume",
            "ascending": "false",
        },
        timeout=15,
    )
    response.raise_for_status()
    raw_markets = response.json()
    client = _get_client()
    now = datetime.now(timezone.utc)
    max_end = now + timedelta(days=max_days)
    candidates = []

    for raw in raw_markets:
        market = normalize_market(raw)
        if market["market_id"] in excluded_market_ids:
            continue
        if market["closed"] or not market["active"]:
            continue
        if market["volume"] < min_volume or market["category"] == "sports":
            continue
        if not market["yes_token_id"] or not market["no_token_id"]:
            continue

        end_date = _parse_end_date(market["end_date_iso"])
        if end_date is None or end_date > max_end or end_date < now:
            continue

        try:
            yes_book = client.get_order_book(market["yes_token_id"])
            no_book = client.get_order_book(market["no_token_id"])
        except Exception:
            continue

        yes_ask = _best_ask_price(yes_book)
        no_ask = _best_ask_price(no_book)
        available = {
            outcome: price
            for outcome, price in (("YES", yes_ask), ("NO", no_ask))
            if price is not None
        }
        if not available:
            continue

        best_outcome, best_ask = min(available.items(), key=lambda item: item[1])
        discount = 1.0 - best_ask
        if discount < min_discount:
            continue

        candidates.append(
            {
                **market,
                "yes_ask_price": yes_ask,
                "no_ask_price": no_ask,
                "real_ask": best_ask,
                "discount": discount,
                "best_outcome": best_outcome,
            }
        )

    candidates.sort(key=lambda item: item["discount"], reverse=True)
    return candidates


def walk_order_book(token_id: str, notional: float) -> tuple[Optional[float], Optional[float]]:
    client = _get_client()
    book = client.get_order_book(token_id)
    remaining = float(notional)
    total_cost = 0.0
    total_shares = 0.0
    limit_price = None

    for price, size in _book_levels(book):
        level_cost = price * size
        cost_taken = min(remaining, level_cost)
        shares_taken = cost_taken / price if price else 0.0
        total_cost += cost_taken
        total_shares += shares_taken
        remaining -= cost_taken
        limit_price = price
        if remaining <= 1e-9:
            break

    if total_shares <= 0 or limit_price is None:
        return None, None
    if remaining > 1e-9:
        return None, None
    return total_cost / total_shares, limit_price


def place_order(token_id: str, side: str, price: float, size_usd: float) -> Optional[str]:
    client = _get_client()
    try:
        from py_clob_client.clob_types import BUY, SELL

        order_args = OrderArgs(
            price=round(price, 4),
            size=round(size_usd / price, 4),
            side=BUY if side == "BUY" else SELL,
            token_id=token_id,
        )
        signed_order = client.create_order(order_args)
        response = client.post_order(signed_order, OrderType.GTC)
        return response.get("orderID") if isinstance(response, dict) else str(response)
    except Exception:
        return None


def walk_and_place_order(
    token_id: str,
    size_usd: float,
    max_buy_price: float = None,
) -> Optional[dict]:
    max_buy_price = config.MAX_BUY_PRICE if max_buy_price is None else max_buy_price
    estimated_avg_price, limit_price = walk_order_book(token_id, size_usd)
    if estimated_avg_price is None or limit_price is None:
        return None
    if estimated_avg_price >= max_buy_price:
        return None

    order_id = place_order(token_id=token_id, side="BUY", price=limit_price, size_usd=size_usd)
    return {
        "order_id": order_id,
        "estimated_avg_price": estimated_avg_price,
        "limit_price": limit_price,
        "actual_avg_price": estimated_avg_price,
    }


def calculate_pnl(side: str, outcome: str, entry_price: float, current_price: float, size_usd: float) -> float:
    if entry_price <= 0:
        return 0.0
    return ((current_price - entry_price) / entry_price) * size_usd


def auto_redeem(trade: dict) -> bool:
    if not WEB3_AVAILABLE or not config.POLYGON_RPC_URL:
        return False
    if trade.get("mode") != "real":
        return False
    if not trade.get("condition_id") and not trade.get("market_id"):
        return False
    return True
