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
app.add_route("/engineering", engineering, methods=["GET"])
app.add_route("/", dashboard, methods=["GET"])
app.add_middleware(APIKeyMiddleware)
