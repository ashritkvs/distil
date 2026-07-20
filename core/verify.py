"""Meaning verification + safe compression (the trust layer).

`verify_meaning` uses an LLM judge to score how well a compressed prompt
preserves the original's intent (0-100). `compress_safe` compresses, verifies,
and — if the score is below a threshold — retries gentler, ultimately falling
back to the original so a meaning-breaking compression is NEVER returned.

Requires OPENAI_API_KEY for the judge; degrades gracefully (verified=False)
without it.
"""

from __future__ import annotations

import json
import os

from core import compress as _compress
from core.intelligence import count_tokens

_JUDGE_SYSTEM = (
    "You are a strict semantic similarity judge. Compare an ORIGINAL prompt to a "
    "COMPRESSED prompt. Score 0-100 how well the compressed version preserves the "
    "full intent, instructions, and specific details of the original. Penalize any "
    "lost specifics, entities, or constraints.\n"
    "Respond in raw JSON only (no markdown):\n"
    '{"score": int, "reasoning": str, "pass": bool, "lost_concepts": [str]}\n'
    "pass is true if score >= 75."
)


def verify_meaning(original: str, compressed: str) -> dict:
    """Score how well `compressed` preserves the meaning of `original`."""
    if not os.getenv("OPENAI_API_KEY"):
        return {"verified": False, "score": None, "pass": None,
                "reasoning": "OPENAI_API_KEY not set — verification unavailable",
                "lost_concepts": []}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            max_tokens=500,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user",
                 "content": f"ORIGINAL:\n{original}\n\nCOMPRESSED:\n{compressed}"},
            ],
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
        return {
            "verified": True,
            "score": int(parsed.get("score", 0)),
            "pass": bool(parsed.get("pass", False)),
            "reasoning": parsed.get("reasoning", ""),
            "lost_concepts": parsed.get("lost_concepts", []),
        }
    except Exception as e:
        return {"verified": False, "score": None, "pass": None,
                "reasoning": f"verification error: {e}", "lost_concepts": []}


def _attach(result: dict, v: dict, safe_fallback: bool = False,
            best_attempt_score=None) -> dict:
    result["meaning_score"] = v.get("score")
    result["meaning_pass"] = v.get("pass")
    result["meaning_reasoning"] = v.get("reasoning")
    result["lost_concepts"] = v.get("lost_concepts", [])
    result["verified"] = v.get("verified", False)
    result["safe_fallback"] = safe_fallback
    if best_attempt_score is not None:
        result["best_attempt_score"] = best_attempt_score
    return result


def compress_safe(text: str, target_ratio: float = 0.5, quality: bool = False,
                  target_model: str | None = None, min_score: int = 75) -> dict:
    """Compress with a verification quality gate.

    Tries the requested ratio; if the compression scores below `min_score`,
    retries once at a gentler ratio; if that still fails, returns the ORIGINAL
    prompt untouched (safe_fallback=True) so a meaning-breaking compression is
    never returned.
    """
    # Attempt 1 — requested ratio.
    r1 = _compress(text, target_ratio, quality, target_model, use_cache=False).to_dict()
    v1 = verify_meaning(text, r1["compressed_text"])
    if not v1.get("verified"):
        return _attach(r1, v1)  # can't verify (no key) — return as-is, flagged
    if (v1.get("score") or 0) >= min_score:
        return _attach(r1, v1)

    # Attempt 2 — gentler ratio (retain more).
    gentler = min(target_ratio + 0.2, 0.9)
    r2 = _compress(text, gentler, quality, target_model, use_cache=False).to_dict()
    v2 = verify_meaning(text, r2["compressed_text"])
    if v2.get("verified") and (v2.get("score") or 0) >= min_score:
        return _attach(r2, v2)

    # Both failed — fall back to the ORIGINAL (never ship a broken compression).
    best = max((v1.get("score") or 0), (v2.get("score") or 0))
    fb = _compress(text, target_ratio, quality, target_model, use_cache=False).to_dict()
    orig_tokens = count_tokens(text)
    fb.update({
        "compressed_text": text,
        "compressed_tokens": orig_tokens,
        "tokens_saved": 0,
        "reduction_pct": 0.0,
        "est_cost_saved_usd": 0.0,
        "est_energy_saved_wh": 0.0,
        "est_carbon_saved_g": 0.0,
        "est_gpu_ms_saved": 0.0,
        "compute_reduction_pct": 0.0,
    })
    return _attach(fb, {"verified": True, "score": 100, "pass": True,
                        "reasoning": "Compression scored below threshold; "
                        "returned original prompt unchanged for safety.",
                        "lost_concepts": []},
                   safe_fallback=True, best_attempt_score=best)
