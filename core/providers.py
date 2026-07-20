"""LLM provider integration feasibility (Phase B, spec item 9).

Describes which LLM providers this gateway can integrate with, how, and whether
each is currently configured (key present). The gateway is provider-agnostic:
any MCP client / agent can call it, and target-model estimates already cover
multiple providers. Direct provider calls (LLM compression, verification,
moderation) require the provider's API key.
"""

from __future__ import annotations

import os

_PROVIDERS = [
    {"id": "openai", "name": "OpenAI", "env": "OPENAI_API_KEY",
     "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1"],
     "used_for": ["llm-compression", "verification", "moderation", "embeddings"]},
    {"id": "anthropic", "name": "Anthropic Claude", "env": "ANTHROPIC_API_KEY",
     "models": ["claude-haiku", "claude-sonnet", "claude-opus"],
     "used_for": ["llm-compression", "verification"]},
    {"id": "google", "name": "Google Gemini", "env": "GOOGLE_API_KEY",
     "models": ["gemini-flash", "gemini-pro"], "used_for": ["llm-compression"]},
    {"id": "mistral", "name": "Mistral", "env": "MISTRAL_API_KEY",
     "models": ["mistral-small", "mixtral-8x7b"], "used_for": ["llm-compression"]},
    {"id": "local", "name": "Local / Ollama", "env": "OLLAMA_HOST",
     "models": ["llama-3-8b", "llama-3-70b"], "used_for": ["llm-compression"]},
]


def list_providers() -> dict:
    providers = []
    for p in _PROVIDERS:
        providers.append({
            **p,
            "configured": bool(os.getenv(p["env"])),
            "integration": "MCP connector (any agent) + direct calls when key set",
        })
    return {
        "gateway_type": "provider-agnostic (MCP + REST)",
        "providers": providers,
        "note": ("Heuristic compression + all metrics are provider-independent. "
                 "Only LLM-mode compression / verification / moderation call a "
                 "provider and need its key."),
    }
