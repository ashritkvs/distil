"""LLM Gateway routes — thin HTTP layer over `core.gateway`.

Drop-in OpenAI / Anthropic / Gemini compatible endpoints. Each handler:
  1. parses the provider-shaped JSON body,
  2. rewrites eligible text fields through governance + compression
     (core.gateway — fail-safe, never raises out to here),
  3. forwards the (possibly rewritten) body to the real provider with the
     customer's own API key via httpx,
  4. streams or returns the provider's response byte-for-byte, with
     `x-distil-*` savings headers attached.

Distil never stores the customer's provider key — only a one-way hash of it
is kept, and only as an in-memory rate-limit/metering identity.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

import httpx
from starlette.responses import JSONResponse, Response, StreamingResponse

from core import gateway as gw

_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_HOP_BY_HOP = {"content-length", "content-encoding", "transfer-encoding",
              "connection", "keep-alive",
              # The ASGI server (uvicorn) emits its own date/server headers
              # regardless — forwarding the upstream's would duplicate them.
              "date", "server"}

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


def _filtered_headers(headers) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _config(request) -> tuple[float, str, bool, bool]:
    """Read the x-distil-* config knobs off the request, with sane defaults."""
    raw_ratio = request.headers.get("x-distil-ratio")
    try:
        target_ratio = float(raw_ratio) if raw_ratio else 0.5
    except ValueError:
        target_ratio = 0.5
    target_ratio = min(max(target_ratio, 0.05), 1.0)

    govern_mode = (request.headers.get("x-distil-govern") or "log").strip().lower()
    if govern_mode not in ("off", "log", "enforce"):
        govern_mode = "log"

    def _bool(name: str, default: bool) -> bool:
        v = request.headers.get(name)
        if v is None:
            return default
        return v.strip().lower() in ("1", "true", "on", "yes")

    compress_on = _bool("x-distil-compress", True)
    compress_system = _bool("x-distil-compress-system", False)
    return target_ratio, govern_mode, compress_on, compress_system


def _savings_headers(acc: gw.RewriteResult) -> dict:
    return {
        "x-distil-original-tokens": str(acc.original_tokens),
        "x-distil-sent-tokens": str(acc.sent_tokens),
        "x-distil-tokens-saved": str(acc.tokens_saved),
    }


def _rate_limited(tenant: str):
    """Returns a 429 Response if `tenant` is over Distil's own request-rate
    budget, else None. Reuses the existing rate limiter (in-memory or
    Upstash) so the gateway shares abuse protection with the rest of Distil."""
    from core.ratelimit import limiter

    rl = limiter.check(tenant)
    if rl["allowed"]:
        return None
    return JSONResponse(
        {"error": "distil_rate_limit_exceeded", "limit": rl["limit"],
         "reset_in_seconds": rl["reset_in"]},
        status_code=429,
        headers={"Retry-After": str(rl["reset_in"])})


async def _forward_json(method: str, url: str, headers: dict, body_bytes: bytes,
                        extra_headers: dict) -> Response:
    try:
        resp = await _http().request(method, url, headers=headers, content=body_bytes)
    except httpx.RequestError as e:
        return JSONResponse(
            {"error": {"message": f"Distil gateway: upstream request failed: {e}",
                      "type": "distil_gateway_error"}},
            status_code=502, headers=extra_headers)
    out_headers = _filtered_headers(resp.headers)
    out_headers.update(extra_headers)
    return Response(content=resp.content, status_code=resp.status_code,
                    headers=out_headers, media_type=resp.headers.get("content-type"))


async def _forward_stream(method: str, url: str, headers: dict, body_bytes: bytes,
                          extra_headers: dict) -> Response:
    client = _http()
    req = client.build_request(method, url, headers=headers, content=body_bytes)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.RequestError as e:
        return JSONResponse(
            {"error": {"message": f"Distil gateway: upstream request failed: {e}",
                      "type": "distil_gateway_error"}},
            status_code=502, headers=extra_headers)

    out_headers = _filtered_headers(upstream.headers)
    out_headers.update(extra_headers)

    async def body_iter():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(body_iter(), status_code=upstream.status_code,
                             headers=out_headers,
                             media_type=upstream.headers.get("content-type"))


# --------------------------------------------------------------------------- #
# OpenAI  POST /v1/chat/completions
# --------------------------------------------------------------------------- #

async def openai_chat_completions(request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(gw.openai_error("Invalid JSON body.", code="invalid_json"),
                            status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return JSONResponse(
            gw.openai_error("Request must include a 'messages' array.",
                            code="missing_messages"),
            status_code=400)

    auth = request.headers.get("authorization", "")
    if not auth:
        return JSONResponse(
            gw.openai_error("Missing Authorization header with your OpenAI API key.",
                            code="missing_api_key"),
            status_code=401)

    tenant = gw.tenant_id(auth)
    if resp := _rate_limited(tenant):
        return resp

    target_ratio, govern_mode, compress_on, compress_system = _config(request)
    new_body, acc = gw.rewrite_openai_body(body, target_ratio, govern_mode,
                                           compress_on, compress_system)
    headers = _savings_headers(acc)
    gw.record_gateway_usage(tenant, "openai", body.get("model"), acc)

    if acc.blocked:
        reasons = ", ".join(acc.blocked.get("reasons", [])) or "policy violation"
        return JSONResponse(
            gw.openai_error(f"Request blocked by Distil governance: {reasons}"),
            status_code=400, headers=headers)

    fwd_headers = {"content-type": "application/json", "authorization": auth}
    for h in ("openai-organization", "openai-project"):
        if v := request.headers.get(h):
            fwd_headers[h] = v

    body_bytes = json.dumps(new_body).encode("utf-8")
    if new_body.get("stream"):
        return await _forward_stream("POST", _OPENAI_URL, fwd_headers, body_bytes, headers)
    return await _forward_json("POST", _OPENAI_URL, fwd_headers, body_bytes, headers)


# --------------------------------------------------------------------------- #
# Anthropic  POST /v1/messages
# --------------------------------------------------------------------------- #

async def anthropic_messages(request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(gw.anthropic_error("Invalid JSON body."), status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return JSONResponse(
            gw.anthropic_error("Request must include a 'messages' array."),
            status_code=400)

    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        return JSONResponse(
            gw.anthropic_error("Missing x-api-key header with your Anthropic API key."),
            status_code=401)

    tenant = gw.tenant_id(api_key)
    if resp := _rate_limited(tenant):
        return resp

    target_ratio, govern_mode, compress_on, compress_system = _config(request)
    new_body, acc = gw.rewrite_anthropic_body(body, target_ratio, govern_mode,
                                              compress_on, compress_system)
    headers = _savings_headers(acc)
    gw.record_gateway_usage(tenant, "anthropic", body.get("model"), acc)

    if acc.blocked:
        reasons = ", ".join(acc.blocked.get("reasons", [])) or "policy violation"
        return JSONResponse(
            gw.anthropic_error(f"Request blocked by Distil governance: {reasons}"),
            status_code=400, headers=headers)

    fwd_headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
        # Required by Anthropic on every request; default to a known-good
        # version if the client didn't send one.
        "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
    }
    if beta := request.headers.get("anthropic-beta"):
        fwd_headers["anthropic-beta"] = beta

    body_bytes = json.dumps(new_body).encode("utf-8")
    if new_body.get("stream"):
        return await _forward_stream("POST", _ANTHROPIC_URL, fwd_headers, body_bytes, headers)
    return await _forward_json("POST", _ANTHROPIC_URL, fwd_headers, body_bytes, headers)


# --------------------------------------------------------------------------- #
# Gemini  POST /v1beta/models/{model}:generateContent | :streamGenerateContent
# --------------------------------------------------------------------------- #

async def gemini_generate_content(request):
    model_action = request.path_params.get("model_action", "")
    if ":" not in model_action:
        return JSONResponse(
            gw.gemini_error("Expected path '/v1beta/models/{model}:generateContent'."),
            status_code=400)
    model, action = model_action.rsplit(":", 1)
    if action not in ("generateContent", "streamGenerateContent"):
        return JSONResponse(gw.gemini_error(f"Unsupported action '{action}'."),
                            status_code=404)
    streaming = action == "streamGenerateContent"

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(gw.gemini_error("Invalid JSON body."), status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("contents"), list):
        return JSONResponse(
            gw.gemini_error("Request must include a 'contents' array."), status_code=400)

    api_key = request.query_params.get("key") or request.headers.get("x-goog-api-key", "")
    if not api_key:
        return JSONResponse(
            gw.gemini_error("Missing API key (?key= query param or x-goog-api-key header)."),
            status_code=401)

    tenant = gw.tenant_id(api_key)
    if resp := _rate_limited(tenant):
        return resp

    target_ratio, govern_mode, compress_on, compress_system = _config(request)
    new_body, acc = gw.rewrite_gemini_body(body, target_ratio, govern_mode,
                                           compress_on, compress_system, target_model=model)
    headers = _savings_headers(acc)
    gw.record_gateway_usage(tenant, "gemini", model, acc)

    if acc.blocked:
        reasons = ", ".join(acc.blocked.get("reasons", [])) or "policy violation"
        return JSONResponse(
            gw.gemini_error(f"Request blocked by Distil governance: {reasons}"),
            status_code=400, headers=headers)

    fwd_headers = {"content-type": "application/json"}
    if gkey := request.headers.get("x-goog-api-key"):
        fwd_headers["x-goog-api-key"] = gkey

    qp = dict(request.query_params)
    if streaming and "alt" not in qp:
        # Google's REST examples use SSE framing for streamGenerateContent;
        # default to it so the passthrough is a clean text/event-stream.
        qp["alt"] = "sse"
    upstream_url = f"{_GEMINI_BASE}/{model}:{action}"
    if qp:
        upstream_url += "?" + urlencode(qp)

    body_bytes = json.dumps(new_body).encode("utf-8")
    if streaming:
        return await _forward_stream("POST", upstream_url, fwd_headers, body_bytes, headers)
    return await _forward_json("POST", upstream_url, fwd_headers, body_bytes, headers)
