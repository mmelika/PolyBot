# Passed On Section Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Passed On" dashboard section showing Phase 3 markets that were fully analyzed but rejected by trade filters, with a human-readable reason why.

**Architecture:** New `skipped_markets` SQLite table stores rejected markets. `get_skip_reason()` in `trader.py` returns the filter reason as a string (or `None` to trade). `scan_and_trade()` calls it and persists skips. A new Dash section renders the table sorted by edge descending.

**Tech Stack:** Python, SQLite (via `sqlite3`), Dash (`dash`, `dash.html`, `dash.dcc`)

---

### Task 1: Add `skipped_markets` table and DB functions

**Files:**
- Modify: `database.py`
- Test: `tests/test_database.py`

**Step 1: Write the failing tests**

Add to `tests/test_database.py`:

```python
def test_init_creates_skipped_markets_table(db):
    import sqlite3
    conn = sqlite3.connect(db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert "skipped_markets" in tables


def test_insert_and_get_skipped_market(db):
    record = {
        "market_id": "mkt_skip_001",
        "question": "Will X happen?",
        "category": "crypto",
        "side": "YES",
        "probability": 0.65,
        "edge": 0.05,
        "confidence": "medium",
        "contested": False,
        "skip_reason": "edge too small (5.0%)",
        "reasoning": "Some reasoning here",
        "mode": "paper",
    }
    database.insert_skipped_market(db, record)
    rows = database.get_skipped_markets(db, limit=10, mode="paper")
    assert len(rows) == 1
    assert rows[0]["market_id"] == "mkt_skip_001"
    assert rows[0]["skip_reason"] == "edge too small (5.0%)"


def test_get_skipped_markets_sorted_by_edge_desc(db):
    for edge in [0.05, 0.12, 0.03]:
        database.insert_skipped_market(db, {
            "market_id": f"mkt_{edge}",
            "question": f"Market edge={edge}",
            "category": "other",
            "side": "YES",
            "probability": 0.6,
            "edge": edge,
            "confidence": "medium",
            "contested": False,
            "skip_reason": "edge too small",
            "reasoning": "",
            "mode": "paper",
        })
    rows = database.get_skipped_markets(db, limit=10, mode="paper")
    edges = [r["edge"] for r in rows]
    assert edges == sorted(edges, reverse=True)


def test_get_skipped_markets_filters_by_mode(db):
    for mode in ["paper", "real"]:
        database.insert_skipped_market(db, {
            "market_id": f"mkt_{mode}",
            "question": "Q",
            "category": "other",
            "side": "YES",
            "probability": 0.6,
            "edge": 0.05,
            "confidence": "medium",
            "contested": False,
            "skip_reason": "low confidence",
            "reasoning": "",
            "mode": mode,
        })
    paper_rows = database.get_skipped_markets(db, limit=10, mode="paper")
    assert all(r["mode"] == "paper" for r in paper_rows)
    assert len(paper_rows) == 1
```

**Step 2: Run tests to verify they fail**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_database.py::test_init_creates_skipped_markets_table tests/test_database.py::test_insert_and_get_skipped_market -v
```

Expected: FAIL — `AttributeError: module 'database' has no attribute 'insert_skipped_market'`

**Step 3: Add table creation to `init_db()` in `database.py`**

Inside `init_db()`, after the existing `app_state` table creation (after line ~49), add:

```python
    conn.execute("""
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
    """)
```

**Step 4: Add `insert_skipped_market()` to `database.py`**

Add after `insert_trade()`:

```python
def insert_skipped_market(db_path: str, record: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO skipped_markets
            (market_id, question, category, side, probability, edge, confidence,
             contested, skip_reason, reasoning, mode)
        VALUES
            (:market_id, :question, :category, :side, :probability, :edge, :confidence,
             :contested, :skip_reason, :reasoning, :mode)
    """, {**record, "contested": int(record.get("contested") or 0)})
    conn.commit()
    conn.close()
```

**Step 5: Add `get_skipped_markets()` to `database.py`**

```python
def get_skipped_markets(db_path: str, limit: int = 20, mode: str = "paper") -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """SELECT * FROM skipped_markets WHERE mode = ?
           ORDER BY edge DESC, created_at DESC LIMIT ?""",
        (mode, limit)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
```

**Step 6: Run all four new tests to verify they pass**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_database.py::test_init_creates_skipped_markets_table tests/test_database.py::test_insert_and_get_skipped_market tests/test_database.py::test_get_skipped_markets_sorted_by_edge_desc tests/test_database.py::test_get_skipped_markets_filters_by_mode -v
```

Expected: 4 PASSED

**Step 7: Run full test suite to check for regressions**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_database.py -v
```

Expected: all PASSED

**Step 8: Commit**

```bash
cd /Users/marco/Documents/PolyBot && git add database.py tests/test_database.py && git commit -m "feat: add skipped_markets table and DB functions"
```

---

### Task 2: Add `get_skip_reason()` to `trader.py` and refactor `should_trade()`

**Files:**
- Modify: `trader.py`
- Test: `tests/test_trader.py`

**Step 1: Write the failing tests**

Add to `tests/test_trader.py`:

```python
# --- get_skip_reason ---

def test_get_skip_reason_low_confidence():
    reason = trader.get_skip_reason(_analysis(0.9, edge=0.15, confidence="low"), _market(3), _settings())
    assert reason == "confidence: low"


def test_get_skip_reason_edge_too_small():
    reason = trader.get_skip_reason(_analysis(0.9, edge=0.01), _market(3), _settings())
    assert "edge too small" in reason
    assert "1.0%" in reason


def test_get_skip_reason_long_term_low_prob():
    reason = trader.get_skip_reason(_analysis(0.60, edge=0.15), _market(10), _settings())
    assert "long-term" in reason


def test_get_skip_reason_contested():
    analysis = _analysis_with_contested(contested=True, confidence="medium", edge=0.12)
    reason = trader.get_skip_reason(analysis, _market(3), _settings())
    assert "contested" in reason


def test_get_skip_reason_none_when_should_trade():
    reason = trader.get_skip_reason(_analysis(0.85, edge=0.15), _market(3), _settings())
    assert reason is None


def test_should_trade_delegates_to_get_skip_reason():
    """should_trade() must agree with get_skip_reason() on all cases."""
    cases = [
        (_analysis(0.9, edge=0.15, confidence="low"), _market(3)),
        (_analysis(0.9, edge=0.01), _market(3)),
        (_analysis(0.60, edge=0.15), _market(10)),
        (_analysis(0.85, edge=0.15), _market(3)),
    ]
    settings = _settings()
    for analysis, market in cases:
        skip_reason = trader.get_skip_reason(analysis, market, settings)
        expected = skip_reason is None
        assert trader.should_trade(analysis, market, settings) == expected
```

**Step 2: Run tests to verify they fail**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_trader.py::test_get_skip_reason_low_confidence tests/test_trader.py::test_get_skip_reason_none_when_should_trade -v
```

Expected: FAIL — `AttributeError: module 'trader' has no attribute 'get_skip_reason'`

**Step 3: Add `get_skip_reason()` to `trader.py`**

Add after the `get_status()` function (after line 21), before `should_trade()`:

```python
def get_skip_reason(analysis: dict, market: dict, settings: dict) -> Optional[str]:
    """Return a human-readable skip reason, or None if the trade should proceed."""
    if analysis.get("confidence") == "low":
        return "confidence: low"
    edge = analysis.get("edge", 0)
    if edge < settings["min_advantage"]:
        return f"edge too small ({edge:.1%})"
    end_date_str = market.get("end_date_iso", "")
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            days_to_close = (end_date - datetime.now(timezone.utc)).total_seconds() / 86400
            if days_to_close > settings["long_term_days"]:
                prob = analysis.get("probability", 0)
                threshold = settings["long_term_min_prob"]
                if prob < threshold:
                    return f"long-term: probability {prob:.0%} below {threshold:.0%} threshold"
        except (ValueError, AttributeError):
            pass
    if analysis.get("contested"):
        if analysis.get("confidence") != "high" or edge < 0.15:
            return "contested: insufficient edge/confidence"
    return None
```

**Step 4: Refactor `should_trade()` to delegate**

Replace the body of `should_trade()` (lines 23-41) with:

```python
def should_trade(analysis: dict, market: dict, settings: dict) -> bool:
    return get_skip_reason(analysis, market, settings) is None
```

**Step 5: Run new tests**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_trader.py::test_get_skip_reason_low_confidence tests/test_trader.py::test_get_skip_reason_edge_too_small tests/test_trader.py::test_get_skip_reason_long_term_low_prob tests/test_trader.py::test_get_skip_reason_contested tests/test_trader.py::test_get_skip_reason_none_when_should_trade tests/test_trader.py::test_should_trade_delegates_to_get_skip_reason -v
```

Expected: 6 PASSED

**Step 6: Run full trader test suite to check regressions**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_trader.py -v
```

Expected: all PASSED

**Step 7: Commit**

```bash
cd /Users/marco/Documents/PolyBot && git add trader.py tests/test_trader.py && git commit -m "feat: add get_skip_reason(), refactor should_trade() to delegate"
```

---

### Task 3: Store skipped markets in `scan_and_trade()`

**Files:**
- Modify: `trader.py`
- Test: `tests/test_trader.py`

**Step 1: Write the failing test**

Add to `tests/test_trader.py`:

```python
def test_scan_and_trade_stores_skipped_market(mock_db):
    """Market that fails should_trade() is stored in skipped_markets."""
    markets = [{
        "market_id": "mkt_skip",
        "question": "Will this be skipped?",
        "category": "sports",
        "yes_price": 0.50,
        "no_price": 0.50,
        "volume": 10000,
        "end_date_iso": "2026-05-01T00:00:00Z",
        "yes_token_id": "t_yes",
        "no_token_id": "t_no",
    }]
    screen_result = [{"market_id": "mkt_skip", "initial_lean": "YES", "reason": "Looks interesting"}]
    research_result = {
        "key_facts": ["50/50 market"],
        "base_rate": "~50%",
        "recent_developments": "Nothing notable",
        "uncertainty_factors": ["Coin flip"],
    }
    # Analysis with confidence=low → will be skipped
    analysis_result = {
        "probability": 0.55,
        "side": "YES",
        "confidence": "low",
        "base_rate_estimate": 0.50,
        "contested": False,
        "reasoning": "Too uncertain to call",
        "edge": 0.05,
        "entry_price": 0.50,
        "token_id": "t_yes",
    }

    with patch("trader.polymarket_client.get_active_markets", return_value=markets), \
         patch("trader.gemini_agent.screen_markets", return_value=screen_result), \
         patch("trader.gemini_agent.research_market", return_value=research_result), \
         patch("trader.gemini_agent.assign_probability", return_value=analysis_result), \
         patch("trader.gemini_agent.calculate_position_size", return_value=15.0):
        count = trader.scan_and_trade(mock_db)

    assert count == 0  # no trade placed
    import database
    skipped = database.get_skipped_markets(mock_db, limit=10, mode="paper")
    assert len(skipped) == 1
    assert skipped[0]["market_id"] == "mkt_skip"
    assert skipped[0]["skip_reason"] == "confidence: low"
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_trader.py::test_scan_and_trade_stores_skipped_market -v
```

Expected: FAIL — skipped list is empty (skip not stored yet)

**Step 3: Update `scan_and_trade()` to store skips**

In `scan_and_trade()`, replace the block at lines 166-176:

```python
        if not should_trade(analysis, market, settings):
            continue

        size_usd = gemini_agent.calculate_position_size(
            probability=analysis["probability"],
            entry_price=analysis["entry_price"],
            portfolio_value=total_value,
        )
        size_usd = min(size_usd, settings["max_position_size"])
        if size_usd < 1.0:
            continue
```

With:

```python
        skip_reason = get_skip_reason(analysis, market, settings)
        if skip_reason is None:
            size_usd = gemini_agent.calculate_position_size(
                probability=analysis["probability"],
                entry_price=analysis["entry_price"],
                portfolio_value=total_value,
            )
            size_usd = min(size_usd, settings["max_position_size"])
            if size_usd < 1.0:
                skip_reason = f"position size too small (${size_usd:.2f})"

        if skip_reason is not None:
            database.insert_skipped_market(db_path, {
                "market_id": market["market_id"],
                "question": market["question"],
                "category": market.get("category", "other"),
                "side": analysis["side"],
                "probability": analysis["probability"],
                "edge": analysis.get("edge"),
                "confidence": analysis.get("confidence"),
                "contested": analysis.get("contested", False),
                "skip_reason": skip_reason,
                "reasoning": analysis.get("reasoning", ""),
                "mode": mode,
            })
            continue
```

**Step 4: Run new test**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_trader.py::test_scan_and_trade_stores_skipped_market -v
```

Expected: PASS

**Step 5: Run full test suite**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/ -v
```

Expected: all PASSED

**Step 6: Commit**

```bash
cd /Users/marco/Documents/PolyBot && git add trader.py tests/test_trader.py && git commit -m "feat: store skipped markets in scan_and_trade"
```

---

### Task 4: Add "Passed On" section to the dashboard

**Files:**
- Modify: `app.py`

No new tests needed — Dash UI rendering is verified visually.

**Step 1: Add `render_passed_on()` function**

In `app.py`, add after `render_gemini_reasoning()` (after line 276):

```python
def render_passed_on(skipped):
    if not skipped:
        return html.Div("No skipped markets yet", className="empty-state")
    rows = []
    for s in skipped[:20]:
        prob = s.get("probability")
        prob_str = f"{prob:.0%}" if prob is not None else "—"
        edge = s.get("edge")
        edge_str = f"{edge:.1%}" if edge is not None else "—"
        conf = s.get("confidence") or "—"
        outcome_cls = "pill-yes" if s.get("side") == "YES" else "pill-no"
        reason = s.get("skip_reason") or "—"
        reason_color = "#ef4444" if any(k in reason for k in ("confidence", "contested")) else "#fbbf24"
        rows.append(html.Tr([
            html.Td(s["question"], className="market-cell", title=s["question"],
                    style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(html.Span(s.get("side", "—"), className=outcome_cls),
                    style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)"}),
            html.Td(prob_str, className="prob-value",
                    style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)",
                           "color": prob_color(prob)}),
            html.Td(edge_str, className="mono",
                    style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)",
                           "color": "#a78bfa"}),
            html.Td(conf, style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)",
                                  "color": "#a1a1aa", "fontSize": "11px"}),
            html.Td(reason, style={"padding": "9px 10px", "borderBottom": "1px solid rgba(255,255,255,0.03)",
                                    "color": reason_color, "fontSize": "11px"}),
        ]))
    return _table(["MARKET", "OUTCOME", "PROB", "EDGE", "CONF", "WHY NOT PICKED"], rows)
```

**Step 2: Add section card to the layout**

In `app.layout`, in the right column (`style={"flex": "1"}`), after the `"gemini-reasoning"` section card (after line ~81), add:

```python
                html.Div(className="section-card", children=[
                    html.Div(className="section-header", children=[
                        html.Span("Passed On", className="section-title"),
                        html.Span(id="passed-on-badge", className="badge"),
                    ]),
                    html.Div(id="passed-on-table"),
                ]),
```

**Step 3: Add outputs to the `refresh()` callback**

In the `@app.callback` decorator for `refresh()`, add two new outputs:

```python
    Output("passed-on-table", "children"),
    Output("passed-on-badge", "children"),
```

**Step 4: Query skipped markets in `refresh()`**

In the `refresh()` function body, after the `daily = ...` line, add:

```python
    mode = database.get_app_state(config.DB_PATH, "trading_mode", config.TRADING_MODE)
    skipped = database.get_skipped_markets(config.DB_PATH, limit=20, mode=mode)
```

Note: `mode` may already be fetched elsewhere in `refresh()` — if so, reuse the existing variable rather than fetching it twice.

**Step 5: Add to the return tuple**

In the `return (...)` statement of `refresh()`, add:

```python
        render_passed_on(skipped), f"{len(skipped)} markets",
```

**Step 6: Start the app and verify the section renders**

```bash
cd /Users/marco/Documents/PolyBot && python app.py
```

Open http://localhost:8050 — verify the "Passed On" section card appears in the right column with "No skipped markets yet" (since no scan has run yet).

**Step 7: Commit**

```bash
cd /Users/marco/Documents/PolyBot && git add app.py && git commit -m "feat: add Passed On section to dashboard"
```

---

### Task 5: Final regression check

**Step 1: Run all tests**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/ -v
```

Expected: all PASSED, no regressions.

**Step 2: Commit if any final fixups were needed**

```bash
cd /Users/marco/Documents/PolyBot && git add -p && git commit -m "fix: <describe any fixup>"
```
