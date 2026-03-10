# Three-Phase Picking Pipeline Design

**Date:** 2026-03-10
**Status:** Approved

## Problem

The current `analyze_market()` function has two compounding flaws:

1. **No real web search.** The `use_web_search=True` parameter is accepted but never wired up — Gemini uses stale training data, not live information.
2. **No screening phase.** Every market gets a single-pass analysis with a sparse prompt, causing Gemini to overconfidently assign probabilities based on narrative signals (e.g. "Forest are struggling" → 85% relegated) without grounding in base rates or actual current data.

The Nottingham Forest relegation pick exemplifies both problems: 1 point above the drop zone in a 3-way battle is ~33-40% probability by base rate, not 85%.

## Solution: Three-Phase Pipeline

Replace `analyze_market()` with three distinct functions, each with a clear responsibility.

---

## Phase 1: Screener (`screen_markets`)

**Input:** All active markets (question, yes_price, volume, close date)
**API call:** 1 bulk Gemini call, no search
**Purpose:** Identify markets where Gemini believes it has genuine informational edge. Skip coin flips, markets it has no knowledge of, and anything already held as an open position.

**Output per flagged market:**
```json
{ "market_id": "...", "initial_lean": "YES|NO", "reason": "..." }
```

**Config:** `MAX_FLAGGED_MARKETS = 10` (default) caps how many proceed to Phase 2.

---

## Phase 2: Research Brief (`research_market`)

**Input:** Single flagged market
**API call:** 1 Gemini call per market, **search grounding enabled**
**Purpose:** Gather current facts only — no probability assigned. Forces separation between evidence collection and reasoning.

**Output:**
```json
{
  "key_facts": ["..."],
  "base_rate": "Teams in 17th with 9 games left are relegated ~45% historically",
  "recent_developments": "...",
  "uncertainty_factors": ["..."]
}
```

The research brief is stored in the DB (`research_brief` column) for auditability and debugging.

---

## Phase 3: Probability Assignment (`assign_probability`)

**Input:** Market metadata + research brief from Phase 2
**API call:** 1 Gemini call per market, **no internet access**
**Purpose:** Deterministic reasoning from fixed facts. No new narrative can be introduced. Must explicitly anchor to the base rate before moving to a final probability.

**Output:**
```json
{
  "probability": 0.38,
  "side": "NO",
  "confidence": "low",
  "base_rate_estimate": 0.45,
  "contested": true,
  "reasoning": "3-way battle makes this near-random. Base rate ~45%, no sufficient edge to trade."
}
```

**New fields vs current:**
- `base_rate_estimate` — forces Gemini to state what naive base rate suggests
- `contested` — true when multiple outcomes are near-equal probability; becomes a hard filter

---

## Filter Changes (`should_trade`)

New rule added to existing filters:

| Filter | Rule |
|--------|------|
| `contested: true` | Skip unless `confidence == "high"` AND `edge >= 0.15` |
| Existing `confidence == "low"` | Skip (unchanged) |
| Existing `edge < MIN_EDGE` | Skip (unchanged) |
| Existing long-term `probability < 0.80` | Skip (unchanged) |

---

## Files Changed

| File | Change |
|------|--------|
| `gemini_agent.py` | Replace `analyze_market()` with `screen_markets()`, `research_market()`, `assign_probability()`. Wire up search grounding in Phase 2. |
| `trader.py` | Update `scan_and_trade()` to run 3-phase pipeline. Update `should_trade()` with `contested` filter. |
| `database.py` | Add `research_brief TEXT` column to `trades` table. |
| `config.py` | Add `MAX_FLAGGED_MARKETS = 10`. |

---

## Trade-offs

- **3× API calls per flagged market** — acceptable because Phase 1 screens down to ~10 markets, so total calls are bounded.
- **Slower per scan cycle** — research phase does live web searches; worth it for accuracy.
- **More complex to debug** — mitigated by storing the research brief in the DB so every decision is auditable.
