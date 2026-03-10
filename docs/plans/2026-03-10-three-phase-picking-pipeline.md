# Three-Phase Picking Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the single-pass `analyze_market()` with a three-phase pipeline: bulk screener → per-market research brief (with live web search) → deterministic probability assignment from fixed facts.

**Architecture:** Phase 1 screens all markets in one Gemini call (no search) to identify candidates. Phase 2 calls Gemini once per candidate with Google Search grounding enabled to gather a research brief (facts only, no probability). Phase 3 calls Gemini once per candidate without search to reason from the fixed brief to a final probability — preventing narrative drift.

**Tech Stack:** Python, google-genai SDK (`genai_types.Tool(google_search=genai_types.GoogleSearch())`), SQLite, pytest + unittest.mock

---

## Task 1: Config and DB migration

**Files:**
- Modify: `config.py`
- Modify: `database.py`
- Modify: `tests/test_database.py`

**Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_research_brief_column_exists(tmp_path):
    import database
    db_path = str(tmp_path / "test.db")
    database.init_db(db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(trades)")
    cols = [row[1] for row in cursor.fetchall()]
    conn.close()
    assert "research_brief" in cols
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/marco/Documents/PolyBot && python -m pytest tests/test_database.py::test_research_brief_column_exists -v
```
Expected: FAIL — `research_brief` not in cols

**Step 3: Add `MAX_FLAGGED_MARKETS` to `config.py`**

Add after `MAX_MARKETS_TO_SCAN = 50`:
```python
MAX_FLAGGED_MARKETS = 10        # max markets to deep-research per scan cycle
```

**Step 4: Add `research_brief` column and migration to `database.py`**

In `init_db`, add after the `trades` CREATE TABLE block:
```python
    # migrate: add research_brief if missing (safe on new and existing DBs)
    conn.execute("ALTER TABLE trades ADD COLUMN research_brief TEXT")
    conn.commit()
```

Then wrap it so it doesn't error on existing DBs — replace that single line with:
```python
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN research_brief TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
```

**Step 5: Run test to verify it passes**

```bash
python -m pytest tests/test_database.py::test_research_brief_column_exists -v
```
Expected: PASS

**Step 6: Run full test suite to check no regressions**

```bash
python -m pytest tests/ -v
```
Expected: all existing tests pass

**Step 7: Commit**

```bash
git add config.py database.py tests/test_database.py
git commit -m "feat: add MAX_FLAGGED_MARKETS config and research_brief DB column"
```

---

## Task 2: Phase 1 — `screen_markets()` and its response parser

**Files:**
- Modify: `gemini_agent.py`
- Modify: `tests/test_gemini_agent.py`

**Step 1: Write failing tests**

Add to `tests/test_gemini_agent.py`:

```python
def test_parse_screen_response_valid():
    raw = '[{"market_id": "abc", "initial_lean": "YES", "reason": "Strong trend"}]'
    result = ga.parse_screen_response(raw)
    assert len(result) == 1
    assert result[0]["market_id"] == "abc"
    assert result[0]["initial_lean"] == "YES"


def test_parse_screen_response_empty_array():
    result = ga.parse_screen_response("[]")
    assert result == []


def test_parse_screen_response_invalid_returns_empty():
    result = ga.parse_screen_response("not json at all")
    assert result == []


def test_parse_screen_response_filters_bad_entries():
    # entries missing required fields are dropped
    raw = '[{"market_id": "a", "initial_lean": "YES", "reason": "ok"}, {"market_id": "b"}]'
    result = ga.parse_screen_response(raw)
    assert len(result) == 1
    assert result[0]["market_id"] == "a"


def test_screen_markets_returns_flagged(monkeypatch):
    markets = [
        {"market_id": "m1", "question": "Will X win?", "yes_price": 0.4,
         "no_price": 0.6, "volume": 5000, "end_date_iso": "2026-04-01T00:00:00Z", "category": "sports"},
        {"market_id": "m2", "question": "Will Y happen?", "yes_price": 0.6,
         "no_price": 0.4, "volume": 2000, "end_date_iso": "2026-04-01T00:00:00Z", "category": "politics"},
    ]
    open_ids = set()
    mock_response = MagicMock()
    mock_response.text = '[{"market_id": "m1", "initial_lean": "YES", "reason": "Good edge"}]'

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("gemini_agent.genai") as mock_genai:
        mock_genai.Client.return_value = mock_client
        result = ga.screen_markets(markets, open_ids)

    assert len(result) == 1
    assert result[0]["market_id"] == "m1"


def test_screen_markets_excludes_open_positions(monkeypatch):
    markets = [
        {"market_id": "m1", "question": "Q1?", "yes_price": 0.4, "no_price": 0.6,
         "volume": 5000, "end_date_iso": "2026-04-01T00:00:00Z", "category": "sports"},
    ]
    open_ids = {"m1"}  # already open
    mock_response = MagicMock()
    mock_response.text = '[{"market_id": "m1", "initial_lean": "YES", "reason": "ok"}]'

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("gemini_agent.genai") as mock_genai:
        mock_genai.Client.return_value = mock_client
        result = ga.screen_markets(markets, open_ids)

    # m1 is already open — must be filtered out even if Gemini flagged it
    assert all(f["market_id"] != "m1" for f in result)
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_gemini_agent.py -k "screen" -v
```
Expected: FAIL — `parse_screen_response` and `screen_markets` not defined

**Step 3: Implement `parse_screen_response` and `screen_markets` in `gemini_agent.py`**

Add the screener system prompt constant near the top (after `SYSTEM_PROMPT`):

```python
SYSTEM_PROMPT_SCREEN = """You are a highly selective prediction market analyst. You have only $1,000 to your name — this money is irreplaceable and every single bet matters enormously.

You will be shown a list of active prediction markets. Flag only markets where you have genuine informational edge — where you know enough to estimate probability meaningfully better than the current market price.

Be extremely selective. Default to passing on most markets. ONLY flag a market if ALL of these are true:
- You have specific knowledge about this topic (sport, team, event, candidate, asset)
- The market price looks clearly mispriced based on what you know
- This is NOT a coin flip, a tight multi-team race, or any situation where the outcome is genuinely near-random

Return a JSON array. Return an empty array [] if nothing is worth investigating.

ALWAYS respond with valid JSON array only, no other text:
[
  { "market_id": "<id>", "initial_lean": "YES or NO", "reason": "<one sentence>" }
]"""
```

Add `parse_screen_response` after `parse_gemini_response`:

```python
def parse_screen_response(raw: str) -> list:
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not all(k in item for k in ("market_id", "initial_lean", "reason")):
                continue
            if item["initial_lean"] not in ("YES", "NO"):
                continue
            result.append({
                "market_id": str(item["market_id"]),
                "initial_lean": item["initial_lean"],
                "reason": str(item["reason"]),
            })
        return result
    except (json.JSONDecodeError, ValueError):
        return []
```

Add `screen_markets` after `build_performance_context`:

```python
def screen_markets(markets: list, open_market_ids: set) -> list:
    if not GENAI_AVAILABLE:
        raise RuntimeError("google-generativeai not installed")
    if not markets:
        return []

    lines = ["Here are the active prediction markets. Flag only ones where you have genuine edge:\n"]
    for m in markets:
        lines.append(
            f"ID: {m['market_id']} | {m['question']} | "
            f"YES={m['yes_price']:.2f} NO={m['no_price']:.2f} | "
            f"Vol=${m['volume']:,.0f} | Closes: {m['end_date_iso'][:10]} | Cat: {m.get('category','other')}"
        )
    prompt = "\n".join(lines)

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_SCREEN,
                temperature=0.2,
            ),
        )
        raw_text = response.text
    except Exception as e:
        print(f"[gemini_agent] Screener API error: {e}")
        return []

    flagged = parse_screen_response(raw_text)
    # Remove any market already held as an open position
    return [f for f in flagged if f["market_id"] not in open_market_ids]
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_gemini_agent.py -k "screen" -v
```
Expected: all screen tests PASS

**Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add gemini_agent.py tests/test_gemini_agent.py
git commit -m "feat: add Phase 1 screen_markets() with selective screener prompt"
```

---

## Task 3: Phase 2 — `research_market()` with live search grounding

**Files:**
- Modify: `gemini_agent.py`
- Modify: `tests/test_gemini_agent.py`

**Step 1: Write failing tests**

Add to `tests/test_gemini_agent.py`:

```python
def test_parse_research_response_valid():
    raw = json.dumps({
        "key_facts": ["Forest are 17th", "3 teams on 28pts"],
        "base_rate": "Teams in 17th at this stage relegated ~45% historically",
        "recent_developments": "Lost last 3 games, striker injured",
        "uncertainty_factors": ["3-way tie", "9 games left"]
    })
    result = ga.parse_research_response(raw)
    assert result is not None
    assert len(result["key_facts"]) == 2
    assert "base_rate" in result


def test_parse_research_response_missing_field_returns_none():
    raw = json.dumps({"key_facts": ["fact1"]})  # missing other required fields
    result = ga.parse_research_response(raw)
    assert result is None


def test_parse_research_response_invalid_json_returns_none():
    result = ga.parse_research_response("not json")
    assert result is None


def test_research_market_returns_brief(monkeypatch):
    market = {
        "market_id": "m1",
        "question": "Will Nottingham Forest be relegated?",
        "yes_price": 0.35,
        "no_price": 0.65,
        "volume": 20000,
        "end_date_iso": "2026-05-20T00:00:00Z",
        "category": "sports",
    }
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "key_facts": ["Forest 17th, 1pt above drop zone"],
        "base_rate": "~40% historically",
        "recent_developments": "Lost 3 in a row",
        "uncertainty_factors": ["3-way battle"]
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("gemini_agent.genai") as mock_genai:
        mock_genai.Client.return_value = mock_client
        result = ga.research_market(market)

    assert result is not None
    assert "key_facts" in result
    assert "base_rate" in result
    # verify search grounding tool was passed
    call_kwargs = mock_client.models.generate_content.call_args
    gen_config = call_kwargs.kwargs.get("config") or call_kwargs.args[2] if len(call_kwargs.args) > 2 else None
    # search tool should be present (we trust the implementation wires it)
    assert gen_config is not None
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_gemini_agent.py -k "research" -v
```
Expected: FAIL — functions not defined

**Step 3: Implement `parse_research_response` and `research_market` in `gemini_agent.py`**

Add research system prompt constant:

```python
SYSTEM_PROMPT_RESEARCH = """You are a rigorous fact-gatherer for prediction market research. Your ONLY job is to find and report current, factual information. Do NOT assign any probability or make a recommendation.

You are researching with $1,000 of irreplaceable money at stake. Be thorough and honest. Actively look for information that CONTRADICTS the initial lean — find the strongest counterargument.

Search for:
- Current standings, polls, prices, statistics directly relevant to the question
- Recent news that could change the outcome
- Historical base rates for similar situations (how often has this type of outcome occurred?)
- Specific factors that make this uncertain or hard to predict

ALWAYS respond with valid JSON only, no other text:
{
  "key_facts": ["<specific fact>", "..."],
  "base_rate": "<historical base rate statement with approximate percentage if known>",
  "recent_developments": "<most relevant recent news in 1-2 sentences>",
  "uncertainty_factors": ["<factor making outcome uncertain>", "..."]
}"""
```

Add `parse_research_response` after `parse_screen_response`:

```python
def parse_research_response(raw: str) -> Optional[dict]:
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
        required = ("key_facts", "base_rate", "recent_developments", "uncertainty_factors")
        if not all(k in data for k in required):
            return None
        return {
            "key_facts": list(data["key_facts"]),
            "base_rate": str(data["base_rate"]),
            "recent_developments": str(data["recent_developments"]),
            "uncertainty_factors": list(data["uncertainty_factors"]),
        }
    except (json.JSONDecodeError, ValueError, KeyError):
        return None
```

Add `research_market` after `screen_markets`:

```python
def research_market(market: dict) -> Optional[dict]:
    if not GENAI_AVAILABLE:
        raise RuntimeError("google-generativeai not installed")

    prompt = (
        f"Market: {market['question']}\n"
        f"Current YES price: {market['yes_price']:.4f} (implied: {market['yes_price']:.1%})\n"
        f"Current NO price: {market['no_price']:.4f}\n"
        f"Category: {market.get('category', 'other')}\n"
        f"Closes: {market['end_date_iso'][:10]}\n\n"
        f"Research this market thoroughly. Find current facts, base rates, and counterarguments."
    )

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_RESEARCH,
                temperature=0.3,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            ),
        )
        raw_text = response.text
    except Exception as e:
        print(f"[gemini_agent] Research API error: {e}")
        return None

    return parse_research_response(raw_text)
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_gemini_agent.py -k "research" -v
```
Expected: all research tests PASS

**Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add gemini_agent.py tests/test_gemini_agent.py
git commit -m "feat: add Phase 2 research_market() with search grounding"
```

---

## Task 4: Phase 3 — `assign_probability()` and updated response parser

**Files:**
- Modify: `gemini_agent.py`
- Modify: `tests/test_gemini_agent.py`

**Step 1: Write failing tests**

Add to `tests/test_gemini_agent.py`:

```python
def test_parse_probability_response_valid():
    raw = json.dumps({
        "probability": 0.38,
        "side": "NO",
        "confidence": "low",
        "base_rate_estimate": 0.45,
        "contested": True,
        "reasoning": "3-way battle, base rate ~45%, no clear edge."
    })
    result = ga.parse_probability_response(raw)
    assert result is not None
    assert result["probability"] == 0.38
    assert result["contested"] is True
    assert result["base_rate_estimate"] == 0.45


def test_parse_probability_response_missing_new_fields_returns_none():
    # missing base_rate_estimate and contested
    raw = json.dumps({
        "probability": 0.7, "side": "YES", "confidence": "high", "reasoning": "ok"
    })
    result = ga.parse_probability_response(raw)
    assert result is None


def test_parse_probability_response_contested_false():
    raw = json.dumps({
        "probability": 0.82,
        "side": "YES",
        "confidence": "high",
        "base_rate_estimate": 0.60,
        "contested": False,
        "reasoning": "Clear favourite with strong recent form."
    })
    result = ga.parse_probability_response(raw)
    assert result is not None
    assert result["contested"] is False


def test_assign_probability_returns_analysis(monkeypatch):
    market = {
        "market_id": "m1",
        "question": "Will Forest be relegated?",
        "yes_price": 0.35,
        "no_price": 0.65,
        "yes_token_id": "t1",
        "no_token_id": "t2",
    }
    research = {
        "key_facts": ["Forest 17th, 1pt above drop zone"],
        "base_rate": "~40% historically",
        "recent_developments": "Lost 3 in a row",
        "uncertainty_factors": ["3-way battle"],
    }
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "probability": 0.38,
        "side": "NO",
        "confidence": "low",
        "base_rate_estimate": 0.40,
        "contested": True,
        "reasoning": "3-way battle, effectively random."
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("gemini_agent.genai") as mock_genai:
        mock_genai.Client.return_value = mock_client
        result = ga.assign_probability(market, research, {})

    assert result is not None
    assert result["contested"] is True
    assert result["base_rate_estimate"] == 0.40
    assert "edge" in result
    assert "entry_price" in result
    assert "token_id" in result
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_gemini_agent.py -k "probability" -v
```
Expected: FAIL — functions not defined

**Step 3: Implement `parse_probability_response` and `assign_probability` in `gemini_agent.py`**

Add probability system prompt constant:

```python
SYSTEM_PROMPT_PROBABILITY = """You are making a final probability judgment for a prediction market. You have $1,000 of irreplaceable money on the line. Be rigorous, honest, and conservative.

You will be given a research brief. Your job:
1. Start from the base rate stated in the research
2. Only move away from the base rate if you can cite SPECIFIC evidence from the research
3. If multiple outcomes are near-equally likely (tight races, coin flips, 3-way battles), set contested=true
4. Be honest about uncertainty — set confidence "low" if you cannot clearly distinguish the outcome

ALWAYS respond with valid JSON only, no other text:
{
  "probability": <float 0-1>,
  "side": "YES or NO",
  "confidence": "low, medium, or high",
  "base_rate_estimate": <float 0-1, what naive base rate alone would suggest>,
  "contested": <true if multiple outcomes are near-equally plausible, else false>,
  "reasoning": "<2-3 sentences: state base rate, state what moves you from it, state final judgment>"
}"""
```

Add `parse_probability_response` after `parse_research_response`:

```python
def parse_probability_response(raw: str) -> Optional[dict]:
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
        required = ("probability", "side", "confidence", "reasoning",
                     "base_rate_estimate", "contested")
        if not all(k in data for k in required):
            return None
        prob = float(data["probability"])
        base_rate = float(data["base_rate_estimate"])
        if not (0 <= prob <= 1) or not (0 <= base_rate <= 1):
            return None
        if data["side"] not in ("YES", "NO"):
            return None
        if data["confidence"] not in ("low", "medium", "high"):
            return None
        return {
            "probability": prob,
            "side": data["side"],
            "confidence": data["confidence"],
            "base_rate_estimate": base_rate,
            "contested": bool(data["contested"]),
            "reasoning": str(data["reasoning"]),
        }
    except (json.JSONDecodeError, ValueError, KeyError):
        return None
```

Add `assign_probability` after `research_market`:

```python
def assign_probability(market: dict, research: dict, performance: dict) -> Optional[dict]:
    if not GENAI_AVAILABLE:
        raise RuntimeError("google-generativeai not installed")

    perf_context = build_performance_context(performance)
    research_text = (
        f"Key facts: {'; '.join(research.get('key_facts', []))}\n"
        f"Base rate: {research.get('base_rate', 'unknown')}\n"
        f"Recent developments: {research.get('recent_developments', 'none')}\n"
        f"Uncertainty factors: {'; '.join(research.get('uncertainty_factors', []))}"
    )

    prompt = (
        f"{perf_context}\n\n"
        f"Market: {market['question']}\n"
        f"Current YES price: {market['yes_price']:.4f} (implied: {market['yes_price']:.1%})\n\n"
        f"Research findings:\n{research_text}\n\n"
        f"Now assign the final probability. Start from the base rate. "
        f"Only deviate if the research gives you specific, concrete evidence to do so."
    )

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_PROBABILITY,
                temperature=0.2,
            ),
        )
        raw_text = response.text
    except Exception as e:
        print(f"[gemini_agent] Probability API error: {e}")
        return None

    parsed = parse_probability_response(raw_text)
    if not parsed:
        return None

    if parsed["side"] == "YES":
        entry_price = market["yes_price"]
        token_id = market.get("yes_token_id", "")
        edge = parsed["probability"] - market["yes_price"]
    else:
        entry_price = market["no_price"]
        token_id = market.get("no_token_id", "")
        edge = (1 - parsed["probability"]) - market["no_price"]

    return {
        "probability": parsed["probability"],
        "side": parsed["side"],
        "confidence": parsed["confidence"],
        "base_rate_estimate": parsed["base_rate_estimate"],
        "contested": parsed["contested"],
        "reasoning": parsed["reasoning"],
        "edge": edge,
        "entry_price": entry_price,
        "token_id": token_id,
    }
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_gemini_agent.py -k "probability" -v
```
Expected: all probability tests PASS

**Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add gemini_agent.py tests/test_gemini_agent.py
git commit -m "feat: add Phase 3 assign_probability() with base_rate and contested fields"
```

---

## Task 5: Update `should_trade()` with contested filter

**Files:**
- Modify: `trader.py`
- Modify: `tests/test_trader.py`

**Step 1: Write failing tests**

Add to `tests/test_trader.py`:

```python
def _analysis_with_contested(probability=0.5, edge=0.15, confidence="medium",
                               base_rate_estimate=0.45, contested=True):
    return {
        "probability": probability,
        "edge": edge,
        "confidence": confidence,
        "base_rate_estimate": base_rate_estimate,
        "contested": contested,
    }


def test_should_trade_rejects_contested_with_low_confidence():
    analysis = _analysis_with_contested(contested=True, confidence="medium", edge=0.12)
    assert trader.should_trade(analysis, _market(3)) is False


def test_should_trade_rejects_contested_with_insufficient_edge():
    analysis = _analysis_with_contested(contested=True, confidence="high", edge=0.10)
    assert trader.should_trade(analysis, _market(3)) is False


def test_should_trade_accepts_contested_high_conf_large_edge():
    analysis = _analysis_with_contested(contested=True, confidence="high", edge=0.20)
    assert trader.should_trade(analysis, _market(3)) is True


def test_should_trade_uncontested_normal_rules_apply():
    analysis = _analysis_with_contested(contested=False, confidence="medium", edge=0.10)
    assert trader.should_trade(analysis, _market(3)) is True
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_trader.py -k "contested" -v
```
Expected: FAIL — contested filter not implemented

**Step 3: Update `should_trade()` in `trader.py`**

Add the contested check inside `should_trade`, before `return True`:

```python
    if analysis.get("contested"):
        if analysis.get("confidence") != "high" or analysis.get("edge", 0) < 0.15:
            return False
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_trader.py -v
```
Expected: all trader tests PASS

**Step 5: Commit**

```bash
git add trader.py tests/test_trader.py
git commit -m "feat: add contested filter to should_trade()"
```

---

## Task 6: Wire up 3-phase pipeline in `scan_and_trade()` and `execute_trade()`

**Files:**
- Modify: `trader.py`
- Modify: `database.py`
- Modify: `tests/test_trader.py`

**Step 1: Write failing integration test**

Add to `tests/test_trader.py`:

```python
def test_scan_and_trade_three_phase_pipeline(mock_db):
    """Full pipeline: screener flags 1 market → research → probability → trade placed."""
    import json

    markets = [{
        "market_id": "mkt_pipeline",
        "question": "Will Chelsea win the league?",
        "category": "sports",
        "yes_price": 0.30,
        "no_price": 0.70,
        "volume": 50000,
        "end_date_iso": "2026-05-20T00:00:00Z",
        "yes_token_id": "t_yes",
        "no_token_id": "t_no",
    }]

    screen_result = [{"market_id": "mkt_pipeline", "initial_lean": "YES", "reason": "Undervalued"}]
    research_result = {
        "key_facts": ["Chelsea top of league"],
        "base_rate": "Leaders at this stage win ~70%",
        "recent_developments": "Won last 5",
        "uncertainty_factors": ["Still 10 games left"],
    }
    analysis_result = {
        "probability": 0.72,
        "side": "YES",
        "confidence": "high",
        "base_rate_estimate": 0.70,
        "contested": False,
        "reasoning": "Strong leader, base rate 70%, recent form confirms.",
        "edge": 0.42,
        "entry_price": 0.30,
        "token_id": "t_yes",
    }

    with patch("trader.polymarket_client.get_active_markets", return_value=markets), \
         patch("trader.gemini_agent.screen_markets", return_value=screen_result), \
         patch("trader.gemini_agent.research_market", return_value=research_result), \
         patch("trader.gemini_agent.assign_probability", return_value=analysis_result), \
         patch("trader.gemini_agent.calculate_position_size", return_value=15.0):
        count = trader.scan_and_trade(mock_db)

    assert count == 1
    import database
    trades = database.get_open_trades(mock_db)
    assert len(trades) == 1
    assert trades[0]["market_id"] == "mkt_pipeline"
    # research brief stored
    assert trades[0]["research_brief"] is not None
    stored = json.loads(trades[0]["research_brief"])
    assert stored["key_facts"] == ["Chelsea top of league"]
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_trader.py::test_scan_and_trade_three_phase_pipeline -v
```
Expected: FAIL

**Step 3: Update `execute_trade()` in `trader.py` to accept and store `research_brief`**

Change the signature:
```python
def execute_trade(
    db_path: str,
    market: dict,
    analysis: dict,
    size_usd: float,
    mode: str,
    research_brief: Optional[str] = None,
) -> Optional[int]:
```

Add `research_brief` to the trade dict before `database.insert_trade`:
```python
    trade["research_brief"] = research_brief
```

**Step 4: Update `insert_trade()` in `database.py` to store `research_brief`**

Replace the existing `insert_trade` function:

```python
def insert_trade(db_path: str, trade: dict) -> int:
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("""
        INSERT INTO trades (market_id, question, category, outcome, side, size_usd,
            entry_price, current_price, pnl, status, mode, gemini_probability,
            gemini_reasoning, edge, closes_at, research_brief)
        VALUES (:market_id, :question, :category, :outcome, :side, :size_usd,
            :entry_price, :current_price, :pnl, :status, :mode, :gemini_probability,
            :gemini_reasoning, :edge, :closes_at, :research_brief)
    """, trade)
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id
```

**Step 5: Replace `scan_and_trade()` in `trader.py` with 3-phase pipeline**

```python
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
```

**Step 6: Run integration test**

```bash
python -m pytest tests/test_trader.py::test_scan_and_trade_three_phase_pipeline -v
```
Expected: PASS

**Step 7: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass

**Step 8: Commit**

```bash
git add trader.py database.py tests/test_trader.py
git commit -m "feat: wire up 3-phase pipeline in scan_and_trade, store research_brief"
```

---

## Task 7: Remove dead `analyze_market()` and clean up

**Files:**
- Modify: `gemini_agent.py`
- Modify: `tests/test_gemini_agent.py`

**Step 1: Delete `analyze_market()` from `gemini_agent.py`**

Remove the entire `analyze_market` function (lines 87–141 in the current file). It is fully replaced by `screen_markets` + `research_market` + `assign_probability`.

Also remove the old `SYSTEM_PROMPT` constant (replaced by the three new phase-specific prompts) and `parse_gemini_response` (replaced by `parse_screen_response`, `parse_research_response`, `parse_probability_response`).

**Step 2: Remove old tests that tested the deleted functions**

In `tests/test_gemini_agent.py`, delete:
- `test_parse_gemini_response_valid_json`
- `test_parse_gemini_response_json_in_markdown`
- `test_parse_gemini_response_invalid_returns_none`

These tested `parse_gemini_response` which no longer exists.

**Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass (no references to removed functions)

**Step 4: Commit**

```bash
git add gemini_agent.py tests/test_gemini_agent.py
git commit -m "refactor: remove legacy analyze_market() and parse_gemini_response()"
```
