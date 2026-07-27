"""Plan tiers + per-tenant usage metering + quota (billing plumbing).

Defines subscription tiers, meters usage per tenant (in the metrics store, so
Upstash-backed when configured), and enforces monthly request/token quotas.
Payment processing (Stripe) is intentionally out of scope — this is the
metering/entitlement layer a billing integration sits on top of.

Config: map API keys to plans via DISTIL_TENANT_PLANS = "keyA:pro,keyB:free".
"""

from __future__ import annotations

import os
import time

from core.store import store

PLANS = {
    "free": {"monthly_requests": 500, "monthly_tokens": 100_000,
             "llm_features": False, "rate_per_min": 30},
    "pro": {"monthly_requests": 50_000, "monthly_tokens": 20_000_000,
            "llm_features": True, "rate_per_min": 300},
    "enterprise": {"monthly_requests": 2_000_000, "monthly_tokens": 1_000_000_000,
                   "llm_features": True, "rate_per_min": 3000},
}
DEFAULT_PLAN = os.getenv("DISTIL_DEFAULT_PLAN", "free")


def _tenant_plans() -> dict[str, str]:
    out = {}
    for pair in os.getenv("DISTIL_TENANT_PLANS", "").split(","):
        if ":" in pair:
            k, p = pair.split(":", 1)
            out[k.strip()] = p.strip()
    return out


def plan_for(tenant: str) -> str:
    return _tenant_plans().get(tenant, DEFAULT_PLAN)


def record_usage(tenant: str, tokens: int):
    """Meter one billable request for a tenant (stored as a usage event)."""
    try:
        store._backend.push({"ts": time.time(), "kind": "usage",
                             "tenant": tenant, "tokens": int(tokens)})
    except Exception:
        pass


def _month_start() -> float:
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0,
                       microsecond=0).timestamp()


def usage(tenant: str) -> dict:
    """Current-month usage + remaining quota for a tenant."""
    plan_name = plan_for(tenant)
    plan = PLANS.get(plan_name, PLANS[DEFAULT_PLAN])
    since = _month_start()
    try:
        events = [r for r in store._backend.all()
                  if r.get("kind") == "usage" and r.get("tenant") == tenant
                  and r.get("ts", 0) >= since]
    except Exception:
        events = []
    reqs = len(events)
    toks = sum(e.get("tokens", 0) for e in events)
    return {
        "tenant": tenant,
        "plan": plan_name,
        "limits": plan,
        "used_requests": reqs,
        "used_tokens": toks,
        "remaining_requests": max(0, plan["monthly_requests"] - reqs),
        "remaining_tokens": max(0, plan["monthly_tokens"] - toks),
        "over_quota": reqs >= plan["monthly_requests"] or toks >= plan["monthly_tokens"],
    }


def check_quota(tenant: str) -> dict:
    """Return {allowed, reason, plan} for a tenant based on monthly quota."""
    u = usage(tenant)
    if u["over_quota"]:
        return {"allowed": False, "reason": "monthly_quota_exceeded",
                "plan": u["plan"], "usage": u}
    return {"allowed": True, "plan": u["plan"], "usage": u}
