"""Multi-model routing (TraceFlow §8.4).

Scores prompt complexity and recommends a model tier: simple/short prompts go
to a small cheap model, complex/long ones to a larger model. Returns the
recommendation plus per-model cost estimates so the routing decision is
transparent.
"""

from __future__ import annotations

import os
import re

from core import estimates
from core.intelligence import count_tokens

_CODE_RE = re.compile(r"```|(?:^|\s)(?:def|class|import|function|return)\s|=>|;\s*$|</\w")
_REASONING = {
    "explain", "analyze", "design", "compare", "evaluate", "derive", "prove",
    "optimize", "architect", "debug", "refactor", "summarize", "critique",
}

SMALL_MODEL = os.getenv("TF_SMALL_MODEL", "gpt-4o-mini")
LARGE_MODEL = os.getenv("TF_LARGE_MODEL", "gpt-4o")
ROUTE_THRESHOLD = float(os.getenv("TF_ROUTE_THRESHOLD", "50"))


def complexity_score(text: str) -> float:
    """0-100 complexity estimate from reasoning, code, structure, and length.

    Reasoning verbs are the strongest signal (a short "design and analyze..."
    prompt is complex); length is a lighter contributor.
    """
    tokens = count_tokens(text)
    lower = text.lower()
    score = 0.0
    reasoning_hits = sum(1 for w in _REASONING if w in lower)
    score += min(reasoning_hits * 10, 40)            # reasoning verbs (up to 40)
    if _CODE_RE.search(text):
        score += 20                                  # contains code
    if any(k in lower for k in ("step by step", "multiple", "several", "all the",
                                "each of", "trade-off", "tradeoff")):
        score += 10                                  # multi-part structure
    score += min(lower.count("?") * 5, 10)           # multiple questions
    score += min(tokens / 400, 1.0) * 20             # length (up to 20)
    return round(min(score, 100.0), 1)


def route(text: str) -> dict:
    """Recommend a model tier for `text` with cost transparency."""
    score = complexity_score(text)
    tier = "large" if score >= ROUTE_THRESHOLD else "small"
    recommended = LARGE_MODEL if tier == "large" else SMALL_MODEL
    tokens = count_tokens(text)

    cost_small = estimates.est_cost_usd(tokens, SMALL_MODEL, "in")
    cost_large = estimates.est_cost_usd(tokens, LARGE_MODEL, "in")
    reason = (
        f"complexity {score}/100 "
        f"({'above' if tier == 'large' else 'below'} threshold {ROUTE_THRESHOLD}) "
        f"→ {tier} tier"
    )
    return {
        "recommended_model": recommended,
        "tier": tier,
        "complexity_score": score,
        "reasoning": reason,
        "prompt_tokens": tokens,
        "est_cost_small_usd": cost_small,
        "est_cost_large_usd": cost_large,
        "est_cost_saved_vs_large_usd": round(max(cost_large - cost_small, 0.0), 8)
        if tier == "small" else 0.0,
        "small_model": SMALL_MODEL,
        "large_model": LARGE_MODEL,
    }
