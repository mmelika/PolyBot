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

SYSTEM_PROMPT = """You are an expert prediction market analyst. Your job is to estimate the true probability of a prediction market resolving YES.

ALWAYS respond with valid JSON in this exact format:
{
  "probability": <float between 0 and 1>,
  "side": <"YES" or "NO">,
  "confidence": <"low", "medium", or "high">,
  "reasoning": "<2-3 sentence explanation>"
}

Do not include any text outside the JSON."""


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


def parse_gemini_response(raw: str) -> Optional[dict]:
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
        if not all(k in data for k in ["probability", "side", "confidence", "reasoning"]):
            return None
        prob = float(data["probability"])
        if not (0 <= prob <= 1):
            return None
        if data["side"] not in ("YES", "NO"):
            return None
        return {
            "probability": prob,
            "side": data["side"],
            "confidence": data["confidence"],
            "reasoning": data["reasoning"],
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


def analyze_market(market: dict, performance: dict, use_web_search: bool = True) -> Optional[dict]:
    if not GENAI_AVAILABLE:
        raise RuntimeError("google-generativeai not installed")

    perf_context = build_performance_context(performance)
    market_price = market["yes_price"]

    prompt = f"""{perf_context}

Market: {market['question']}
Category: {market['category']}
Current YES price: {market_price:.4f} (implied probability: {market_price:.1%})
Current NO price: {market['no_price']:.4f}
Total volume: ${market['volume']:,.0f}
Closes: {market['end_date_iso'][:10]}

Estimate the true probability this resolves YES."""

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
            ),
        )
        raw_text = response.text
    except Exception as e:
        print(f"[gemini_agent] API error: {e}")
        return None

    parsed = parse_gemini_response(raw_text)
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
        "reasoning": parsed["reasoning"],
        "edge": edge,
        "entry_price": entry_price,
        "token_id": token_id,
    }
