"""Content moderation — non-violent / safe content (Phase A, spec items 6-7).

Uses the OpenAI Moderation API when OPENAI_API_KEY is set (free, robust);
otherwise a lightweight keyword heuristic. Flags violence, hate, self-harm,
sexual, and harassment categories.
"""

from __future__ import annotations

import os

_HEURISTIC = {
    "violence": ["kill", "murder", "shoot", "stab", "bomb", "attack", "assault",
                 "behead", "massacre", "torture"],
    "hate": ["racist", "genocide", "ethnic cleansing"],
    "self_harm": ["suicide", "self-harm", "kill myself", "end my life"],
    "weapons": ["how to make a bomb", "build a weapon", "napalm", "nerve agent"],
}


def _heuristic(text: str) -> dict:
    lower = text.lower()
    flagged_categories = []
    for cat, words in _HEURISTIC.items():
        if any(w in lower for w in words):
            flagged_categories.append(cat)
    return {
        "flagged": bool(flagged_categories),
        "categories": flagged_categories,
        "method": "keyword-heuristic",
        "note": "Basic keyword screen; not comprehensive.",
    }


def moderate(text: str) -> dict:
    """Return moderation verdict + flagged categories."""
    if not os.getenv("OPENAI_API_KEY"):
        return _heuristic(text)
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.moderations.create(
            model="omni-moderation-latest", input=text[:4000])
        r = resp.results[0]
        cats = [k for k, v in r.categories.model_dump().items() if v]
        return {
            "flagged": bool(r.flagged),
            "categories": cats,
            "method": "openai-moderation",
        }
    except Exception:
        return _heuristic(text)
