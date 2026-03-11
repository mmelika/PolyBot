import json
import logging
import re
from typing import Optional

import config

log = logging.getLogger("gemini")

try:
    from google import genai
    from google.genai import types as genai_types

    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    genai_types = None
    GENAI_AVAILABLE = False


SYSTEM_PROMPT_VERIFY = """You are verifying whether a prediction market outcome is already effectively decided.

Be conservative. Only mark a market verified when the underlying real-world outcome is already determined or overwhelmingly locked in by objective facts available from current reporting.

Return valid JSON only:
{
  "verified": true,
  "outcome": "YES",
  "confidence": 0.93,
  "reasoning": "Short explanation of why the outcome is already settled or why uncertainty remains."
}
"""


def parse_verify_response(raw: str) -> Optional[dict]:
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    required = ("verified", "outcome", "confidence", "reasoning")
    if not all(key in data for key in required):
        return None

    try:
        confidence = float(data["confidence"])
    except (TypeError, ValueError):
        return None

    if not 0.0 <= confidence <= 1.0:
        return None
    outcome = str(data["outcome"]).upper()
    if outcome not in ("YES", "NO", "UNKNOWN"):
        return None
    return {
        "verified": bool(data["verified"]),
        "outcome": outcome,
        "confidence": confidence,
        "reasoning": str(data["reasoning"]),
    }


def verify_outcome(question: str, market_data: Optional[dict] = None) -> Optional[dict]:
    if not GENAI_AVAILABLE:
        raise RuntimeError("google-genai not installed")
    if not config.GEMINI_API_KEY:
        log.error("[gemini] GEMINI_API_KEY is empty; skipping verification")
        return None

    market_data = market_data or {}
    prompt = (
        f"Question: {question}\n"
        f"Closes at: {market_data.get('end_date_iso', 'unknown')}\n"
        f"Category: {market_data.get('category', 'other')}\n"
        f"Current best YES ask: {market_data.get('yes_ask_price', 'unknown')}\n"
        f"Current best NO ask: {market_data.get('no_ask_price', 'unknown')}\n\n"
        "Has this market outcome already been determined? "
        "If yes, identify whether the winning outcome is YES or NO. "
        "If any meaningful uncertainty remains, set verified=false."
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
    except Exception as exc:
        log.error("[gemini] Verification request failed: %s", exc)
        return None

    return parse_verify_response(response.text)
