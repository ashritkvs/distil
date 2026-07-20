"""TraceFlow Compress — MCP server (FastMCP).

Exposes the compression core as MCP tools + a metrics resource. Runs over
stdio locally (`python mcp_server.py`) and is mounted over streamable HTTP for
serverless deployment (see api/index.py).
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from core import analyze as _analyze
from core import compress as _compress
from core import estimates
from core.aiops import detect_anomalies as _detect_anomalies
from core.cache import cache
from core.budget import get_budget as _get_budget
from core.classify import classify as _classify
from core.forecast import forecast as _forecast
from core.governance import govern as _govern
from core.pipeline import process as _process
from core.policy import check_packages as _check_packages
from core.providers import list_providers as _list_providers
from core.routing import route as _route
from core.store import store
from core.verify import compress_safe as _compress_safe
from core.verify import verify_meaning as _verify_meaning

load_dotenv()

# Transport security. Access is gated by the API key (see api/index.py), so
# DNS-rebinding host checks are off by default to work on any deploy domain.
# Tighten via env: TF_DNS_REBIND_PROTECTION=true + TF_ALLOWED_HOSTS/ORIGINS.
_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=(
        os.getenv("TF_DNS_REBIND_PROTECTION", "false").lower() == "true"
    ),
    allowed_hosts=[h.strip() for h in os.getenv("TF_ALLOWED_HOSTS", "*").split(",")],
    allowed_origins=[o.strip() for o in os.getenv("TF_ALLOWED_ORIGINS", "*").split(",")],
)

# stateless_http=True: each request is self-contained (no persistent session /
# background task group), which is required to run on serverless (Vercel).
mcp = FastMCP("traceflow-compress", transport_security=_security,
              stateless_http=True, json_response=True)


@mcp.tool()
def compress_prompt(
    text: str,
    target_ratio: float = 0.5,
    quality: bool = False,
    target_model: str = "gpt-4o-mini",
    use_cache: bool = True,
    verify: bool = False,
    safe: bool = False,
    min_score: int = 75,
) -> dict:
    """Compress a prompt and return full TraceFlow metrics.

    Args:
        text: the prompt to compress.
        target_ratio: fraction of tokens to retain (0-1). Lower = smaller.
        quality: if true, use the gpt-4o-mini LLM path (needs OPENAI_API_KEY);
            otherwise the fast, free, local heuristic.
        target_model: model the compressed prompt will be sent to. Pass "auto"
            to route by complexity.
        use_cache: consult/populate the semantic cache (default true).
        verify: if true, LLM-score how well meaning is preserved (needs key).
        safe: if true, apply a quality gate — retry gentler and fall back to the
            original if the compression scores below min_score (never ships a
            meaning-breaking result). Implies verification.
        min_score: pass threshold for verify/safe (0-100).
    """
    try:
        if safe:
            d = _compress_safe(text, target_ratio, quality, target_model, min_score)
        else:
            result = _compress(text, target_ratio=target_ratio, quality=quality,
                               target_model=target_model, use_cache=use_cache)
            d = result.to_dict()
            if verify and not d.get("cache_hit"):
                v = _verify_meaning(text, d["compressed_text"])
                d["meaning_score"] = v.get("score")
                d["meaning_pass"] = v.get("pass")
                d["meaning_reasoning"] = v.get("reasoning")
                d["lost_concepts"] = v.get("lost_concepts", [])
                d["verified"] = v.get("verified", False)
    except Exception as e:
        store.record_error(type(e).__name__, text)
        raise
    if not d.get("cache_hit"):
        store.record(d)
    return d


@mcp.tool()
def verify_meaning(original: str, compressed: str) -> dict:
    """Score 0-100 how well a compressed prompt preserves the original's intent.

    Uses an LLM judge (needs OPENAI_API_KEY). Returns score, pass (>=75),
    reasoning, and any lost concepts.
    """
    return _verify_meaning(original, compressed)


@mcp.tool()
def process_prompt(
    text: str,
    target_ratio: float = 0.5,
    quality: bool = False,
    target_model: str = "gpt-4o-mini",
    verify: bool = False,
    safe: bool = False,
    enforce: bool = True,
) -> dict:
    """UNIFIED gateway: govern + compress a prompt in one call.

    Runs classification, content-security, moderation, package-policy, and
    code-vuln checks, then (if not blocked) compresses. Returns one combined
    report with verdict (allow/warn/block), governance detail, and compression
    metrics. This is the main entrypoint that ties everything together.
    """
    return _process(text, target_ratio=target_ratio, quality=quality,
                    target_model=target_model, verify=verify, safe=safe,
                    enforce=enforce)


@mcp.tool()
def govern_prompt(text: str) -> dict:
    """Governance only (no compression): classification, content-security,
    moderation, package-policy, and code-vuln checks with a verdict."""
    return _govern(text)


@mcp.tool()
def classify_prompt(text: str) -> dict:
    """Classify a prompt's output type: enquiry, code, testing, draft, ppt,
    refinement, or other."""
    return _classify(text)


@mcp.tool()
def get_violations(n: int = 50) -> list[dict]:
    """Return recent governance violations (warn/block verdicts)."""
    return store.violations(n)


@mcp.tool()
def get_budget() -> dict:
    """Token budget status: used, available, utilization, and how much the
    budget is stretched by compression (configured via TF_TOKEN_BUDGET)."""
    return _get_budget()


@mcp.tool()
def check_policy(text: str) -> dict:
    """Check package references in a prompt/code against the allow/deny policy."""
    return _check_packages(text)


@mcp.tool()
def list_providers() -> dict:
    """List LLM providers this gateway can integrate with and whether each is
    currently configured."""
    return _list_providers()


@mcp.tool()
def route_prompt(text: str) -> dict:
    """Recommend a model tier (small vs large) for a prompt by complexity.

    Returns the recommended model, a 0-100 complexity score, the reasoning,
    and per-model cost estimates so the routing decision is transparent.
    """
    return _route(text)


@mcp.tool()
def analyze_prompt(text: str) -> dict:
    """Analyze a prompt (tokens, fillers, redundancy) without compressing it."""
    return _analyze(text)


@mcp.tool()
def estimate_savings(
    text: str,
    calls_per_day: int = 1000,
    target_model: str = "gpt-4o-mini",
) -> dict:
    """Project cost + carbon savings if this prompt were compressed and reused.

    Compresses once (heuristic), then scales the per-call savings by
    `calls_per_day` to a monthly projection.
    """
    r = _compress(text, target_ratio=0.5, target_model=target_model)
    per_call_cost = r.est_cost_saved_usd
    per_call_carbon = r.est_carbon_saved_g
    monthly = calls_per_day * 30
    return {
        "target_model": target_model,
        "tokens_saved_per_call": r.tokens_saved,
        "reduction_pct": r.reduction_pct,
        "calls_per_day": calls_per_day,
        "projected_monthly_cost_saved_usd": round(per_call_cost * monthly, 4),
        "projected_monthly_carbon_saved_g": round(per_call_carbon * monthly, 2),
        "projected_monthly_tokens_saved": r.tokens_saved * monthly,
        "estimates_meta": estimates.estimates_meta(target_model),
    }


@mcp.tool()
def get_metrics() -> dict:
    """Aggregate TraceFlow metrics across all compressions, incl. cache stats."""
    summary = store.summary()
    summary["cache"] = cache.stats()
    return summary


@mcp.tool()
def get_top_prompts(n: int = 5) -> list[dict]:
    """Return the prompts that saved the most tokens (most compressible)."""
    return store.top_prompts(n)


@mcp.tool()
def detect_anomalies() -> dict:
    """Detect anomalies in the compression metric stream (AIOps).

    Flags low-compression prompts, token spikes, and cost spikes using an
    IQR-fence baseline over stored records. Returns status "insufficient_data"
    until enough history exists.
    """
    return _detect_anomalies(store.all_records())


@mcp.tool()
def forecast_savings(horizon_days: int = 30) -> dict:
    """Project cost / carbon / token savings over a horizon from the run-rate.

    Extrapolates from stored records. Flags low confidence for short windows.
    """
    return _forecast(store.all_records(), horizon_days=horizon_days)


@mcp.resource("metrics://summary")
def metrics_summary() -> dict:
    """Live aggregate metrics as a readable MCP resource."""
    return store.summary()


if __name__ == "__main__":
    mcp.run()  # stdio transport
