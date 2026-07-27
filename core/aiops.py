"""AIOps: anomaly detection over the compression metric stream (Distil §7).

Statistical (IQR-fence) + rule-based detection on stored records. Flags:
  * low-compression prompts (reduction far below the normal range)
  * token spikes (unusually large prompts)
  * cost spikes (est. cost saved far above normal — i.e. very expensive prompts)

All detection is on MEASURED token metrics and labeled estimates — no faked
signals. Returns a status so callers can tell "insufficient data" from "clean".
"""

from __future__ import annotations

import statistics


def _fences(values: list[float]) -> tuple[float, float]:
    """Return (low_fence, high_fence) using the 1.5*IQR rule."""
    if len(values) < 4:
        lo, hi = min(values), max(values)
        return lo, hi
    q1, _, q3 = statistics.quantiles(values, n=4)
    iqr = q3 - q1
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def detect_anomalies(records: list[dict], min_samples: int = 8,
                     scan_recent: int = 25) -> dict:
    """Detect anomalies in recent compression records.

    Args:
        records: stored metric records (newest-first list from the store).
        min_samples: minimum history before detection runs.
        scan_recent: how many of the most-recent records to flag against the
            baseline computed from all records.
    """
    if len(records) < min_samples:
        return {
            "status": "insufficient_data",
            "min_samples": min_samples,
            "samples": len(records),
            "anomalies": [],
        }

    reductions = [r.get("reduction_pct", 0.0) for r in records]
    tokens = [r.get("original_tokens", 0) for r in records]
    costs = [r.get("est_cost_saved_usd", 0.0) for r in records]

    red_low, _ = _fences(reductions)
    _, tok_high = _fences(tokens)
    _, cost_high = _fences(costs)

    anomalies: list[dict] = []
    for r in records[:scan_recent]:
        preview = r.get("preview", "")
        red = r.get("reduction_pct", 0.0)
        tok = r.get("original_tokens", 0)
        cost = r.get("est_cost_saved_usd", 0.0)
        if red < red_low:
            anomalies.append({
                "type": "low_compression",
                "severity": "warning",
                "detail": f"reduction {red}% below normal (< {round(red_low, 1)}%)",
                "preview": preview,
            })
        if tok > tok_high:
            anomalies.append({
                "type": "token_spike",
                "severity": "info",
                "detail": f"{tok} tokens above normal (> {round(tok_high)})",
                "preview": preview,
            })
        if cost > cost_high and cost_high > 0:
            anomalies.append({
                "type": "cost_spike",
                "severity": "info",
                "detail": f"est. cost saved ${cost} above normal",
                "preview": preview,
            })

    return {
        "status": "ok",
        "samples": len(records),
        "baselines": {
            "reduction_low_fence_pct": round(red_low, 1),
            "token_high_fence": round(tok_high),
            "median_reduction_pct": round(statistics.median(reductions), 1),
        },
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }
