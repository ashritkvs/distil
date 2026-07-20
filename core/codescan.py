"""Lightweight code-vulnerability scan (Phase C, spec item 6).

Rule-based detection of dangerous patterns in code found within prompts /
generated content. Serverless-friendly (no Semgrep binary). It FLAGS risky
patterns for review — it does not GUARANTEE code is safe (stated honestly).
"""

from __future__ import annotations

import re

_CODE_HINT = re.compile(r"```|(?:^|\s)(?:def|class|import|return|function)\s|=>|;\s*$")

_RULES = [
    ("eval_or_exec", "high", re.compile(r"\b(?:eval|exec)\s*\(")),
    ("os_system", "high", re.compile(r"\bos\.system\s*\(")),
    ("subprocess_shell_true", "high",
     re.compile(r"subprocess\.(?:call|run|Popen)\([^)]*shell\s*=\s*True")),
    ("pickle_loads", "high", re.compile(r"\bpickle\.loads?\s*\(")),
    ("yaml_unsafe_load", "medium",
     re.compile(r"\byaml\.load\s*\((?![^)]*SafeLoader)")),
    ("sql_injection_fstring", "high",
     re.compile(r"(?:execute|executemany)\s*\(\s*f?['\"].*(?:\{|\%s|\+)")),
    ("weak_hash", "low", re.compile(r"\bhashlib\.(?:md5|sha1)\s*\(")),
    ("hardcoded_secret", "high",
     re.compile(r"(?:password|secret|api_key|token)\s*=\s*['\"][^'\"]{6,}['\"]", re.I)),
    ("eval_input", "high", re.compile(r"eval\s*\(\s*input")),
    ("tls_verify_disabled", "medium", re.compile(r"verify\s*=\s*False")),
    ("wildcard_bind", "low", re.compile(r"0\.0\.0\.0")),
]

_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def scan_code(text: str) -> dict:
    """Scan `text` for known-dangerous code patterns.

    The dangerous-pattern rules run unconditionally (they rarely appear in
    prose), so inline snippets like `eval(user_input)` are caught even without
    obvious code markers.
    """
    findings = []
    risk = "none"
    for name, sev, rx in _RULES:
        if rx.search(text):
            findings.append({"type": name, "severity": sev})
            if _ORDER[sev] > _ORDER[risk]:
                risk = sev
    has_code = bool(findings) or bool(_CODE_HINT.search(text))
    return {
        "has_code": has_code,
        "risk_level": risk,
        "finding_count": len(findings),
        "findings": findings,
        "method": "rule-based",
        "note": "Flags risky patterns; not a guarantee of safe code.",
    }
