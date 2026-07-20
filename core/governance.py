"""Governance orchestrator — unifies all policy checks into one verdict.

Runs classification + content-security + moderation (Phase A) and, when
available, package-policy + code-vuln checks (Phase B/C). Produces a single
report with a verdict: allow / warn / block, and records violations.
"""

from __future__ import annotations

from core.classify import classify
from core.moderation import moderate
from core.security import scan_content
from core.store import store


def govern(text: str, check_policy: bool = True, check_code: bool = True) -> dict:
    """Run the full governance pipeline and return a unified report."""
    classification = classify(text)
    security = scan_content(text)
    moderation = moderate(text)

    reasons: list[str] = []
    verdict = "allow"

    def escalate(level):
        nonlocal verdict
        order = {"allow": 0, "warn": 1, "block": 2}
        if order[level] > order[verdict]:
            verdict = level

    # Content security
    if security["risk_level"] == "high":
        escalate("block")
        reasons.append(f"security:high ({security['finding_count']} findings)")
    elif security["risk_level"] == "medium":
        escalate("warn")
        reasons.append("security:medium")
    elif security["risk_level"] == "low" and security["finding_count"]:
        escalate("warn")
        reasons.append("security:pii-detected")

    # Moderation
    if moderation["flagged"]:
        escalate("block")
        reasons.append("moderation:" + ",".join(moderation["categories"]))

    report = {
        "verdict": verdict,
        "reasons": reasons,
        "classification": classification,
        "security": security,
        "moderation": moderation,
    }

    # Package policy (Phase B) — optional import so Phase A works standalone.
    if check_policy:
        try:
            from core.policy import check_packages

            pol = check_packages(text)
            report["policy"] = pol
            if pol.get("violations"):
                escalate("block")
                reasons.append("policy:" + ",".join(pol["violations"][:3]))
        except Exception:
            pass

    # Code vulnerability scan (Phase C) — optional import.
    if check_code:
        try:
            from core.codescan import scan_code

            code = scan_code(text)
            report["code_scan"] = code
            if code.get("risk_level") == "high":
                escalate("block")
                reasons.append("code:high")
            elif code.get("findings"):
                escalate("warn")
                reasons.append("code:findings")
        except Exception:
            pass

    report["verdict"] = verdict
    report["reasons"] = reasons

    if verdict != "allow":
        store.record_violation(verdict, reasons, text)
    return report
