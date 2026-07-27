"""Connector manifest for marketplace listing (Phase C).

Machine-readable metadata describing this MCP connector — served at /manifest
and usable as the basis for a Claude/Vercel marketplace submission.
"""

from __future__ import annotations

import os

MANIFEST = {
    "schema_version": "1.0",
    "name": "distil",
    "display_name": "Distil — Prompt Governance & Compression",
    "description": (
        "A governance + token-optimization gateway for LLM prompts: classifies, "
        "security-scans, moderates, and policy-checks prompts, then compresses "
        "them, with full token/cost/carbon observability."
    ),
    "categories": ["developer-tools", "security", "observability", "ai-infra"],
    "transport": "streamable-http",
    "mcp_endpoint": "/mcp",
    "auth": {
        "type": "api_key",
        "header": "x-api-key",
        "required": bool(os.getenv("CONNECTOR_API_KEY")),
        "note": "Set CONNECTOR_API_KEY to require auth on /mcp.",
    },
    "capabilities": [
        "prompt-compression", "prompt-classification", "content-security",
        "moderation", "package-policy", "code-vuln-scan", "meaning-verification",
        "token-budget", "cost-carbon-metrics", "multi-model-routing",
        "semantic-cache", "anomaly-detection", "savings-forecast",
    ],
    "endpoints": {
        "dashboard": "/",
        "engineering": "/engineering",
        "metrics": "/metrics",
        "violations": "/violations",
        "process": "/process",
        "manifest": "/manifest",
    },
    "pricing_model": "free heuristic tier; LLM features require the operator's key",
    "homepage": "https://github.com/ashritkvs/distil",
}


def get_manifest() -> dict:
    return MANIFEST
