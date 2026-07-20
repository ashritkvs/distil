"""Prompt classification by intended output type (Phase A, spec item 2).

Pure-Python keyword/pattern classifier (free, serverless). Categories:
enquiry, code, testing, draft, ppt, refinement, other.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, list[str]]] = [
    ("testing", [r"\bunit test", r"\btest case", r"\bpytest\b", r"\bassert\b",
                 r"\btest(s|ing)?\b.*\b(function|code|api)\b", r"\bmock\b"]),
    ("code", [r"\bdef \b", r"\bclass \b", r"\bimport \b", r"```", r"\bfunction\b",
              r"\bimplement\b", r"\bwrite (a |the )?(function|script|program|code|class|method)\b",
              r"\bpython\b|\bjavascript\b|\bjava\b|\bc\+\+\b|\bsql\b|\brust\b|\bgo\b",
              r"\bapi\b.*\b(endpoint|call)\b", r"\balgorithm\b"]),
    ("ppt", [r"\bslide", r"\bpresentation\b", r"\bdeck\b", r"\bpowerpoint\b",
             r"\bppt\b", r"\bpitch\b"]),
    ("draft", [r"\bdraft\b", r"\bwrite (a |an )?(email|letter|essay|blog|post|article|memo|report)\b",
               r"\bcompose\b", r"\bblog post\b", r"\bcover letter\b"]),
    ("refinement", [r"\bimprove\b", r"\brefine\b", r"\brewrite\b", r"\bpolish\b",
                    r"\bedit\b", r"\bmake (it |this )?better\b", r"\bfix\b",
                    r"\brephrase\b", r"\bproofread\b", r"\boptimi[sz]e\b"]),
    ("enquiry", [r"\bwhat\b", r"\bhow\b", r"\bwhy\b", r"\bexplain\b", r"\bdefine\b",
                 r"\bdescribe\b", r"\bcompare\b", r"\bwhen\b", r"\bwhere\b",
                 r"\bsummar", r"\?\s*$"]),
]


def classify(text: str) -> dict:
    """Classify a prompt's output type with per-category signal counts."""
    lower = text.lower()
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}
    for category, patterns in _PATTERNS:
        hits = [p for p in patterns if re.search(p, lower)]
        if hits:
            scores[category] = len(hits)
            matched[category] = hits[:3]

    if not scores:
        return {"category": "other", "confidence": 0.3, "signals": [],
                "all_scores": {}}

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = round(min(0.5 + scores[best] / max(total, 1) * 0.5, 0.99), 2)
    return {
        "category": best,
        "confidence": confidence,
        "signals": matched.get(best, []),
        "all_scores": scores,
    }
