"""Package allow/deny policy for code agents (Phase B, spec final item).

Detects package references in prompts/code (import / pip install / npm / require)
and evaluates them against a configurable allowlist or denylist. Used by the
governance orchestrator to block disallowed dependencies.

Config (env):
  DISTIL_POLICY_MODE      = "denylist" (default) | "allowlist"
  DISTIL_DENIED_PACKAGES  = comma list (denylist mode)
  DISTIL_ALLOWED_PACKAGES = comma list (allowlist mode)
"""

from __future__ import annotations

import os
import re

# Illustrative default denylist (typo-squats / deprecated / risky). Override
# via env for your own policy.
_DEFAULT_DENIED = {
    "pycrypto", "request", "urllib2", "python-sqlite", "django-rest-framework",
    "beautifulsoup", "sklearn", "ftplib", "telnetlib", "pickle5",
}

_PATTERNS = [
    re.compile(r"\bimport\s+([a-zA-Z0-9_]+)"),
    re.compile(r"\bfrom\s+([a-zA-Z0-9_]+)\s+import"),
    re.compile(r"\bpip\s+install\s+([a-zA-Z0-9_\-\[\]=<>.]+)", re.I),
    re.compile(r"\bnpm\s+(?:install|i|add)\s+([a-zA-Z0-9_\-@/]+)", re.I),
    re.compile(r"\byarn\s+add\s+([a-zA-Z0-9_\-@/]+)", re.I),
    re.compile(r"\brequire\(\s*['\"]([a-zA-Z0-9_\-@/]+)['\"]", re.I),
]


def _detect(text: str) -> list[str]:
    found: set[str] = set()
    for rx in _PATTERNS:
        for m in rx.findall(text):
            name = re.split(r"[=<>\[]", m)[0].strip().lower()
            if name and name not in ("os", "sys", "re", "json", "time", "math"):
                found.add(name)
    return sorted(found)


def _config() -> tuple[str, set[str], set[str]]:
    mode = os.getenv("DISTIL_POLICY_MODE", "denylist").lower()
    denied = {p.strip().lower() for p in os.getenv("DISTIL_DENIED_PACKAGES", "").split(",") if p.strip()}
    allowed = {p.strip().lower() for p in os.getenv("DISTIL_ALLOWED_PACKAGES", "").split(",") if p.strip()}
    if not denied:
        denied = set(_DEFAULT_DENIED)
    return mode, denied, allowed


def check_packages(text: str) -> dict:
    """Return detected packages and any policy violations."""
    mode, denied, allowed = _config()
    detected = _detect(text)
    if mode == "allowlist" and allowed:
        violations = [p for p in detected if p not in allowed]
    else:
        violations = [p for p in detected if p in denied]
    return {
        "policy_mode": mode,
        "detected_packages": detected,
        "violations": violations,
        "note": "Configure via DISTIL_POLICY_MODE / DISTIL_DENIED_PACKAGES / DISTIL_ALLOWED_PACKAGES.",
    }
