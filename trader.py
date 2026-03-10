import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional
import config
import database
import gemini_agent
import polymarket_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("trader")

_trader_thread: Optional[threading.Thread] = None
_running = False
_status = "STOPPED"


def get_status() -> str:
    return _status


def should_trade(analysis: dict, market: dict, min_edge: float = config.MIN_EDGE) -> bool:
    if analysis.get("confidence") == "low":
        return False
    if analysis.get("edge", 0) < min_edge:
        return False
    end_date_str = market.get("end_date_iso", "")
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            days_to_close = (end_date - datetime.now(timezone.utc)).total_seconds() / 86400
            if days_to_close > config.LONG_TERM_DAYS:
                if analysis.get("probability", 0) < config.LONG_TERM_MIN_PROB:
                    return False
        except (ValueError, AttributeError):
            pass  # unparseable date — skip long-term check
    if analysis.get("contested"):
        if analysis.get("confidence") != "high" or analysis.get("edge", 0) < 0.15:
            return False
    return True


def is_market_already_open(db_path: str, market_id: str) -> bool:
    return database.get_trade_by_market_id(db_path, market_id) is not None


def execute_trade(
    db_path: str,
    market: dict,
    analysis: dict,
    size_usd: float,
    mode: str,
    research_brief: Optional[str] = None,
) -> Optional[int]:
    outcome = analysis["side"]

    if mode == "real":
        order_id = polymarket_client.place_order(
            token_id=analysis["token_id"],
            side="BUY",
            price=analysis["entry_price"],
            size_usd=size_usd,
        )
        if not order_id:
            log.warning("[trader] Real order failed for %s", market['question'][:50])
            return None

    trade = {
        "market_id": market["market_id"],
        "question": market["question"],
        "category": market.get("category", "other"),
        "outcome": outcome,
        "side": "BUY",
        "size_usd": size_usd,
        "entry_price": analysis["entry_price"],
        "current_price": analysis["entry_price"],
        "pnl": 0.0,
        "status": "FILLED",
        "mode": mode,
        "gemini_probability": analysis["probability"],
        "gemini_reasoning": analysis["reasoning"],
        "edge": analysis["edge"],
        "closes_at": market.get("end_date_iso", "")[:10],
        "research_brief": research_brief,
    }
    trade_id = database.insert_trade(db_path, trade)
    log.info("[trader] %s trade: %s | %s @ %.3f | edge=%.1f%% | $%.2f",
             'Paper' if mode == 'paper' else 'Real', market['question'][:50],
             outcome, analysis['entry_price'], analysis['edge'] * 100, size_usd)
    return trade_id


def scan_and_trade(db_path: str) -> int:
    import json as _json
    global _status
    _status = "SCANNING"

    mode = database.get_app_state(db_path, "trading_mode", config.TRADING_MODE)
    performance = database.get_performance_by_category(db_path)
    deployed = database.get_deployed_capital(db_path)

    snapshots = database.get_portfolio_snapshots(db_path, limit=1)
    total_value = snapshots[-1]["total_value"] if snapshots else config.STARTING_CAPITAL
    max_deployable = total_value * config.MAX_DEPLOYED_PCT - deployed

    if max_deployable <= 0:
        _status = "RUNNING"
        return 0

    try:
        markets = polymarket_client.get_active_markets()
    except Exception as e:
        log.error("[trader] Failed to fetch markets: %s", e)
        _status = "RUNNING"
        return 0

    log.info("[trader] Fetched %d markets, mode=%s, deployable=$%.2f",
             len(markets), mode, max_deployable)

    # Phase 1: Screen
    open_ids = {t["market_id"] for t in database.get_open_trades(db_path)}
    try:
        flagged = gemini_agent.screen_markets(markets, open_ids)
    except Exception as e:
        log.error("[trader] Screener error: %s", e)
        _status = "RUNNING"
        return 0

    flagged = flagged[:config.MAX_FLAGGED_MARKETS]
    log.info("[trader] Screener flagged %d markets for research", len(flagged))

    market_by_id = {m["market_id"]: m for m in markets}
    trades_placed = 0

    for flag in flagged:
        market = market_by_id.get(flag["market_id"])
        if not market:
            continue

        # Phase 2: Research brief
        try:
            research = gemini_agent.research_market(market)
        except Exception as e:
            log.error("[trader] Research error for %s: %s", market["question"][:40], e)
            continue
        if not research:
            log.info("[trader] No research for: %s", market["question"][:50])
            continue

        # Phase 3: Probability from research
        try:
            analysis = gemini_agent.assign_probability(market, research, performance)
        except Exception as e:
            log.error("[trader] Probability error for %s: %s", market["question"][:40], e)
            continue
        if not analysis:
            log.info("[trader] No probability for: %s", market["question"][:50])
            continue

        log.info("[trader] %s | prob=%.2f edge=%.1f%% conf=%s contested=%s",
                 market["question"][:40], analysis["probability"],
                 analysis["edge"] * 100, analysis["confidence"],
                 analysis.get("contested", False))

        if not should_trade(analysis, market):
            continue

        size_usd = gemini_agent.calculate_position_size(
            probability=analysis["probability"],
            entry_price=analysis["entry_price"],
            portfolio_value=total_value,
        )
        if size_usd < 1.0:
            continue

        research_brief_json = _json.dumps(research)
        trade_id = execute_trade(db_path, market, analysis, size_usd, mode, research_brief_json)
        if trade_id:
            trades_placed += 1
            new_deployed = database.get_deployed_capital(db_path)
            database.snapshot_portfolio(
                db_path,
                total_value=config.STARTING_CAPITAL + database.get_total_pnl(db_path),
                cash_balance=total_value - new_deployed,
                mode=mode,
            )

    _status = "RUNNING"
    return trades_placed


def _trading_loop(db_path: str) -> None:
    global _running, _status
    _status = "RUNNING"
    while _running:
        try:
            scan_and_trade(db_path)
        except Exception as e:
            log.error("[trader] Loop error: %s", e)
        for _ in range(config.SCAN_INTERVAL_MINUTES * 60):
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
