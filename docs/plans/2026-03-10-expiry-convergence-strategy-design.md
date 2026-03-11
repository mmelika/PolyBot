# Expiry Convergence Strategy — Design

**Date:** 2026-03-10
**Status:** Approved
**Source:** Deep Research Report on Profitable Polymarket Trading Bots Built With Claude Code (LainNet-42 analysis)

## Overview

Replace the existing Gemini-based three-phase probability analysis with an **expiry convergence** ("high-probability bond scalping") strategy. This is the only Polymarket trading strategy with publicly verifiable on-chain profitability evidence (18/19 wins, 94.7% win rate, withdrawal verified on-chain per LainNet-42).

**Core insight:** For near-certain markets expiring soon, the CLOB ask price IS the consensus probability. We don't need AI to estimate probability — we need AI to *verify* the outcome is truly locked in, then buy the discount.

**Profit model:**
```
Gross profit per share = 1 - real_ask_price
Net profit = Gross - (gas_cost / shares_purchased)
```

## Architecture

```
[Scan Cycle Start]
      |
      v
SessionStart Hook
  - Auto-redeem settled positions (Web3 CTF contract calls)
  - Check stop-losses on open positions → auto-sell if triggered
      |
      v
Phase 1: CLOB Scan  (polymarket_client.find_expiry_candidates)
  - Gamma API: open binary markets, volume >= 1000, ends <= 3 days, no sports
  - For each: fetch CLOB order book, compute real_ask (best ask for YES token)
  - Filter: real_ask < 0.98 (>= 2% discount)
  - Sort by discount descending
      |
      v
Phase 2: Verification  (gemini_agent.verify_outcome)
  - Single Gemini call with Google Search
  - "Has [question] already been determined? Is there any uncertainty?"
  - Returns: {verified, outcome, confidence, reasoning}
  - Only proceed if verified=True AND confidence >= 0.85
      |
      v
Phase 3: Execute  (polymarket_client.walk_and_place_order)
  - Walk order book levels to estimate average fill price
  - Reject if estimated_avg_price >= 0.99
  - Check position concentration: same event slug <= 20% portfolio
  - Place GTC limit order at limit_price (worst fill level)
  - Poll CLOB trades endpoint until filled or 60s timeout
  - Record stop_loss_price = actual_avg_price * 0.80
      |
      v
Log to DB, update portfolio snapshot
```

## Component Changes

### `trader.py` (major rewrite)
- Replace `screen_markets()`, `research_market()`, `assign_probability()` with `find_expiry_candidates()` orchestrator
- New `check_stop_losses()` function called at each scan start
- New `scan_and_trade()` uses 2-phase pipeline: CLOB scan → verify → execute
- Keep existing daemon thread scheduling

### `polymarket_client.py` (additions)
- `find_expiry_candidates(max_days, min_volume, min_discount)` → list of candidates with real_ask
- `walk_order_book(token_id, notional)` → `(estimated_avg_price, limit_price)`
- `auto_redeem(trade)` → calls Web3 CTF contract (normal + neg-risk paths)
- Keep existing `get_markets()`, `place_order()`, `get_midpoint_price()`

### `gemini_agent.py` (simplification)
- Replace three system prompts / three API calls with single `verify_outcome(question, market_data)` call
- Returns: `{verified: bool, outcome: str, confidence: float, reasoning: str}`
- Keep Google Search tool enabled

### `database.py` (additions)
- Add `stop_loss_price REAL` to trades table
- Add `strategy TEXT DEFAULT 'expiry_convergence'` to trades table
- Add `STOPPED_OUT` as valid resolution value
- New query: `get_settled_unredeemed_trades()`

### `hooks/session_start.py` (new file)
- Auto-redeem logic: query settled trades, detect resolution, call CTF contracts
- Stop-loss logic: check open positions, trigger auto-sell

### `config/contracts.py` (new file)
- CTF contract addresses (ConditionalTokens, NegRiskAdapter on Polygon)
- Minimal ABIs for redeemPositions

### `app.py` (minor changes)
- Open positions table: add `Stop Loss` column
- Settings modal: add `MIN_DISCOUNT` (default 2%), `STOP_LOSS_PCT` (default 20%), `MAX_EXPIRY_DAYS` (default 3)

## Risk Controls

| Parameter | Default | Config Key | Description |
|-----------|---------|------------|-------------|
| Max buy price | 0.99 | `MAX_BUY_PRICE` | Reject if estimated fill >= this |
| Min discount | 0.02 | `MIN_DISCOUNT` | Minimum (1 - real_ask) |
| Max position per event | 20% | `MAX_POSITION_PCT` | Concentration limit by event slug |
| Stop-loss | 20% drop | `STOP_LOSS_PCT` | Auto-sell threshold from entry |
| Max expiry days | 3 | `MAX_EXPIRY_DAYS` | Only trade markets closing within N days |
| Min volume | 1000 | `MIN_VOLUME` | Existing setting |

## Auto-Redeem

Two redemption paths (per LainNet-42 implementation):
1. **Normal markets** → `ConditionalTokens.redeemPositions(collateralToken, parentCollectionId, conditionId, indexSets)`
2. **Negative-risk markets** → `NegRiskAdapter.redeemPositions(...)` with different parameters

Detection: check market metadata for `negRisk` flag from Gamma API.

Configuration: `POLYGON_RPC_URL` in `.env` (required for auto-redeem).

## New Dependencies

- `web3>=6.0` — for on-chain redemption calls

## Testing Plan

- `test_polymarket_client.py`: order book walking (mock CLOB), find_expiry_candidates filtering, auto-redeem dispatch
- `test_gemini_agent.py`: verify_outcome JSON parsing, unverified case handling
- `test_trader.py`: stop-loss trigger logic, position concentration check, full scan loop
- `test_session_start.py`: auto-redeem with mocked Web3, stop-loss auto-sell

## What Is NOT Changing

- Dashboard layout (mostly intact, one new column)
- Database schema (additions only, no breaking changes)
- Paper/Real mode toggle
- Portfolio snapshots
- Settings modal (additions only)
- Buy-more modal
- All existing tests (no regressions)
