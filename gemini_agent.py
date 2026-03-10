import json
import re
from typing import Optional
import config

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    genai_types = None
    GENAI_AVAILABLE = False

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


def build_performance_context(performance: dict) -> str:
    if not performance:
        return "No closed trades yet — no historical performance data available."
    lines = ["Your historical performance by category (use this to calibrate):"]
    for category, stats in performance.items():
        win_rate_pct = int(round(stats["win_rate"] * 100))
        lines.append(
            f"  - {category.capitalize()}: {stats['total']} trades, "
            f"{win_rate_pct}% win rate, ${stats['total_pnl']:.2f} total P&L, "
            f"avg edge {stats['avg_edge']:.1%}"
        )
    return "\n".join(lines)


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


def calculate_position_size(
    probability: float,
    entry_price: float,
    portfolio_value: float,
    max_position: float = None,
    kelly_fraction: float = None,
) -> float:
    if max_position is None:
        max_position = config.MAX_POSITION_SIZE
    if kelly_fraction is None:
        kelly_fraction = config.KELLY_FRACTION
    if entry_price <= 0 or entry_price >= 1:
        return min(max_position * 0.1, max_position)
    b = (1.0 / entry_price) - 1.0
    p = probability
    q = 1.0 - probability
    kelly_f = (p * b - q) / b
    if kelly_f <= 0:
        return 0.0
    size = kelly_f * kelly_fraction * portfolio_value
    return min(size, max_position)


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


