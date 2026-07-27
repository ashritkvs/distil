"""Token budget / quota tracking (Phase B, spec item 1 — available side).

Tracks tokens used against a configured monthly budget (DISTIL_TOKEN_BUDGET). This
is a *configured* budget, not live provider-account quota (which needs the
provider's billing API + keys). Also reports how much the budget is effectively
stretched by compression.
"""

from __future__ import annotations

import os

from core.store import store


def get_budget() -> dict:
    budget = int(os.getenv("DISTIL_TOKEN_BUDGET", "1000000"))
    records = store.all_records()
    used = sum(r.get("original_tokens", 0) for r in records)
    sent_after_compression = sum(r.get("compressed_tokens", 0) for r in records)
    saved = sum(r.get("tokens_saved", 0) for r in records)
    available = max(0, budget - used)
    # Effective budget when compression is applied (fewer tokens actually sent).
    effective_available = max(0, budget - sent_after_compression)
    return {
        "budget_tokens": budget,
        "tokens_used": used,
        "tokens_available": available,
        "utilization_pct": round(used / budget * 100, 2) if budget else 0.0,
        "tokens_sent_after_compression": sent_after_compression,
        "tokens_saved_by_compression": saved,
        "effective_available_with_compression": effective_available,
        "note": ("Configured budget (DISTIL_TOKEN_BUDGET). Live provider-account "
                 "quota requires the provider's billing API + keys."),
    }
