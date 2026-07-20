"""Serverless entrypoint — mounts the MCP server over streamable HTTP.

Exposes the FastMCP app as an ASGI `app` for Vercel (Python / Fluid Compute):
  * `/mcp`      — the MCP endpoint (API-key gated when CONNECTOR_API_KEY is set)
  * `/metrics`  — public aggregate metrics JSON
  * `/`         — public read-only dashboard
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse

from core import compress as _compress
from core.cache import cache
from core.store import store
from mcp_server import mcp

_DASH_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
_DASHBOARD = os.path.join(_DASH_DIR, "index.html")
_ENGINEERING = os.path.join(_DASH_DIR, "engineering.html")


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Gate `/mcp` with a bearer token / x-api-key matching CONNECTOR_API_KEY.

    Read-only `/metrics` and `/` (dashboard) stay public.
    """

    async def dispatch(self, request, call_next):
        required = os.getenv("CONNECTOR_API_KEY")
        if required and request.url.path.startswith("/mcp"):
            auth = request.headers.get("authorization", "")
            token = (
                auth[7:] if auth.lower().startswith("bearer ")
                else request.headers.get("x-api-key", "")
            )
            if token != required:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def metrics_endpoint(request):
    summary = store.summary()
    summary["cache"] = cache.stats()
    return JSONResponse(summary)


async def records_endpoint(request):
    return JSONResponse(store.all_records()[:100])


async def violations_endpoint(request):
    return JSONResponse(store.violations(50))


async def process_endpoint(request):
    """Unified gateway: govern + compress a prompt."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    prompt = (data.get("prompt") or "").strip()
    if len(prompt) < 3:
        return JSONResponse({"error": "prompt too short (min 3 chars)"},
                            status_code=422)
    if len(prompt) > 6000:
        return JSONResponse({"error": "prompt too long (max 6000 chars)"},
                            status_code=422)
    from core.pipeline import process
    try:
        d = process(
            prompt,
            target_ratio=float(data.get("target_ratio", 0.5)),
            quality=bool(data.get("quality", False)),
            target_model=data.get("target_model") or "gpt-4o-mini",
            safe=bool(data.get("safe", False)),
            enforce=bool(data.get("enforce", True)),
        )
    except Exception as e:
        store.record_error(type(e).__name__, prompt)
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse(d)


async def compress_endpoint(request):
    """Compress a prompt from the dashboard and record it. Public (heuristic
    is free; quality=true spends the server's OpenAI credits)."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    prompt = (data.get("prompt") or "").strip()
    if len(prompt) < 3:
        return JSONResponse({"error": "prompt too short (min 3 chars)"},
                            status_code=422)
    if len(prompt) > 6000:
        return JSONResponse({"error": "prompt too long (max 6000 chars)"},
                            status_code=422)
    ratio = float(data.get("target_ratio", 0.5))
    quality = bool(data.get("quality", False))
    model = data.get("target_model") or "gpt-4o-mini"
    verify = bool(data.get("verify", False))
    safe = bool(data.get("safe", False))
    try:
        if safe:
            from core.verify import compress_safe
            d = compress_safe(prompt, ratio, quality, model,
                              int(data.get("min_score", 75)))
        else:
            d = _compress(prompt, target_ratio=ratio, quality=quality,
                          target_model=model).to_dict()
            if verify and not d.get("cache_hit"):
                from core.verify import verify_meaning
                v = verify_meaning(prompt, d["compressed_text"])
                d.update({"meaning_score": v.get("score"),
                          "meaning_pass": v.get("pass"),
                          "meaning_reasoning": v.get("reasoning"),
                          "verified": v.get("verified", False)})
    except Exception as e:
        store.record_error(type(e).__name__, prompt)
        return JSONResponse({"error": str(e)}, status_code=500)
    if not d.get("cache_hit"):
        store.record(d)
    return JSONResponse(d)


def _serve(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse(fallback)


async def dashboard(request):
    return _serve(_DASHBOARD, "<h1>TraceFlow Compress</h1><p>See /metrics</p>")


async def engineering(request):
    return _serve(_ENGINEERING, "<h1>Engineering view</h1><p>See /records</p>")


# ASGI app served by Vercel. MCP at /mcp; dashboards + data public.
app = mcp.streamable_http_app()
app.add_route("/metrics", metrics_endpoint, methods=["GET"])
app.add_route("/records", records_endpoint, methods=["GET"])
app.add_route("/violations", violations_endpoint, methods=["GET"])
app.add_route("/compress", compress_endpoint, methods=["POST"])
app.add_route("/process", process_endpoint, methods=["POST"])
app.add_route("/engineering", engineering, methods=["GET"])
app.add_route("/", dashboard, methods=["GET"])
app.add_middleware(APIKeyMiddleware)
