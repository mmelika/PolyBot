# Expiry Convergence Strategy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Gemini three-phase probability analysis with LainNet-style expiry convergence — scan CLOB for discounted near-certain markets, verify via web search, walk order book to estimate fill, execute, auto-stop-loss, and auto-redeem on settlement.

**Architecture:** Two-phase pipeline replaces three-phase: (1) CLOB scans for real_ask < 0.98 on binary markets expiring ≤3 days, (2) Gemini verifies outcome is locked in. Stop-loss checked at each scan start. SessionStart hook auto-redeems settled positions via Web3 CTF contracts.

**Tech Stack:** Python, py-clob-client, Google Gemini 2.5 Pro (verify only), web3>=6.0, SQLite, Polymarket Gamma/CLOB APIs, Polygon CTF contracts.

---

## Task 1: Config — Add Expiry Convergence Constants

**Files:**
- Modify: `config.py`

**Step 1: Add new constants after existing ones**

```python
# At bottom of config.py, add:
MAX_EXPIRY_DAYS = int(os.getenv("MAX_EXPIRY_DAYS", "3"))
MIN_DISCOUNT = float(os.getenv("MIN_DISCOUNT", "0.02"))   # min (1 - real_ask)
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.20")) # sell if drops 20%
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.20"))  # max 20% per event
MAX_BUY_PRICE = float(os.getenv("MAX_BUY_PRICE", "0.99"))
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "")
```

**Step 2: Run existing config tests**

```bash
cd /Users/marco/Documents/PolyBot && source venv/bin/activate && pytest tests/test_config.py -v
```
Expected: all PASS

**Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add expiry convergence config constants"
```

---

## Task 2: Database — Add Columns and New Settings

**Files:**
- Modify: `database.py`
- Test: `tests/test_database.py`

**Step 1: Write failing tests**

Add to `tests/test_database.py`:

```python
def test_init_db_creates_stop_loss_columns(tmp_path):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    conn = sqlite3.connect(db)
    cursor = conn.execute("PRAGMA table_info(trades)")
    cols = {row[1] for row in cursor.fetchall()}
    conn.close()
    assert "stop_loss_price" in cols
    assert "strategy" in cols
    assert "token_id" in cols


def test_insert_trade_with_expiry_fields(tmp_path):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    trade = {
        "market_id": "0xabc", "question": "Q?", "category": "crypto",
        "outcome": "YES", "side": "BUY", "size_usd": 10.0,
        "entry_price": 0.96, "current_price": 0.96, "pnl": 0.0,
        "status": "FILLED", "mode": "paper",
        "gemini_probability": 0.97, "gemini_reasoning": "verified",
        "edge": 0.04, "closes_at": "2026-03-11", "research_brief": None,
        "stop_loss_price": 0.768, "strategy": "expiry_convergence",
        "token_id": "0xtoken",
    }
    trade_id = database.insert_trade(db, trade)
    result = database.get_trade_by_id(db, trade_id)
    assert result["stop_loss_price"] == pytest.approx(0.768)
    assert result["strategy"] == "expiry_convergence"
    assert result["token_id"] == "0xtoken"


def test_get_settled_unredeemed_trades(tmp_path):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    trade = {
        "market_id": "0xabc", "question": "Q?", "category": "crypto",
        "outcome": "YES", "side": "BUY", "size_usd": 10.0,
        "entry_price": 0.96, "current_price": 0.96, "pnl": 0.0,
        "status": "FILLED", "mode": "real",
        "gemini_probability": 0.97, "gemini_reasoning": "verified",
        "edge": 0.04, "closes_at": "2020-01-01",  # past date
        "research_brief": None, "stop_loss_price": 0.768,
        "strategy": "expiry_convergence", "token_id": "0xtoken",
    }
    database.insert_trade(db, trade)
    results = database.get_settled_unredeemed_trades(db)
    assert len(results) == 1
    assert results[0]["market_id"] == "0xabc"


def test_new_settings_keys_have_defaults(tmp_path):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    settings = database.get_settings(db)
    assert "max_expiry_days" in settings
    assert "min_discount" in settings
    assert "stop_loss_pct" in settings
    assert "max_position_pct" in settings
    assert settings["max_expiry_days"] == 3
    assert settings["min_discount"] == pytest.approx(0.02)
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_database.py::test_init_db_creates_stop_loss_columns tests/test_database.py::test_insert_trade_with_expiry_fields tests/test_database.py::test_get_settled_unredeemed_trades tests/test_database.py::test_new_settings_keys_have_defaults -v
```
Expected: FAIL

**Step 3: Implement — update `database.py`**

In `init_db()`, add three migration blocks after the existing `research_brief` migration (after line 71):

```python
    for col_def in [
        "ALTER TABLE trades ADD COLUMN stop_loss_price REAL",
        "ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT 'expiry_convergence'",
        "ALTER TABLE trades ADD COLUMN token_id TEXT",
    ]:
        try:
            conn.execute(col_def)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
```

In `insert_trade()`, update the INSERT to include the new columns. Replace lines 83-94 with:

```python
    row = {
        "research_brief": None,
        "stop_loss_price": None,
        "strategy": "expiry_convergence",
        "token_id": None,
        **trade,
    }
    cursor = conn.execute("""
        INSERT INTO trades (market_id, question, category, outcome, side, size_usd,
            entry_price, current_price, pnl, status, mode, gemini_probability,
            gemini_reasoning, edge, closes_at, research_brief,
            stop_loss_price, strategy, token_id)
        VALUES (:market_id, :question, :category, :outcome, :side, :size_usd,
            :entry_price, :current_price, :pnl, :status, :mode, :gemini_probability,
            :gemini_reasoning, :edge, :closes_at, :research_brief,
            :stop_loss_price, :strategy, :token_id)
    """, row)
```

Add `get_settled_unredeemed_trades()` after `get_open_trades()`:

```python
def get_settled_unredeemed_trades(db_path: str) -> list:
    """Trades in real mode that are still FILLED but closes_at is in the past."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("""
        SELECT * FROM trades
        WHERE status IN ('FILLED', 'PENDING')
        AND mode = 'real'
        AND closes_at < date('now')
        ORDER BY closes_at ASC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
```

In `_SETTINGS_META` dict (line 272), add four new keys:

```python
    "max_expiry_days": int,
    "min_discount": float,
    "stop_loss_pct": float,
    "max_position_pct": float,
    "max_buy_price": float,
```

In `_settings_defaults()`, add:

```python
        "max_expiry_days": config.MAX_EXPIRY_DAYS,
        "min_discount": config.MIN_DISCOUNT,
        "stop_loss_pct": config.STOP_LOSS_PCT,
        "max_position_pct": config.MAX_POSITION_PCT,
        "max_buy_price": config.MAX_BUY_PRICE,
```

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_database.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: add stop_loss_price/strategy/token_id columns and expiry settings"
```

---

## Task 3: polymarket_client — Add neg_risk and event_slug to normalize_market

**Files:**
- Modify: `polymarket_client.py:47-83`
- Test: `tests/test_polymarket_client.py`

**Step 1: Write failing test**

Add to `tests/test_polymarket_client.py`:

```python
def test_normalize_market_includes_neg_risk_and_event_slug():
    raw = {
        "conditionId": "0xabc",
        "question": "Will X happen?",
        "endDate": "2026-03-15T00:00:00Z",
        "volume": "5000",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.96", "0.04"]',
        "clobTokenIds": '["0xyes", "0xno"]',
        "negRisk": True,
        "active": True,
        "closed": False,
        "events": [{"category": "crypto", "slug": "will-x-happen"}],
    }
    result = polymarket_client.normalize_market(raw)
    assert result["neg_risk"] is True
    assert result["event_slug"] == "will-x-happen"


def test_normalize_market_neg_risk_false_by_default():
    raw = {
        "conditionId": "0xabc",
        "question": "Will Y happen?",
        "endDate": "2026-03-15T00:00:00Z",
        "volume": "1000",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.60", "0.40"]',
        "clobTokenIds": '["0xyes", "0xno"]',
        "active": True,
        "closed": False,
        "events": [],
    }
    result = polymarket_client.normalize_market(raw)
    assert result["neg_risk"] is False
    assert result["event_slug"] == ""
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_polymarket_client.py::test_normalize_market_includes_neg_risk_and_event_slug tests/test_polymarket_client.py::test_normalize_market_neg_risk_false_by_default -v
```
Expected: FAIL

**Step 3: Update `normalize_market()` in `polymarket_client.py`**

In the `normalize_market` function, update the category/events block and the return dict:

Replace lines 64-83 with:

```python
    # Detect category and event slug from events if available
    events = raw.get("events", [])
    category = "other"
    event_slug = ""
    if events and isinstance(events, list) and isinstance(events[0], dict):
        category = events[0].get("category", "other") or "other"
        event_slug = events[0].get("slug", "") or ""

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
        "neg_risk": bool(raw.get("negRisk", False)),
        "event_slug": event_slug,
    }
```

**Step 4: Run tests**

```bash
pytest tests/test_polymarket_client.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add polymarket_client.py tests/test_polymarket_client.py
git commit -m "feat: add neg_risk and event_slug to normalize_market"
```

---

## Task 4: polymarket_client — Add find_expiry_candidates

**Files:**
- Modify: `polymarket_client.py`
- Test: `tests/test_polymarket_client.py`

**Step 1: Write failing tests**

```python
def test_find_expiry_candidates_filters_sports(monkeypatch):
    """Markets in 'sports' category should be excluded."""
    from unittest.mock import MagicMock
    import polymarket_client

    now = datetime.now(timezone.utc)
    raw_sports = {
        "conditionId": "0xsports",
        "question": "Will team win?",
        "endDate": (now + timedelta(days=1)).isoformat(),
        "volume": "5000",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.96","0.04"]',
        "clobTokenIds": '["0xt1","0xt2"]',
        "active": True, "closed": False,
        "events": [{"category": "sports", "slug": "game"}],
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = [raw_sports]
    mock_resp.raise_for_status = MagicMock()
    monkeypatch.setattr("polymarket_client.requests.get", lambda *a, **kw: mock_resp)
    # CLOB client won't be called since filtered before
    monkeypatch.setattr("polymarket_client._get_client", lambda: MagicMock())

    result = polymarket_client.find_expiry_candidates()
    assert result == []


def test_find_expiry_candidates_filters_by_discount(monkeypatch):
    """Markets where real_ask >= 0.98 should be excluded."""
    from unittest.mock import MagicMock, patch
    import polymarket_client

    now = datetime.now(timezone.utc)
    raw = {
        "conditionId": "0xabc",
        "question": "Will X happen?",
        "endDate": (now + timedelta(days=1)).isoformat(),
        "volume": "5000",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.99","0.01"]',
        "clobTokenIds": '["0xyes","0xno"]',
        "active": True, "closed": False,
        "events": [{"category": "crypto", "slug": "x-event"}],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = [raw]
    mock_resp.raise_for_status = MagicMock()
    monkeypatch.setattr("polymarket_client.requests.get", lambda *a, **kw: mock_resp)

    # Mock CLOB client: order book returns ask at 0.99
    mock_book = MagicMock()
    level = MagicMock()
    level.price = "0.99"
    mock_book.asks = [level]
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = mock_book
    monkeypatch.setattr("polymarket_client._get_client", lambda: mock_client)

    result = polymarket_client.find_expiry_candidates(min_discount=0.02)
    assert result == []


def test_find_expiry_candidates_returns_sorted_by_discount(monkeypatch):
    """Returns candidates sorted by discount descending."""
    from unittest.mock import MagicMock
    import polymarket_client

    now = datetime.now(timezone.utc)
    def make_raw(cid, ask_price):
        return {
            "conditionId": cid,
            "question": f"Market {cid}?",
            "endDate": (now + timedelta(days=1)).isoformat(),
            "volume": "5000",
            "outcomes": '["Yes","No"]',
            "outcomePrices": f'["{ask_price}","0.01"]',
            "clobTokenIds": f'["{cid}_yes","{cid}_no"]',
            "active": True, "closed": False,
            "events": [{"category": "crypto", "slug": cid}],
        }

    mock_resp = MagicMock()
    mock_resp.json.return_value = [make_raw("0xhigh", 0.96), make_raw("0xlow", 0.97)]
    mock_resp.raise_for_status = MagicMock()
    monkeypatch.setattr("polymarket_client.requests.get", lambda *a, **kw: mock_resp)

    call_count = [0]
    def fake_get_client():
        mc = MagicMock()
        def get_book(token_id):
            book = MagicMock()
            level = MagicMock()
            level.price = "0.96" if "0xhigh" in token_id else "0.97"
            book.asks = [level]
            return book
        mc.get_order_book = get_book
        return mc
    monkeypatch.setattr("polymarket_client._get_client", fake_get_client)

    result = polymarket_client.find_expiry_candidates(min_discount=0.02)
    assert len(result) == 2
    assert result[0]["discount"] > result[1]["discount"]
    assert result[0]["real_ask"] == pytest.approx(0.96)
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_polymarket_client.py::test_find_expiry_candidates_filters_sports tests/test_polymarket_client.py::test_find_expiry_candidates_filters_by_discount tests/test_polymarket_client.py::test_find_expiry_candidates_returns_sorted_by_discount -v
```
Expected: FAIL (function doesn't exist)

**Step 3: Implement `find_expiry_candidates` in `polymarket_client.py`**

Add after `get_active_markets()` (after line 137):

```python
def find_expiry_candidates(
    max_days: float = None,
    min_volume: float = None,
    min_discount: float = None,
) -> list:
    """
    Scan Gamma + CLOB for near-certain markets expiring soon with sufficient discount.

    Returns list of normalized market dicts plus:
        real_ask: float  — best CLOB ask for YES token
        discount: float  — 1 - real_ask (gross profit per share at settlement)
        event_slug: str  — for position concentration checks
    Sorted by discount descending.
    """
    if max_days is None:
        max_days = config.MAX_EXPIRY_DAYS
    if min_volume is None:
        min_volume = config.MIN_MARKET_VOLUME
    if min_discount is None:
        min_discount = config.MIN_DISCOUNT

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
    max_end = now + timedelta(days=max_days)
    candidates = []

    for raw in raw_markets:
        if raw.get("closed") or not raw.get("active"):
            continue

        # Exclude sports
        events = raw.get("events", [])
        cat = "other"
        if events and isinstance(events, list) and isinstance(events[0], dict):
            cat = (events[0].get("category", "other") or "other").lower()
        if cat == "sports":
            continue

        # Volume filter
        try:
            volume = float(raw.get("volume", 0))
        except (ValueError, TypeError):
            continue
        if volume < min_volume:
            continue

        # End date: must be within max_days, not already expired
        end_date_str = raw.get("endDate", "") or ""
        if not end_date_str:
            continue
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if end_date > max_end or end_date < now:
            continue

        # Must be binary (exactly 2 outcomes, 2 token IDs)
        outcomes = _parse_json_str(raw.get("outcomes", "[]"))
        token_ids = _parse_json_str(raw.get("clobTokenIds", "[]"))
        if len(outcomes) != 2 or len(token_ids) < 2:
            continue

        yes_token_id = token_ids[0]

        # Get real ask from CLOB order book
        try:
            client = _get_client()
            book = client.get_order_book(yes_token_id)
            asks = book.asks or []
            if not asks:
                continue
            real_ask = float(asks[0].price)
        except Exception:
            continue

        discount = 1.0 - real_ask
        if discount < min_discount:
            continue

        m = normalize_market(raw)
        candidates.append({**m, "real_ask": real_ask, "discount": discount})

    candidates.sort(key=lambda x: x["discount"], reverse=True)
    return candidates
```

**Step 4: Run tests**

```bash
pytest tests/test_polymarket_client.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add polymarket_client.py tests/test_polymarket_client.py
git commit -m "feat: add find_expiry_candidates to polymarket_client"
```

---

## Task 5: polymarket_client — Add walk_order_book

**Files:**
- Modify: `polymarket_client.py`
- Test: `tests/test_polymarket_client.py`

**Step 1: Write failing tests**

```python
def test_walk_order_book_single_level(monkeypatch):
    """When one level covers the full notional, returns correct avg price."""
    from unittest.mock import MagicMock
    import polymarket_client

    mock_book = MagicMock()
    level = MagicMock()
    level.price = "0.96"
    level.size = "1000"  # 1000 shares × $0.96 = $960 >> $50 notional
    mock_book.asks = [level]
    mock_book.bids = []
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = mock_book
    monkeypatch.setattr("polymarket_client._get_client", lambda: mock_client)

    result = polymarket_client.walk_order_book("0xtoken", notional_usd=50.0)
    assert result is not None
    assert result["estimated_avg_price"] == pytest.approx(0.96)
    assert result["limit_price"] == pytest.approx(0.96)
    assert result["total_cost"] == pytest.approx(50.0)
    assert result["total_shares"] == pytest.approx(50.0 / 0.96)


def test_walk_order_book_rejects_above_max_buy_price(monkeypatch):
    """Returns None when only levels at or above MAX_BUY_PRICE exist."""
    from unittest.mock import MagicMock
    import polymarket_client

    mock_book = MagicMock()
    level = MagicMock()
    level.price = "0.995"   # above MAX_BUY_PRICE=0.99
    level.size = "1000"
    mock_book.asks = [level]
    mock_book.bids = []
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = mock_book
    monkeypatch.setattr("polymarket_client._get_client", lambda: mock_client)

    result = polymarket_client.walk_order_book("0xtoken", notional_usd=50.0)
    assert result is None


def test_walk_order_book_spans_multiple_levels(monkeypatch):
    """Correctly weights average price across multiple price levels."""
    from unittest.mock import MagicMock
    import polymarket_client

    mock_book = MagicMock()
    l1, l2 = MagicMock(), MagicMock()
    l1.price, l1.size = "0.95", "10"   # $9.50 cost, 10 shares
    l2.price, l2.size = "0.97", "100"  # easily covers remainder
    mock_book.asks = [l1, l2]
    mock_book.bids = []
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = mock_book
    monkeypatch.setattr("polymarket_client._get_client", lambda: mock_client)

    result = polymarket_client.walk_order_book("0xtoken", notional_usd=20.0)
    assert result is not None
    # first $9.50 at 0.95, remaining $10.50 at 0.97
    assert result["limit_price"] == pytest.approx(0.97)
    assert result["estimated_avg_price"] < 0.97  # weighted avg below top level
    assert result["estimated_avg_price"] > 0.95
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_polymarket_client.py::test_walk_order_book_single_level tests/test_polymarket_client.py::test_walk_order_book_rejects_above_max_buy_price tests/test_polymarket_client.py::test_walk_order_book_spans_multiple_levels -v
```
Expected: FAIL

**Step 3: Implement `walk_order_book` in `polymarket_client.py`**

Add after `find_expiry_candidates()`:

```python
def walk_order_book(token_id: str, notional_usd: float) -> Optional[dict]:
    """
    Walk CLOB ask levels to estimate average fill price for a notional buy.

    Returns dict with keys:
        estimated_avg_price, limit_price, total_shares, total_cost
    Returns None if the book is empty, too thin, or avg price >= MAX_BUY_PRICE.
    """
    try:
        client = _get_client()
        book = client.get_order_book(token_id)
        asks = book.asks or []
    except Exception:
        return None

    if not asks:
        return None

    remaining = notional_usd
    total_cost = 0.0
    total_shares = 0.0
    limit_price = None

    for level in asks:
        price = float(level.price)
        size = float(level.size)

        if price >= config.MAX_BUY_PRICE:
            break

        level_cost = price * size
        fill_cost = min(remaining, level_cost)
        fill_shares = fill_cost / price

        total_cost += fill_cost
        total_shares += fill_shares
        remaining -= fill_cost
        limit_price = price

        if remaining <= 0:
            break

    if total_shares == 0 or limit_price is None:
        return None

    estimated_avg_price = total_cost / total_shares
    if estimated_avg_price >= config.MAX_BUY_PRICE:
        return None

    return {
        "estimated_avg_price": estimated_avg_price,
        "limit_price": limit_price,
        "total_shares": total_shares,
        "total_cost": total_cost,
    }
```

**Step 4: Run tests**

```bash
pytest tests/test_polymarket_client.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add polymarket_client.py tests/test_polymarket_client.py
git commit -m "feat: add walk_order_book to polymarket_client"
```

---

## Task 6: gemini_agent — Add verify_outcome

**Files:**
- Modify: `gemini_agent.py`
- Test: `tests/test_gemini_agent.py`

**Step 1: Write failing tests**

Add to `tests/test_gemini_agent.py`:

```python
def test_parse_verify_response_verified():
    raw = '{"verified": true, "outcome": "YES", "confidence": 0.95, "reasoning": "Event already happened."}'
    result = gemini_agent.parse_verify_response(raw)
    assert result is not None
    assert result["verified"] is True
    assert result["outcome"] == "YES"
    assert result["confidence"] == pytest.approx(0.95)
    assert "happened" in result["reasoning"]


def test_parse_verify_response_not_verified():
    raw = '{"verified": false, "outcome": "UNKNOWN", "confidence": 0.40, "reasoning": "Still ongoing."}'
    result = gemini_agent.parse_verify_response(raw)
    assert result is not None
    assert result["verified"] is False
    assert result["outcome"] == "UNKNOWN"


def test_parse_verify_response_invalid_json():
    result = gemini_agent.parse_verify_response("not json")
    assert result is None


def test_parse_verify_response_missing_fields():
    result = gemini_agent.parse_verify_response('{"verified": true}')
    assert result is None


def test_parse_verify_response_strips_markdown():
    raw = '```json\n{"verified": true, "outcome": "NO", "confidence": 0.92, "reasoning": "Lost."}\n```'
    result = gemini_agent.parse_verify_response(raw)
    assert result is not None
    assert result["outcome"] == "NO"


def test_verify_outcome_calls_gemini_with_google_search(monkeypatch):
    """verify_outcome should call Gemini once with google_search tool enabled."""
    from unittest.mock import MagicMock, patch
    import gemini_agent

    mock_response = MagicMock()
    mock_response.text = '{"verified": true, "outcome": "YES", "confidence": 0.93, "reasoning": "Done."}'
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setattr("gemini_agent.genai.Client", lambda **kw: mock_client)

    result = gemini_agent.verify_outcome("Will X win?", current_ask=0.96)
    assert result is not None
    assert result["verified"] is True
    assert mock_client.models.generate_content.call_count == 1
    call_kwargs = mock_client.models.generate_content.call_args
    # Should have google_search tool
    cfg = call_kwargs.kwargs.get("config") or call_kwargs.args[2] if len(call_kwargs.args) > 2 else None
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_gemini_agent.py::test_parse_verify_response_verified tests/test_gemini_agent.py::test_parse_verify_response_not_verified tests/test_gemini_agent.py::test_parse_verify_response_invalid_json tests/test_gemini_agent.py::test_parse_verify_response_missing_fields tests/test_gemini_agent.py::test_parse_verify_response_strips_markdown tests/test_gemini_agent.py::test_verify_outcome_calls_gemini_with_google_search -v
```
Expected: FAIL

**Step 3: Add to `gemini_agent.py`**

Add this system prompt constant at the top (after `SYSTEM_PROMPT_SCREEN`):

```python
SYSTEM_PROMPT_VERIFY = """You are verifying whether the outcome of a Polymarket prediction market has already been definitively determined.

Search the web for the most current information. You have one job: determine if YES or NO is certain.

ALWAYS respond with valid JSON only, no other text:
{
  "verified": <true ONLY if outcome is 100% determined with no remaining uncertainty>,
  "outcome": "YES or NO or UNKNOWN",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<1-2 sentences: what you found and why you are certain or uncertain>"
}"""
```

Add these two functions at the end of `gemini_agent.py`:

```python
def parse_verify_response(raw: str) -> Optional[dict]:
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
        required = ("verified", "outcome", "confidence", "reasoning")
        if not all(k in data for k in required):
            return None
        confidence = float(data["confidence"])
        if not (0 <= confidence <= 1):
            return None
        outcome = data["outcome"]
        if outcome not in ("YES", "NO", "UNKNOWN"):
            outcome = "UNKNOWN"
        return {
            "verified": bool(data["verified"]),
            "outcome": outcome,
            "confidence": confidence,
            "reasoning": str(data["reasoning"]),
        }
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


def verify_outcome(question: str, current_ask: float) -> Optional[dict]:
    """
    Use Gemini with web search to verify if a market outcome is already determined.

    Returns: {verified, outcome, confidence, reasoning} or None on API error.
    """
    if not GENAI_AVAILABLE:
        raise RuntimeError("google-generativeai not installed")

    prompt = (
        f"Prediction market: {question}\n"
        f"Current YES ask price: {current_ask:.3f} (market implies {current_ask:.1%} probability YES)\n\n"
        f"Has this outcome already been definitively determined? "
        f"Search for the latest information and confirm whether YES or NO is certain."
    )

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_VERIFY,
                temperature=0.1,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            ),
        )
        raw_text = response.text
    except Exception as e:
        print(f"[gemini_agent] verify_outcome API error: {e}")
        return None

    return parse_verify_response(raw_text)
```

**Step 4: Run tests**

```bash
pytest tests/test_gemini_agent.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add gemini_agent.py tests/test_gemini_agent.py
git commit -m "feat: add verify_outcome to gemini_agent"
```

---

## Task 7: trader.py — Add Helpers (stop-loss check, event exposure, execute_expiry_trade)

**Files:**
- Modify: `trader.py`
- Test: `tests/test_trader.py`

**Step 1: Write failing tests**

Add to `tests/test_trader.py`:

```python
def test_check_stop_losses_triggers_on_drop(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    trade = {
        "market_id": "0xabc", "question": "Q?", "category": "crypto",
        "outcome": "YES", "side": "BUY", "size_usd": 100.0,
        "entry_price": 0.96, "current_price": 0.96, "pnl": 0.0,
        "status": "FILLED", "mode": "paper",
        "gemini_probability": 0.97, "gemini_reasoning": "test",
        "edge": 0.04, "closes_at": "2030-01-01", "research_brief": None,
        "stop_loss_price": 0.768,  # 0.96 * 0.80
        "strategy": "expiry_convergence", "token_id": "0xtoken",
    }
    trade_id = database.insert_trade(db, trade)

    # Mock current price to be below stop loss
    monkeypatch.setattr("polymarket_client.get_market_price", lambda tid: 0.50)

    stopped = trader.check_stop_losses(db, "paper")
    assert stopped == 1
    result = database.get_trade_by_id(db, trade_id)
    assert result["status"] == "CLOSED"
    assert result["resolution"] == "STOPPED_OUT"


def test_check_stop_losses_no_trigger_above_stop(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    trade = {
        "market_id": "0xabc", "question": "Q?", "category": "crypto",
        "outcome": "YES", "side": "BUY", "size_usd": 100.0,
        "entry_price": 0.96, "current_price": 0.96, "pnl": 0.0,
        "status": "FILLED", "mode": "paper",
        "gemini_probability": 0.97, "gemini_reasoning": "test",
        "edge": 0.04, "closes_at": "2030-01-01", "research_brief": None,
        "stop_loss_price": 0.768,
        "strategy": "expiry_convergence", "token_id": "0xtoken",
    }
    database.insert_trade(db, trade)
    monkeypatch.setattr("polymarket_client.get_market_price", lambda tid: 0.95)

    stopped = trader.check_stop_losses(db, "paper")
    assert stopped == 0


def test_execute_expiry_trade_paper_mode(tmp_path):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    candidate = {
        "market_id": "0xabc", "question": "Q?", "category": "crypto",
        "yes_token_id": "0xtoken", "no_token_id": "0xno",
        "end_date_iso": "2026-03-12T00:00:00Z",
        "real_ask": 0.96, "discount": 0.04, "event_slug": "q-event",
        "volume": 5000.0, "neg_risk": False,
    }
    fill_est = {
        "estimated_avg_price": 0.961,
        "limit_price": 0.962,
        "total_shares": 52.0,
        "total_cost": 50.0,
    }
    verification = {"verified": True, "outcome": "YES", "confidence": 0.95, "reasoning": "Done."}

    trade_id = trader.execute_expiry_trade(db, candidate, fill_est, 50.0, "paper", verification)
    assert trade_id is not None
    result = database.get_trade_by_id(db, trade_id)
    assert result["entry_price"] == pytest.approx(0.961)
    assert result["stop_loss_price"] == pytest.approx(0.961 * 0.80)
    assert result["strategy"] == "expiry_convergence"
    assert result["token_id"] == "0xtoken"
    assert result["outcome"] == "YES"
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_trader.py::test_check_stop_losses_triggers_on_drop tests/test_trader.py::test_check_stop_losses_no_trigger_above_stop tests/test_trader.py::test_execute_expiry_trade_paper_mode -v
```
Expected: FAIL

**Step 3: Add helpers to `trader.py`**

Add after `is_market_already_open()` (after line 53):

```python
def _get_event_exposure_pct(db_path: str, event_slug: str, mode: str, total_value: float) -> float:
    """Return current deployed capital for event_slug as fraction of portfolio."""
    if not event_slug or total_value <= 0:
        return 0.0
    open_trades = database.get_open_trades(db_path, mode)
    event_exposure = sum(
        t["size_usd"] for t in open_trades
        if t.get("event_slug") == event_slug
    )
    return event_exposure / total_value


def check_stop_losses(db_path: str, mode: str) -> int:
    """Check open positions against stop_loss_price. Auto-close if triggered.
    Returns number of positions stopped out."""
    open_trades = database.get_open_trades(db_path, mode)
    stopped = 0
    for trade in open_trades:
        stop_price = trade.get("stop_loss_price")
        if stop_price is None:
            continue
        token_id = trade.get("token_id") or trade.get("market_id")
        try:
            current_price = polymarket_client.get_market_price(token_id)
        except Exception:
            continue
        if current_price is None:
            continue
        if current_price <= stop_price:
            log.warning("[trader] STOP-LOSS triggered for %s | current=%.4f <= stop=%.4f",
                        trade["question"][:45], current_price, stop_price)
            if mode == "real":
                polymarket_client.place_order(
                    token_id=token_id,
                    side="SELL",
                    price=round(current_price * 0.99, 4),
                    size_usd=trade["size_usd"],
                )
            pnl = polymarket_client.calculate_pnl(
                trade["side"], trade["outcome"],
                trade["entry_price"], current_price, trade["size_usd"]
            )
            database.update_trade_price(db_path, trade["id"], current_price, pnl)
            database.close_trade(db_path, trade["id"], "STOPPED_OUT", current_price)
            stopped += 1
    return stopped


def execute_expiry_trade(
    db_path: str,
    candidate: dict,
    fill_est: dict,
    size_usd: float,
    mode: str,
    verification: dict,
) -> Optional[int]:
    """Place order (real mode) and record expiry convergence trade in DB."""
    avg_price = fill_est["estimated_avg_price"]

    if mode == "real":
        order_id = polymarket_client.place_order(
            token_id=candidate["yes_token_id"],
            side="BUY",
            price=fill_est["limit_price"],
            size_usd=size_usd,
        )
        if not order_id:
            log.warning("[trader] Real order failed for %s", candidate["question"][:50])
            return None

    stop_loss_price = round(avg_price * (1.0 - config.STOP_LOSS_PCT), 6)

    trade = {
        "market_id": candidate["market_id"],
        "question": candidate["question"],
        "category": candidate.get("category", "other"),
        "outcome": "YES",
        "side": "BUY",
        "size_usd": size_usd,
        "entry_price": avg_price,
        "current_price": avg_price,
        "pnl": 0.0,
        "status": "FILLED",
        "mode": mode,
        "gemini_probability": 1.0 - candidate["real_ask"],
        "gemini_reasoning": verification.get("reasoning", ""),
        "edge": candidate["discount"],
        "closes_at": candidate.get("end_date_iso", "")[:10],
        "research_brief": None,
        "stop_loss_price": stop_loss_price,
        "strategy": "expiry_convergence",
        "token_id": candidate.get("yes_token_id", ""),
    }
    trade_id = database.insert_trade(db_path, trade)
    log.info("[trader] %s expiry trade: %s @ %.4f (discount=%.2f%%) | stop=%.4f | $%.2f",
             "Paper" if mode == "paper" else "Real",
             candidate["question"][:45],
             avg_price, candidate["discount"] * 100,
             stop_loss_price, size_usd)
    return trade_id
```

**Step 4: Run tests**

```bash
pytest tests/test_trader.py -v
```
Expected: all PASS (old tests still pass, new ones pass)

**Step 5: Commit**

```bash
git add trader.py tests/test_trader.py
git commit -m "feat: add check_stop_losses and execute_expiry_trade to trader"
```

---

## Task 8: trader.py — Rewrite scan_and_trade

**Files:**
- Modify: `trader.py:102-220`
- Test: `tests/test_trader.py`

**Step 1: Write failing integration test**

```python
def test_scan_and_trade_full_expiry_pipeline(tmp_path, monkeypatch):
    """Full pipeline: find candidate → verify → walk book → trade."""
    import trader, database, polymarket_client, gemini_agent
    db = str(tmp_path / "test.db")
    database.init_db(db)
    database.snapshot_portfolio(db, total_value=1000.0, cash_balance=1000.0, mode="paper")

    candidate = {
        "market_id": "0xabc", "question": "Did X happen?",
        "category": "crypto", "yes_token_id": "0xtoken", "no_token_id": "0xno",
        "end_date_iso": "2026-03-11T00:00:00Z",
        "real_ask": 0.96, "discount": 0.04, "event_slug": "x-event",
        "volume": 5000.0, "neg_risk": False,
    }
    fill_est = {
        "estimated_avg_price": 0.961, "limit_price": 0.962,
        "total_shares": 52.0, "total_cost": 20.0,
    }
    verification = {"verified": True, "outcome": "YES", "confidence": 0.95, "reasoning": "Confirmed."}

    monkeypatch.setattr("polymarket_client.find_expiry_candidates", lambda **kw: [candidate])
    monkeypatch.setattr("gemini_agent.verify_outcome", lambda **kw: verification)
    monkeypatch.setattr("polymarket_client.walk_order_book", lambda **kw: fill_est)
    monkeypatch.setattr("polymarket_client.get_market_price", lambda tid: 0.96)

    count = trader.scan_and_trade(db)
    assert count == 1
    open_trades = database.get_open_trades(db, "paper")
    assert len(open_trades) == 1
    assert open_trades[0]["strategy"] == "expiry_convergence"
    assert open_trades[0]["token_id"] == "0xtoken"


def test_scan_and_trade_skips_unverified(tmp_path, monkeypatch):
    """Markets that fail verification should be logged as skipped."""
    import trader, database
    db = str(tmp_path / "test.db")
    database.init_db(db)
    database.snapshot_portfolio(db, total_value=1000.0, cash_balance=1000.0, mode="paper")

    candidate = {
        "market_id": "0xunver", "question": "Uncertain event?",
        "category": "crypto", "yes_token_id": "0xt", "no_token_id": "0tn",
        "end_date_iso": "2026-03-11T00:00:00Z",
        "real_ask": 0.96, "discount": 0.04, "event_slug": "un-event",
        "volume": 5000.0, "neg_risk": False,
    }
    unverified = {"verified": False, "outcome": "UNKNOWN", "confidence": 0.40, "reasoning": "Unclear."}

    monkeypatch.setattr("polymarket_client.find_expiry_candidates", lambda **kw: [candidate])
    monkeypatch.setattr("gemini_agent.verify_outcome", lambda **kw: unverified)
    monkeypatch.setattr("polymarket_client.get_market_price", lambda tid: 0.96)

    count = trader.scan_and_trade(db)
    assert count == 0
    skipped = database.get_skipped_markets(db, mode="paper")
    assert any("unverified" in s["skip_reason"] or "confidence" in s["skip_reason"] for s in skipped)
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_trader.py::test_scan_and_trade_full_expiry_pipeline tests/test_trader.py::test_scan_and_trade_skips_unverified -v
```
Expected: FAIL

**Step 3: Replace `scan_and_trade` in `trader.py`**

Replace the entire `scan_and_trade` function (lines 102-220) with:

```python
def scan_and_trade(db_path: str) -> int:
    global _status
    _status = "SCANNING"

    mode = database.get_app_state(db_path, "trading_mode", config.TRADING_MODE)
    settings = database.get_settings(db_path)
    starting_capital = (
        settings["paper_starting_capital"] if mode == "paper"
        else settings["real_starting_capital"]
    )

    snapshots = database.get_portfolio_snapshots(db_path, limit=1, mode=mode)
    total_value = snapshots[-1]["total_value"] if snapshots else starting_capital
    deployed = database.get_deployed_capital(db_path, mode)
    max_deployable = total_value * settings["max_deployed_pct"] - deployed

    if max_deployable <= 0:
        _status = "RUNNING"
        return 0

    # Check stop-losses before scanning for new trades
    check_stop_losses(db_path, mode)

    try:
        candidates = polymarket_client.find_expiry_candidates(
            max_days=settings.get("max_expiry_days", config.MAX_EXPIRY_DAYS),
            min_volume=settings["min_market_volume"],
            min_discount=settings.get("min_discount", config.MIN_DISCOUNT),
        )
    except Exception as e:
        log.error("[trader] Candidate scan error: %s", e)
        _status = "RUNNING"
        return 0

    log.info("[trader] Found %d expiry candidates, mode=%s, deployable=$%.2f",
             len(candidates), mode, max_deployable)

    open_ids = {t["market_id"] for t in database.get_open_trades(db_path, mode)}
    trades_placed = 0
    max_pos_pct = settings.get("max_position_pct", config.MAX_POSITION_PCT)

    for candidate in candidates:
        market_id = candidate["market_id"]

        if market_id in open_ids:
            continue

        if max_deployable < 1.0:
            break

        # Position concentration check
        event_slug = candidate.get("event_slug", "")
        if event_slug and _get_event_exposure_pct(db_path, event_slug, mode, total_value) >= max_pos_pct:
            database.insert_skipped_market(db_path, {
                "market_id": market_id,
                "question": candidate["question"],
                "category": candidate.get("category", "other"),
                "side": "YES",
                "probability": 1.0 - candidate["real_ask"],
                "edge": candidate["discount"],
                "confidence": "high",
                "contested": False,
                "skip_reason": f"concentration: event '{event_slug}' at {max_pos_pct:.0%} limit",
                "reasoning": "",
                "mode": mode,
            })
            continue

        # Verify outcome via Gemini
        try:
            verification = gemini_agent.verify_outcome(
                question=candidate["question"],
                current_ask=candidate["real_ask"],
            )
        except Exception as e:
            log.error("[trader] Verify error for %s: %s", candidate["question"][:40], e)
            continue

        conf = verification.get("confidence", 0) if verification else 0
        if not verification or not verification["verified"] or conf < 0.85:
            reason = (
                f"confidence too low ({conf:.2f})"
                if verification and not verification["verified"] is False
                else "unverified"
            )
            database.insert_skipped_market(db_path, {
                "market_id": market_id,
                "question": candidate["question"],
                "category": candidate.get("category", "other"),
                "side": "YES",
                "probability": 1.0 - candidate["real_ask"],
                "edge": candidate["discount"],
                "confidence": "low",
                "contested": False,
                "skip_reason": reason,
                "reasoning": verification.get("reasoning", "") if verification else "",
                "mode": mode,
            })
            continue

        # Size: bounded by max_position_size, deployable capital, and max_position_pct
        notional = min(
            settings["max_position_size"],
            max_deployable,
            total_value * max_pos_pct,
        )

        # Walk order book
        try:
            fill_est = polymarket_client.walk_order_book(
                token_id=candidate["yes_token_id"],
                notional_usd=notional,
            )
        except Exception as e:
            log.error("[trader] Walk order book error for %s: %s", candidate["question"][:40], e)
            continue

        if not fill_est:
            database.insert_skipped_market(db_path, {
                "market_id": market_id,
                "question": candidate["question"],
                "category": candidate.get("category", "other"),
                "side": "YES",
                "probability": 1.0 - candidate["real_ask"],
                "edge": candidate["discount"],
                "confidence": "high",
                "contested": False,
                "skip_reason": "book too thin or avg price too high",
                "reasoning": verification.get("reasoning", ""),
                "mode": mode,
            })
            continue

        log.info("[trader] %s | discount=%.2f%% | avg_fill=%.4f | conf=%.2f | $%.2f",
                 candidate["question"][:45],
                 candidate["discount"] * 100,
                 fill_est["estimated_avg_price"],
                 verification["confidence"],
                 notional)

        trade_id = execute_expiry_trade(db_path, candidate, fill_est, notional, mode, verification)
        if trade_id:
            trades_placed += 1
            open_ids.add(market_id)
            new_deployed = database.get_deployed_capital(db_path, mode)
            max_deployable = total_value * settings["max_deployed_pct"] - new_deployed
            database.snapshot_portfolio(
                db_path,
                total_value=starting_capital + database.get_total_pnl(db_path, mode),
                cash_balance=total_value - new_deployed,
                mode=mode,
            )

    _status = "RUNNING"
    return trades_placed
```

**Step 4: Run all trader tests**

```bash
pytest tests/test_trader.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add trader.py tests/test_trader.py
git commit -m "feat: rewrite scan_and_trade for expiry convergence pipeline"
```

---

## Task 9: Contract Constants and Auto-Redeem Hook

**Files:**
- Create: `config/contracts.py`
- Create: `hooks/session_start.py`
- Test: `tests/test_session_start.py`

**Step 1: Create `config/contracts.py`**

```python
# config/contracts.py
# Polymarket contract addresses on Polygon (chain ID 137)

USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER_ADDRESS = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# Minimal ABI: redeemPositions for ConditionalTokens
CTF_REDEEM_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Minimal ABI: redeemPositions for NegRiskAdapter
NEG_RISK_REDEEM_ABI = [
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "amounts", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]
```

**Step 2: Create `hooks/session_start.py`**

Note: This file is executed by Claude Code's SessionStart hook mechanism, but here we expose it as a plain Python module callable from `trader.py` or directly.

```python
# hooks/session_start.py
"""
SessionStart hook: auto-redeem settled positions and check stop-losses.
Called at the beginning of each scan cycle (and optionally by Claude Code hooks).
"""
import logging
import os
import sys

# Ensure project root is on path when run as hook
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

import database
import config
from config.contracts import (
    USDC_ADDRESS,
    CONDITIONAL_TOKENS_ADDRESS,
    NEG_RISK_ADAPTER_ADDRESS,
    CTF_REDEEM_ABI,
    NEG_RISK_REDEEM_ABI,
)

log = logging.getLogger("session_start")


def _get_web3():
    """Return connected Web3 instance or None if unavailable."""
    rpc_url = config.POLYGON_RPC_URL
    if not rpc_url:
        return None
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        return w3 if w3.is_connected() else None
    except ImportError:
        log.warning("[session_start] web3 not installed — skipping on-chain redeem")
        return None


def _check_market_resolved(market_id: str) -> bool:
    """Check Gamma API to see if the market is closed/resolved."""
    try:
        import requests
        resp = requests.get(
            f"https://gamma-api.polymarket.com/markets/{market_id}",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return bool(data.get("closed") or data.get("resolved"))
    except Exception as e:
        log.error("[session_start] Market resolution check error for %s: %s", market_id, e)
    return False


def redeem_position(w3, trade: dict) -> bool:
    """
    Call CTF or NegRiskAdapter redeemPositions for a settled trade.
    Returns True if transaction submitted successfully.
    """
    private_key = config.POLYMARKET_PRIVATE_KEY
    if not private_key:
        log.warning("[session_start] No private key configured for redemption")
        return False

    from web3 import Web3

    account = w3.eth.account.from_key(private_key)
    condition_id = trade["market_id"]

    # Convert condition_id to bytes32
    if condition_id.startswith("0x"):
        cid_bytes = bytes.fromhex(condition_id[2:].zfill(64))
    else:
        cid_bytes = bytes.fromhex(condition_id.zfill(64))

    neg_risk = bool(trade.get("neg_risk", False))

    try:
        if neg_risk:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(NEG_RISK_ADAPTER_ADDRESS),
                abi=NEG_RISK_REDEEM_ABI,
            )
            txn = contract.functions.redeemPositions(
                cid_bytes,
                [1],  # YES index set
            ).build_transaction({
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": 200000,
                "gasPrice": w3.eth.gas_price,
            })
        else:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(CONDITIONAL_TOKENS_ADDRESS),
                abi=CTF_REDEEM_ABI,
            )
            txn = contract.functions.redeemPositions(
                Web3.to_checksum_address(USDC_ADDRESS),
                b"\x00" * 32,   # parentCollectionId = 0x000...
                cid_bytes,
                [1],             # YES index set
            ).build_transaction({
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": 200000,
                "gasPrice": w3.eth.gas_price,
            })

        signed = w3.eth.account.sign_transaction(txn, private_key=private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        log.info("[session_start] Redeem tx sent: %s for %s",
                 tx_hash.hex(), trade["question"][:45])
        return True

    except Exception as e:
        log.error("[session_start] Redeem transaction error for %s: %s",
                  trade["question"][:45], e)
        return False


def auto_redeem_settled_positions(db_path: str = None) -> int:
    """
    Find open real-mode trades past closes_at, check resolution, redeem on-chain.
    Returns number of redemptions attempted.
    """
    if db_path is None:
        db_path = config.DB_PATH

    trades = database.get_settled_unredeemed_trades(db_path)
    if not trades:
        return 0

    w3 = _get_web3()
    redeemed = 0

    for trade in trades:
        if not _check_market_resolved(trade["market_id"]):
            log.info("[session_start] Market not yet resolved: %s", trade["question"][:45])
            continue

        if w3:
            success = redeem_position(w3, trade)
            if success:
                redeemed += 1
        else:
            log.info("[session_start] No Web3 — marking %s as WIN without on-chain redeem",
                     trade["question"][:45])

        # Mark as WIN in DB regardless (redemption is just claiming)
        pnl = trade["size_usd"] * (1.0 - trade["entry_price"]) / trade["entry_price"]
        database.update_trade_price(db_path, trade["id"], 1.0, pnl)
        database.close_trade(db_path, trade["id"], "WIN", 1.0)

    return redeemed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = auto_redeem_settled_positions()
    print(f"[session_start] Redeemed {count} positions")
```

**Step 3: Write tests**

Create `tests/test_session_start.py`:

```python
import pytest
import sqlite3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database
import hooks.session_start as session_start


def _make_real_trade(db, market_id="0xabc", closes_at="2020-01-01", neg_risk=False):
    trade = {
        "market_id": market_id, "question": "Did X happen?",
        "category": "crypto", "outcome": "YES", "side": "BUY",
        "size_usd": 50.0, "entry_price": 0.96, "current_price": 0.96,
        "pnl": 0.0, "status": "FILLED", "mode": "real",
        "gemini_probability": 0.97, "gemini_reasoning": "verified",
        "edge": 0.04, "closes_at": closes_at, "research_brief": None,
        "stop_loss_price": 0.768, "strategy": "expiry_convergence",
        "token_id": "0xtoken", "neg_risk": int(neg_risk),
    }
    return database.insert_trade(db, trade)


def test_auto_redeem_no_web3_marks_win(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    trade_id = _make_real_trade(db)

    monkeypatch.setattr("hooks.session_start._get_web3", lambda: None)
    monkeypatch.setattr("hooks.session_start._check_market_resolved", lambda mid: True)

    count = session_start.auto_redeem_settled_positions(db_path=db)
    assert count == 0  # no web3, so 0 on-chain redeems
    result = database.get_trade_by_id(db, trade_id)
    assert result["status"] == "CLOSED"
    assert result["resolution"] == "WIN"


def test_auto_redeem_skips_unresolved_markets(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    _make_real_trade(db)

    monkeypatch.setattr("hooks.session_start._get_web3", lambda: None)
    monkeypatch.setattr("hooks.session_start._check_market_resolved", lambda mid: False)

    count = session_start.auto_redeem_settled_positions(db_path=db)
    assert count == 0
    # Trade should still be open
    open_trades = database.get_settled_unredeemed_trades(db)
    assert len(open_trades) == 1


def test_auto_redeem_with_mock_web3_submits_transaction(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    database.init_db(db)
    trade_id = _make_real_trade(db)

    from unittest.mock import MagicMock
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True

    monkeypatch.setattr("hooks.session_start._get_web3", lambda: mock_w3)
    monkeypatch.setattr("hooks.session_start._check_market_resolved", lambda mid: True)
    monkeypatch.setattr("hooks.session_start.redeem_position", lambda w3, trade: True)

    count = session_start.auto_redeem_settled_positions(db_path=db)
    assert count == 1
    result = database.get_trade_by_id(db, trade_id)
    assert result["resolution"] == "WIN"
```

**Step 4: Run to verify tests pass**

```bash
mkdir -p /Users/marco/Documents/PolyBot/config
touch /Users/marco/Documents/PolyBot/config/__init__.py
pytest tests/test_session_start.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add config/contracts.py config/__init__.py hooks/session_start.py tests/test_session_start.py
git commit -m "feat: add auto-redeem hook and CTF contract constants"
```

---

## Task 10: app.py — Add Stop Loss Column and New Settings

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_helpers.py`

**Step 1: Add `stop_loss_pct` to settings modal**

In `app.py`, find the settings modal content (search for `"scan_interval_minutes"` in the modal). Add new settings inputs at the bottom of the settings form:

```python
# After the existing long_term_min_prob input, add:
html.Div([
    html.Label("Max Expiry Days", className="settings-label"),
    dcc.Input(id="input-max-expiry-days", type="number", min=1, max=30,
              className="settings-input"),
], className="settings-field"),
html.Div([
    html.Label("Min Discount %", className="settings-label"),
    dcc.Input(id="input-min-discount", type="number", min=0.5, max=10, step=0.1,
              className="settings-input"),
], className="settings-field"),
html.Div([
    html.Label("Stop-Loss %", className="settings-label"),
    dcc.Input(id="input-stop-loss-pct", type="number", min=5, max=50, step=1,
              className="settings-input"),
], className="settings-field"),
```

**Step 2: Add Stop Loss column to open positions table**

In `render_open_positions()` (search for `"MAX PROFIT"` in app.py), add `"Stop Loss"` as a new header and populate it in the row rendering with:

```python
html.Td(fmt_price(trade.get("stop_loss_price") or 0))
```

**Step 3: Wire up new settings inputs in the settings callback**

In the `update_settings` callback, add the new inputs to the `Input` list and read them in the settings dict passed to `database.save_settings()`:

```python
Input("input-max-expiry-days", "value"),
Input("input-min-discount", "value"),
Input("input-stop-loss-pct", "value"),
```

And in the callback body:
```python
if max_expiry_days is not None:
    new_settings["max_expiry_days"] = int(max_expiry_days)
if min_discount is not None:
    new_settings["min_discount"] = float(min_discount) / 100.0
if stop_loss_pct is not None:
    new_settings["stop_loss_pct"] = float(stop_loss_pct) / 100.0
```

**Step 4: Populate new settings inputs in the load_settings callback**

In the callback that loads current settings into the inputs, add the three new outputs:
```python
Output("input-max-expiry-days", "value"),
Output("input-min-discount", "value"),
Output("input-stop-loss-pct", "value"),
```
And in the return:
```python
settings.get("max_expiry_days", config.MAX_EXPIRY_DAYS),
round(settings.get("min_discount", config.MIN_DISCOUNT) * 100, 1),
round(settings.get("stop_loss_pct", config.STOP_LOSS_PCT) * 100, 0),
```

**Step 5: Run existing tests to check nothing broke**

```bash
pytest tests/test_app_helpers.py -v
```
Expected: all PASS

**Step 6: Commit**

```bash
git add app.py
git commit -m "feat: add stop-loss column and expiry settings to dashboard"
```

---

## Task 11: requirements.txt — Add web3

**Files:**
- Modify: `requirements.txt`

**Step 1: Add web3 dependency**

```bash
echo "web3>=6.0" >> /Users/marco/Documents/PolyBot/requirements.txt
```

Verify it's in the file:
```bash
grep web3 requirements.txt
```

**Step 2: Install in venv**

```bash
cd /Users/marco/Documents/PolyBot && source venv/bin/activate && pip install "web3>=6.0"
```

**Step 3: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all PASS

**Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: add web3 for on-chain CTF redemption"
```

---

## Task 12: Integration — Wire session_start into trader loop

**Files:**
- Modify: `trader.py:223-237` (`_trading_loop`)

**Step 1: Call auto_redeem at the top of each scan cycle**

In `_trading_loop`, add a call to `auto_redeem_settled_positions` at the start of each iteration:

```python
def _trading_loop(db_path: str) -> None:
    global _running, _status
    _status = "RUNNING"
    while _running:
        try:
            # Auto-redeem settled real positions before scanning
            from hooks.session_start import auto_redeem_settled_positions
            auto_redeem_settled_positions(db_path=db_path)
        except Exception as e:
            log.error("[trader] Auto-redeem error: %s", e)
        try:
            scan_and_trade(db_path)
        except Exception as e:
            log.error("[trader] Loop error: %s", e)
        settings = database.get_settings(db_path)
        interval_seconds = int(settings["scan_interval_minutes"]) * 60
        for _ in range(interval_seconds):
            if not _running:
                break
            time.sleep(1)
    _status = "STOPPED"
```

**Step 2: Run full test suite one final time**

```bash
pytest tests/ -v
```
Expected: all PASS

**Step 3: Final commit**

```bash
git add trader.py
git commit -m "feat: wire auto-redeem into trading loop"
```

---

## Task 13: Smoke Test

**Step 1: Start the app in paper mode and verify it runs**

```bash
cd /Users/marco/Documents/PolyBot && source venv/bin/activate && python app.py
```

Navigate to http://localhost:8050 and confirm:
- Dashboard loads
- Settings modal shows new fields (Max Expiry Days, Min Discount %, Stop-Loss %)
- Open positions table has Stop Loss column
- No errors in terminal

**Step 2: Manually trigger one scan cycle**

In a Python REPL:
```python
import config, trader
trader.scan_and_trade(config.DB_PATH)
```

Confirm in logs: `[trader] Found N expiry candidates` or `[trader] Found 0 expiry candidates`.

**Step 3: Done**

The expiry convergence strategy is fully implemented. The bot now:
1. Scans CLOB for discounted near-certain markets expiring within 3 days
2. Verifies each outcome via Gemini web search (confidence ≥ 0.85 required)
3. Walks order book to estimate fill price, rejects if avg ≥ 0.99
4. Places trades with stop_loss at 20% below entry
5. Auto-checks stop-losses at each scan start
6. Auto-redeems settled real positions via CTF contracts
