"""Forecasting: project savings from the recent run-rate (Distil §7).

Extrapolates cost / carbon / token savings over a horizon from the rate
observed in stored records. Flags low confidence when the observed window is
short (a burst of test calls is not a reliable daily rate).
"""

from __future__ import annotations

import time


def forecast(records: list[dict], horizon_days: int = 30,
             min_samples: int = 8) -> dict:
    ok = [r for r in records if r.get("kind") != "error"]
    if len(ok) < min_samples:
        return {"status": "insufficient_data", "min_samples": min_samples,
                "samples": len(ok)}

    ts = [r.get("ts", 0.0) for r in ok if r.get("ts")]
    span_s = (max(ts) - min(ts)) if len(ts) >= 2 else 0.0
    # If the window is tiny, fall back to a rate assuming records span 1 hour
    # so we don't report absurd per-day rates from a burst.
    window_days = max(span_s / 86400, 1 / 24)
    low_confidence = span_s < 3600  # under an hour of real history

    tot_cost = sum(r.get("est_cost_saved_usd", 0.0) for r in ok)
    tot_carbon = sum(r.get("est_carbon_saved_g", 0.0) for r in ok)
    tot_tokens = sum(r.get("tokens_saved", 0) for r in ok)

    daily = {
        "requests": round(len(ok) / window_days, 1),
        "tokens_saved": round(tot_tokens / window_days),
        "cost_saved_usd": round(tot_cost / window_days, 6),
        "carbon_saved_g": round(tot_carbon / window_days, 4),
    }
    projected = {
        "tokens_saved": round(daily["tokens_saved"] * horizon_days),
        "cost_saved_usd": round(daily["cost_saved_usd"] * horizon_days, 4),
        "carbon_saved_g": round(daily["carbon_saved_g"] * horizon_days, 2),
    }
    return {
        "status": "ok",
        "samples": len(ok),
        "observed_window_days": round(window_days, 3),
        "low_confidence": low_confidence,
        "note": ("Extrapolated from a short window (<1h); treat as indicative."
                 if low_confidence else "Projected from observed run-rate."),
        "horizon_days": horizon_days,
        "daily_rate": daily,
        f"projected_{horizon_days}d": projected,
        "estimated": True,
    }
