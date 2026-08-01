"""Serverless entrypoint — mounts the MCP server over streamable HTTP.

Exposes the FastMCP app as an ASGI `app` for Vercel (Python / Fluid Compute):
  * `/mcp`      — the MCP endpoint (API-key gated when CONNECTOR_API_KEY is set)
  * `/metrics`  — public aggregate metrics JSON
  * `/`         — public read-only dashboard
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse, JSONResponse

from core import compress as _compress
from core.cache import cache
from core.store import store
from mcp_server import mcp

from api.gateway_routes import (
    anthropic_messages,
    gemini_generate_content,
    openai_chat_completions,
)

_DASH_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
_DASHBOARD = os.path.join(_DASH_DIR, "index.html")
_ENGINEERING = os.path.join(_DASH_DIR, "engineering.html")


# Endpoints that consume compute — rate-limited + metered. /answer is open in
# the tool (uses the server's connected OpenAI) but rate-limited to cap cost.
_PROTECTED_PREFIXES = ("/mcp", "/process", "/compress", "/answer")


def _extract_key(request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return request.headers.get("x-api-key", "")


class GatewayMiddleware(BaseHTTPMiddleware):
    """Multi-tenant auth + per-identity rate limiting on compute endpoints.

    Read-only `/metrics`, `/records`, `/violations`, `/manifest`, and the
    dashboards stay public.
    """

    async def dispatch(self, request, call_next):
        from core.ratelimit import auth_required, is_valid_key, limiter

        path = request.url.path
        key = _extract_key(request)
        authed = (not auth_required()) or is_valid_key(key)
        request.state.authed = authed

        if any(path.startswith(p) for p in _PROTECTED_PREFIXES):
            # /mcp always requires a valid key when auth is configured.
            # /process and /compress allow anonymous access but run heuristic-
            # only (no LLM/credit spend) — enforced in the endpoints.
            if path.startswith("/mcp") and auth_required() and not authed:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            identity = key or (request.client.host if request.client else "anon")
            request.state.tenant = identity
            rl = limiter.check(identity)
            if not rl["allowed"]:
                return JSONResponse(
                    {"error": "rate_limit_exceeded", "limit": rl["limit"],
                     "reset_in_seconds": rl["reset_in"]},
                    status_code=429,
                    headers={"Retry-After": str(rl["reset_in"]),
                             "X-RateLimit-Limit": str(rl["limit"]),
                             "X-RateLimit-Remaining": str(rl["remaining"])})
            # Monthly plan quota
            from core.plans import check_quota
            q = check_quota(identity)
            if not q["allowed"]:
                return JSONResponse(
                    {"error": "quota_exceeded", "plan": q["plan"],
                     "usage": q["usage"]}, status_code=402)
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(rl["limit"])
            response.headers["X-RateLimit-Remaining"] = str(rl["remaining"])
            return response
        return await call_next(request)


async def metrics_endpoint(request):
    from core.budget import get_budget
    summary = store.summary()
    summary["cache"] = cache.stats()
    summary["budget"] = get_budget()
    return JSONResponse(summary)


async def records_endpoint(request):
    return JSONResponse(store.all_records()[:100])


async def violations_endpoint(request):
    return JSONResponse(store.violations(50))


async def manifest_endpoint(request):
    from core.manifest import get_manifest
    return JSONResponse(get_manifest())


async def usage_endpoint(request):
    from core.plans import usage
    tenant = getattr(request.state, "tenant", None) or \
        request.headers.get("x-api-key", "anon")
    return JSONResponse(usage(tenant))


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
    authed = getattr(request.state, "authed", True)  # LLM only for authed keys
    try:
        d = process(
            prompt,
            target_ratio=float(data.get("target_ratio", 0.5)),
            quality=bool(data.get("quality", False)) and authed,
            target_model=data.get("target_model") or "gpt-4o-mini",
            safe=bool(data.get("safe", False)) and authed,
            adaptive=bool(data.get("adaptive", False)) and authed,
            enforce=bool(data.get("enforce", True)),
        )
        d["llm_available"] = authed
    except Exception as e:
        store.record_error(type(e).__name__, prompt)
        return JSONResponse({"error": str(e)}, status_code=500)
    _meter(request, d)
    return JSONResponse(d)


async def answer_endpoint(request):
    """End-to-end: govern + compress the prompt, then send the compressed
    prompt to the connected LLM and return its answer."""
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
    try:
        from starlette.concurrency import run_in_threadpool
        from core.answer import answer
        # Run the blocking LLM call off the event loop (required on serverless).
        d = await run_in_threadpool(
            answer,
            prompt,
            target_ratio=float(data.get("target_ratio", 0.5)),
            target_model=data.get("target_model") or "gpt-4o-mini",
            adaptive=bool(data.get("adaptive", False)),
            enforce=bool(data.get("enforce", True)),
        )
    except Exception as e:
        store.record_error(type(e).__name__, prompt)
        return JSONResponse({"error": str(e)}, status_code=500)
    _meter(request, {"original_tokens": d.get("input_tokens_if_original", 0)})
    return JSONResponse(d)


def _meter(request, d):
    """Record billable usage for the tenant (identity set by the middleware)."""
    from core.plans import record_usage
    tenant = getattr(request.state, "tenant", "anon")
    record_usage(tenant, d.get("original_tokens", 0) or 0)


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
    authed = getattr(request.state, "authed", True)
    ratio = float(data.get("target_ratio", 0.5))
    quality = bool(data.get("quality", False)) and authed
    model = data.get("target_model") or "gpt-4o-mini"
    verify = bool(data.get("verify", False)) and authed
    safe = bool(data.get("safe", False)) and authed
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
    _meter(request, d)
    return JSONResponse(d)


def _serve(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse(fallback)


async def dashboard(request):
    return _serve(_DASHBOARD, "<h1>Distil</h1><p>See /metrics</p>")


async def engineering(request):
    return _serve(_ENGINEERING, "<h1>Engineering view</h1><p>See /records</p>")


# ASGI app served by Vercel. MCP at /mcp; dashboards + data public.
app = mcp.streamable_http_app()
app.add_route("/metrics", metrics_endpoint, methods=["GET"])
app.add_route("/records", records_endpoint, methods=["GET"])
app.add_route("/violations", violations_endpoint, methods=["GET"])
app.add_route("/manifest", manifest_endpoint, methods=["GET"])
app.add_route("/usage", usage_endpoint, methods=["GET"])
app.add_route("/compress", compress_endpoint, methods=["POST"])
app.add_route("/process", process_endpoint, methods=["POST"])
app.add_route("/answer", answer_endpoint, methods=["POST"])
app.add_route("/engineering", engineering, methods=["GET"])
app.add_route("/", dashboard, methods=["GET"])

# LLM Gateway (drop-in proxy) — auth is the CUSTOMER's own provider key, not
# Distil's CONNECTOR_API_KEY, so these stay outside GatewayMiddleware's
# key-extraction (which would otherwise treat the provider key as a Distil
# tenant key). Each handler does its own hashed-identity rate limiting.
app.add_route("/v1/chat/completions", openai_chat_completions, methods=["POST"])
app.add_route("/v1/messages", anthropic_messages, methods=["POST"])
app.add_route("/v1beta/models/{model_action}", gemini_generate_content, methods=["POST"])

app.add_middleware(GatewayMiddleware)

# Public API: allow browser-JS callers (the Distil browser extension's
# background worker, customer frontends calling the gateway directly).
# CORS is not an auth boundary — every route here already gates on its own
# key/rate-limit; this only controls which origins a browser will let JS
# read the response from.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["x-distil-original-tokens", "x-distil-sent-tokens",
                    "x-distil-tokens-saved", "x-ratelimit-limit",
                    "x-ratelimit-remaining", "retry-after"],
)


class _StripVercelPrefix:
    """Vercel rewrites all requests to '/api/index/<path>' and (in the current
    platform behavior) delivers that rewritten path to the app. Strip the
    '/api/index' prefix so the real routes match. Backward-safe: if the path
    doesn't carry the prefix (old behavior / local uvicorn), it's untouched."""

    def __init__(self, inner, prefix="/api/index"):
        self.inner = inner
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self.prefix or path.startswith(self.prefix + "/"):
                new = path[len(self.prefix):] or "/"
                scope = dict(scope)
                scope["path"] = new
                scope["raw_path"] = new.encode("utf-8")
        await self.inner(scope, receive, send)


# Outermost wrapper: fixes the path before Starlette routing (passes lifespan
# and everything else straight through).
app = _StripVercelPrefix(app)
