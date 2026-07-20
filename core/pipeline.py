"""Unified pipeline — governance + compression + verification in one call.

`process` is the single integrated entrypoint that ties together every piece
we've built: it governs a prompt (classify/security/moderation/policy/code),
optionally blocks it, then compresses (with optional verification / safe mode)
and returns one combined report.
"""

from __future__ import annotations

from core import compress as _compress
from core.governance import govern
from core.store import store


def process(
    text: str,
    target_ratio: float = 0.5,
    quality: bool = False,
    target_model: str = "gpt-4o-mini",
    verify: bool = False,
    safe: bool = False,
    adaptive: bool = False,
    min_score: int = 75,
    use_cache: bool = True,
    enforce: bool = True,
) -> dict:
    """Govern then compress a prompt, returning a unified report.

    Args:
        enforce: if True, a governance verdict of "block" stops compression and
            returns the prompt unprocessed with the reasons.
        (other args mirror compress_prompt)
    """
    report = govern(text)

    # Enforcement gate: block prompts that fail governance.
    if enforce and report["verdict"] == "block":
        return {
            "blocked": True,
            "verdict": "block",
            "governance": report,
            "compressed_text": None,
            "reasons": report["reasons"],
        }

    # Otherwise compress (reusing adaptive/safe/verify/normal paths).
    if adaptive:
        from core.verify import compress_adaptive
        d = compress_adaptive(text, target_ratio, target_model, min_score)
    elif safe:
        from core.verify import compress_safe
        d = compress_safe(text, target_ratio, quality, target_model, min_score)
    else:
        d = _compress(text, target_ratio=target_ratio, quality=quality,
                      target_model=target_model, use_cache=use_cache).to_dict()
        if verify and not d.get("cache_hit"):
            from core.verify import verify_meaning
            v = verify_meaning(text, d["compressed_text"])
            d.update({"meaning_score": v.get("score"),
                      "meaning_pass": v.get("pass"),
                      "verified": v.get("verified", False)})

    if not d.get("cache_hit"):
        store.record(d)

    d["blocked"] = False
    d["verdict"] = report["verdict"]
    d["governance"] = report
    return d
