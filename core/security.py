"""Content security scanning (Phase A, spec item 5).

Detects PII, secrets, and prompt-injection attempts in prompt content. Pure
regex/heuristics — fast, serverless, no API. Catches common cases; NOT an
enterprise-grade guarantee (flagged honestly in the output).
"""

from __future__ import annotations

import re

# (type, severity, compiled pattern)
_PII = [
    ("email", "low", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone", "low", re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("ssn", "high", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", "high", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("ip_address", "low", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]

_SECRETS = [
    ("openai_key", "high", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("aws_key", "high", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", "high", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("generic_token", "medium", re.compile(r"\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}", re.I)),
]

_INJECTION = [
    ("ignore_instructions", "high", re.compile(r"ignore (all |the |your |previous |above )*(instructions|prompt|rules|directions)", re.I)),
    ("override_system", "high", re.compile(r"(disregard|forget|override).{0,20}(system|previous|prior).{0,20}(prompt|instruction|message)", re.I)),
    ("reveal_prompt", "medium", re.compile(r"(reveal|show|print|repeat|output).{0,20}(your |the )?(system )?(prompt|instructions)", re.I)),
    ("role_hijack", "medium", re.compile(r"you are now (a |an )?|act as (a |an )?(?:dan|jailbreak|unrestricted)", re.I)),
    ("jailbreak", "high", re.compile(r"\b(jailbreak|\bDAN\b mode|developer mode|do anything now)\b", re.I)),
]

_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _scan(groups, text) -> list[dict]:
    out = []
    for kind, severity, rx in groups:
        m = rx.search(text)
        if m:
            frag = m.group(0)
            out.append({"type": kind, "severity": severity,
                        "match_preview": (frag[:24] + "…") if len(frag) > 24 else frag})
    return out


def scan_content(text: str) -> dict:
    """Scan prompt content for PII, secrets, and injection attempts."""
    findings = _scan(_PII, text) + _scan(_SECRETS, text) + _scan(_INJECTION, text)
    risk = "none"
    for f in findings:
        if _ORDER[f["severity"]] > _ORDER[risk]:
            risk = f["severity"]
    return {
        "risk_level": risk,
        "finding_count": len(findings),
        "findings": findings,
        "method": "regex-heuristic",
        "note": "Common-case detection, not an enterprise-grade guarantee.",
    }
