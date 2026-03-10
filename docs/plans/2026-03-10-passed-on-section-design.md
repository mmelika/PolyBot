# "Passed On" Section Design

**Date:** 2026-03-10
**Status:** Approved

## Problem

Markets that pass all three pipeline phases but are rejected by `should_trade()` filters vanish silently. There's no way to audit what the bot almost-picked, or understand why it exercised restraint. A "Passed On" section makes the bot's discipline visible and debuggable.

---

## Scope

Only Phase 3 rejects — markets that received a full Gemini probability assignment but were rejected by `should_trade()` or the `size_usd < 1.0` guard. Phase 1 (screener) rejects are excluded because they lack probability/edge data.

---

## Database (`database.py`)

New `skipped_markets` table created in `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS skipped_markets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL,
    question    TEXT NOT NULL,
    category    TEXT DEFAULT 'other',
    side        TEXT NOT NULL,
    probability REAL,
    edge        REAL,
    confidence  TEXT,
    contested   INTEGER DEFAULT 0,
    skip_reason TEXT NOT NULL,
    reasoning   TEXT,
    mode        TEXT DEFAULT 'paper',
    created_at  TEXT DEFAULT (datetime('now'))
)
```

New functions:
- `insert_skipped_market(db_path, record)` — inserts one row
- `get_skipped_markets(db_path, limit=20, mode="paper")` — returns rows ordered by `edge DESC`, then `created_at DESC`

---

## `trader.py`

Add `get_skip_reason(analysis, market, settings) -> str | None`:
- Returns `None` if the trade should proceed (mirrors `should_trade()` logic exactly)
- Returns a human-readable string for each rejection:
  - `"confidence: low"`
  - `"edge too small ({edge:.1%})"`
  - `"long-term: probability {prob:.0%} below {threshold:.0%} threshold"`
  - `"contested: insufficient edge/confidence"`
  - `"position size too small (${size:.2f})"`

Refactor `should_trade()` to delegate: `return get_skip_reason(...) is None`.

In `scan_and_trade()`, after `should_trade()` returns False, call `insert_skipped_market()`. Also move the `size_usd < 1.0` guard to use `get_skip_reason()` so it's captured too.

---

## UI (`app.py`)

New `render_passed_on(skipped)` function. New **"Passed On"** section card in the right column, below "Latest Gemini Analysis".

Table columns: `MARKET | OUTCOME | PROB | EDGE | CONF | WHY NOT PICKED`

- `PROB` — colored green/amber/red via `prob_color()`
- `EDGE` — purple mono, same as Recent Trades
- `WHY NOT PICKED` — `#ef4444` (red) for confidence/contested reasons, `#fbbf24` (amber) for edge/size/long-term reasons
- Shows 20 most recent, sorted by edge descending

The `refresh()` callback gains a new output `skipped-markets-table` and queries `database.get_skipped_markets()`.

---

## Files Changed

| File | Change |
|------|--------|
| `database.py` | Add `skipped_markets` table to `init_db()`, add `insert_skipped_market()` and `get_skipped_markets()` |
| `trader.py` | Add `get_skip_reason()`, refactor `should_trade()` to delegate, store skips in `scan_and_trade()` |
| `app.py` | Add `render_passed_on()`, new section card, new callback output |
