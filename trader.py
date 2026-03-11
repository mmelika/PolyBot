import logging
import threading
import time
from typing import Optional

import config
import database
import gemini_agent
from hooks import session_start
import polymarket_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("trader")

_trader_thread: Optional[threading.Thread] = None
_running = False
_status = "STOPPED"


def get_status() -> str:
    return _status

def get_skip_reason(candidate: dict, verification: dict, settings: dict, portfolio_value: float, open_trades: list) -> Optional[str]:
    if not verification:
        return "verification failed"
    if not verification.get("verified"):
        return "outcome not verified"
    if verification.get("confidence", 0.0) < 0.85:
        return f"verification confidence too low ({verification.get('confidence', 0.0):.0%})"

    outcome = verification.get("outcome")
    ask_price = candidate.get(f"{outcome.lower()}_ask_price")
    if ask_price is None:
        return f"no {outcome} liquidity"

    discount = 1.0 - ask_price
    if discount < settings["min_discount"]:
        return f"discount below threshold ({discount:.1%})"

    if ask_price >= settings["max_buy_price"]:
        return f"buy price too high ({ask_price:.3f})"

    event_slug = candidate.get("event_slug")
    event_exposure = sum(t["size_usd"] for t in open_trades if t.get("event_slug") == event_slug)
    if event_exposure + settings["max_position_size"] > portfolio_value * settings["max_position_pct"]:
        return "event concentration limit reached"

    return None


def should_trade(candidate: dict, verification: dict, settings: dict, portfolio_value: float, open_trades: list) -> bool:
    return get_skip_reason(candidate, verification, settings, portfolio_value, open_trades) is None


def is_market_already_open(db_path: str, market_id: str) -> bool:
    return database.get_trade_by_market_id(db_path, market_id) is not None


def _position_size(settings: dict, deployable_cash: float, portfolio_value: float, open_trades: list, candidate: dict) -> float:
    size = min(settings["max_position_size"], deployable_cash)
    event_exposure = sum(
        trade["size_usd"]
        for trade in open_trades
        if trade.get("event_slug") == candidate.get("event_slug")
    )
    event_remaining = max(0.0, portfolio_value * settings["max_position_pct"] - event_exposure)
    return min(size, event_remaining)


def execute_trade(
    db_path: str,
    candidate: dict,
    verification: dict,
    size_usd: float,
    mode: str,
    settings: dict,
) -> Optional[int]:
    outcome = verification["outcome"]
    token_id = candidate["token_ids"][outcome]

    if mode == "real":
        execution = polymarket_client.walk_and_place_order(
            token_id=token_id,
            size_usd=size_usd,
            max_buy_price=settings["max_buy_price"],
        )
    else:
        estimated_avg_price, limit_price = polymarket_client.walk_order_book(token_id, size_usd)
        if estimated_avg_price is None or limit_price is None or estimated_avg_price >= settings["max_buy_price"]:
            return None
        execution = {
            "order_id": None,
            "estimated_avg_price": estimated_avg_price,
            "limit_price": limit_price,
            "actual_avg_price": estimated_avg_price,
        }

    if not execution:
        return None

    fill_price = execution["actual_avg_price"]
    trade_id = database.insert_trade(
        db_path,
        {
            "market_id": candidate["market_id"],
            "question": candidate["question"],
            "category": candidate.get("category", "other"),
            "outcome": outcome,
            "side": "BUY",
            "size_usd": size_usd,
            "entry_price": fill_price,
            "current_price": fill_price,
            "pnl": 0.0,
            "status": "FILLED",
            "mode": mode,
            "gemini_probability": verification["confidence"],
            "gemini_reasoning": verification["reasoning"],
            "edge": 1.0 - fill_price,
            "closes_at": candidate.get("end_date_iso", "")[:10],
            "research_brief": verification["reasoning"],
            "stop_loss_price": fill_price * (1.0 - settings["stop_loss_pct"]),
            "strategy": "expiry_convergence",
            "token_id": token_id,
            "event_slug": candidate.get("event_slug"),
        },
    )
    log.info(
        "[trader] %s trade: %s | %s @ %.3f | discount=%.1f%% | $%.2f",
        "Paper" if mode == "paper" else "Real",
        candidate["question"][:60],
        outcome,
        fill_price,
        (1.0 - fill_price) * 100,
        size_usd,
    )
    return trade_id


def check_stop_losses(db_path: str, mode: str, settings: Optional[dict] = None) -> int:
    settings = settings or database.get_settings(db_path)
    open_trades = database.get_open_trades(db_path, mode)
    stopped_out = 0
    for trade in open_trades:
        token_id = trade.get("token_id")
        if not token_id:
            continue
        current_price = polymarket_client.get_market_price(token_id)
        if current_price is None:
            continue
        pnl = polymarket_client.calculate_pnl(
            side=trade["side"],
            outcome=trade["outcome"],
            entry_price=trade["entry_price"],
            current_price=current_price,
            size_usd=trade["size_usd"],
        )
        database.update_trade_price(db_path, trade["id"], current_price, pnl)
        stop_loss = trade.get("stop_loss_price")
        if stop_loss is not None and current_price <= stop_loss:
            database.close_trade(db_path, trade["id"], "STOPPED_OUT", current_price)
            stopped_out += 1
    return stopped_out


def scan_and_trade(db_path: str) -> int:
    global _status
    _status = "SCANNING"

    mode = database.get_app_state(db_path, "trading_mode", config.TRADING_MODE)
    settings = database.get_settings(db_path)
    session_start.run(db_path, mode, settings)

    starting_capital = (
        settings["paper_starting_capital"]
        if mode == "paper"
        else settings["real_starting_capital"]
    )
    snapshots = database.get_portfolio_snapshots(db_path, limit=1, mode=mode)
    total_value = snapshots[-1]["total_value"] if snapshots else starting_capital
    deployed = database.get_deployed_capital(db_path, mode)
    deployable_cash = total_value * settings["max_deployed_pct"] - deployed
    if deployable_cash <= 0:
        _status = "RUNNING"
        return 0

    open_trades = database.get_open_trades(db_path, mode)
    excluded_market_ids = {trade["market_id"] for trade in open_trades}
    try:
        candidates = polymarket_client.find_expiry_candidates(
            max_days=settings["max_expiry_days"],
            min_volume=settings["min_market_volume"],
            min_discount=settings["min_discount"],
            excluded_market_ids=excluded_market_ids,
        )
    except Exception as exc:
        log.error("[trader] Expiry scan error: %s", exc)
        _status = "RUNNING"
        return 0

    trades_placed = 0
    for candidate in candidates:
        verification = gemini_agent.verify_outcome(candidate["question"], candidate)
        skip_reason = get_skip_reason(candidate, verification, settings, total_value, open_trades)
        if skip_reason is not None:
            database.insert_skipped_market(
                db_path,
                {
                    "market_id": candidate["market_id"],
                    "question": candidate["question"],
                    "category": candidate.get("category", "other"),
                    "side": (verification or {}).get("outcome", candidate.get("best_outcome", "YES")),
                    "probability": (verification or {}).get("confidence"),
                    "edge": candidate.get("discount"),
                    "confidence": f"{((verification or {}).get('confidence', 0.0) * 100):.0f}%",
                    "contested": 0,
                    "skip_reason": skip_reason,
                    "reasoning": (verification or {}).get("reasoning", ""),
                    "mode": mode,
                },
            )
            continue

        size_usd = _position_size(settings, deployable_cash, total_value, open_trades, candidate)
        if size_usd < 1.0:
            database.insert_skipped_market(
                db_path,
                {
                    "market_id": candidate["market_id"],
                    "question": candidate["question"],
                    "category": candidate.get("category", "other"),
                    "side": verification["outcome"],
                    "probability": verification["confidence"],
                    "edge": candidate["discount"],
                    "confidence": f"{verification['confidence'] * 100:.0f}%",
                    "contested": 0,
                    "skip_reason": "position size too small",
                    "reasoning": verification["reasoning"],
                    "mode": mode,
                },
            )
            continue

        trade_id = execute_trade(db_path, candidate, verification, size_usd, mode, settings)
        if not trade_id:
            database.insert_skipped_market(
                db_path,
                {
                    "market_id": candidate["market_id"],
                    "question": candidate["question"],
                    "category": candidate.get("category", "other"),
                    "side": verification["outcome"],
                    "probability": verification["confidence"],
                    "edge": candidate["discount"],
                    "confidence": f"{verification['confidence'] * 100:.0f}%",
                    "contested": 0,
                    "skip_reason": "order rejected by liquidity or max price",
                    "reasoning": verification["reasoning"],
                    "mode": mode,
                },
            )
            continue

        trades_placed += 1
        deployable_cash -= size_usd
        open_trades = database.get_open_trades(db_path, mode)
        deployed = database.get_deployed_capital(db_path, mode)
        database.snapshot_portfolio(
            db_path,
            total_value=starting_capital + database.get_total_pnl(db_path, mode),
            cash_balance=total_value - deployed,
            mode=mode,
        )
        if deployable_cash <= 0:
            break

    _status = "RUNNING"
    return trades_placed


def _trading_loop(db_path: str) -> None:
    global _running, _status
    _status = "RUNNING"
    while _running:
        try:
            scan_and_trade(db_path)
        except Exception as exc:
            log.error("[trader] Loop error: %s", exc)
        settings = database.get_settings(db_path)
        interval_seconds = int(settings["scan_interval_minutes"]) * 60
        for _ in range(interval_seconds):
            if not _running:
                break
            time.sleep(1)
    _status = "STOPPED"


def start(db_path: str) -> None:
    global _trader_thread, _running
    if _running:
        return
    _running = True
    _trader_thread = threading.Thread(target=_trading_loop, args=(db_path,), daemon=True)
    _trader_thread.start()


def stop() -> None:
    global _running
    _running = False
