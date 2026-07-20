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
from core.forecast import forecast as _forecast
from core.routing import route as _route
from core.store import store

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
) -> dict:
    """Compress a prompt and return full TraceFlow metrics.

    Args:
        text: the prompt to compress.
        target_ratio: fraction of tokens to retain (0-1). Lower = smaller.
        quality: if true, use the gpt-4o-mini LLM path (needs OPENAI_API_KEY);
            otherwise the fast, free, local heuristic.
        target_model: model the compressed prompt will be sent to (drives the
            cost/energy/carbon/compute estimates). Pass "auto" to route by
            complexity.
        use_cache: consult/populate the semantic cache (default true).
    """
    try:
        result = _compress(text, target_ratio=target_ratio, quality=quality,
                           target_model=target_model, use_cache=use_cache)
    except Exception as e:
        store.record_error(type(e).__name__, text)
        raise
    d = result.to_dict()
    if not d.get("cache_hit"):
        store.record(d)  # count real compressions, not cache replays
    return d


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
