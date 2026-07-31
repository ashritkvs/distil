"""LLM Gateway core — provider-agnostic request rewriting for the drop-in proxy.

Pure logic, no networking. `api/gateway_routes.py` does the HTTP forwarding;
everything here is: pull the text worth compressing out of a provider's
request body, run it through governance + compression, and put it back.

Fail-safe by construction: every text-processing call is wrapped so that any
exception in governance or compression degrades to "use the original text"
rather than breaking the customer's request. The one place that legitimately
stops the request is an `enforce`-mode governance block, which is returned
as a `blocked` result for the route layer to turn into a provider-shaped error.
"""

from __future__ import annotations

import hashlib

from core import estimates
from core.compressor import compress as _compress
from core.governance import govern
from core.intelligence import count_tokens

GovernMode = str  # "off" | "log" | "enforce"


def tenant_id(raw_key: str | None) -> str:
    """Hash a customer-supplied provider key into a stable, non-reversible
    tenant identity for rate-limiting/metering. Distil never stores or logs
    the raw key — only this hash."""
    if not raw_key:
        return "anon"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]


def govern_and_compress(text: str, target_ratio: float, govern_mode: GovernMode,
                        target_model: str | None = None, do_compress: bool = True) -> dict:
    """Govern (per mode) then compress one text block. Never raises.

    `do_compress=False` still runs governance (per `govern_mode`) but skips
    the compression step — lets the two `x-distil-*` knobs act independently.

    Returns a dict with: blocked, verdict, reasons, governance,
    compressed_text, original_tokens, compressed_tokens.
    """
    try:
        original_tokens = count_tokens(text)
    except Exception:
        original_tokens = 0

    verdict, reasons, report = "allow", [], None
    if govern_mode in ("log", "enforce"):
        try:
            report = govern(text)
            verdict = report["verdict"]
            reasons = report["reasons"]
        except Exception:
            verdict, reasons, report = "allow", [], None

    if govern_mode == "enforce" and verdict == "block":
        return {
            "blocked": True, "verdict": verdict, "reasons": reasons,
            "governance": report, "compressed_text": text,
            "original_tokens": original_tokens, "compressed_tokens": original_tokens,
        }

    if not do_compress:
        return {
            "blocked": False, "verdict": verdict, "reasons": reasons,
            "governance": report, "compressed_text": text,
            "original_tokens": original_tokens, "compressed_tokens": original_tokens,
        }

    try:
        result = _compress(text, target_ratio=target_ratio, quality=False,
                           target_model=target_model, use_cache=True)
        compressed_text = result.compressed_text
        compressed_tokens = result.compressed_tokens
        if not compressed_text.strip():
            # Never ship an empty prompt because compression over-trimmed it.
            compressed_text, compressed_tokens = text, original_tokens
    except Exception:
        compressed_text, compressed_tokens = text, original_tokens

    return {
        "blocked": False, "verdict": verdict, "reasons": reasons,
        "governance": report, "compressed_text": compressed_text,
        "original_tokens": original_tokens, "compressed_tokens": compressed_tokens,
    }


class RewriteResult:
    def __init__(self):
        self.original_tokens = 0
        self.sent_tokens = 0
        self.blocked: dict | None = None

    def add(self, r: dict):
        self.original_tokens += r["original_tokens"]
        self.sent_tokens += r["compressed_tokens"]
        if r["blocked"]:
            self.blocked = r

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.sent_tokens)


def _compress_field(text: str, target_ratio: float, govern_mode: GovernMode,
                    target_model: str | None, acc: RewriteResult,
                    do_compress: bool = True) -> str:
    if not text or not text.strip():
        return text
    r = govern_and_compress(text, target_ratio, govern_mode, target_model, do_compress)
    acc.add(r)
    return r["compressed_text"] if not r["blocked"] else text


# --------------------------------------------------------------------------- #
# OpenAI  (POST /v1/chat/completions)
# --------------------------------------------------------------------------- #

def rewrite_openai_body(body: dict, target_ratio: float, govern_mode: GovernMode,
                        compress: bool, compress_system: bool) -> tuple[dict, RewriteResult]:
    """Rewrite an OpenAI chat-completions request body in place-safe fashion.

    Compresses text content of "user" role messages (and "system" if
    `compress_system`). Never touches "assistant"/"tool"/"function" messages,
    `tool_calls`, or the top-level `tools`/`functions` schema arrays.
    """
    acc = RewriteResult()
    messages = body.get("messages")
    if (not compress and govern_mode == "off") or not isinstance(messages, list):
        return body, acc

    target_model = body.get("model")
    new_messages = []
    for msg in messages:
        if not isinstance(msg, dict):
            new_messages.append(msg)
            continue
        role = msg.get("role")
        eligible = role == "user" or (compress_system and role == "system")
        content = msg.get("content")
        if not eligible or content is None or msg.get("tool_calls"):
            new_messages.append(msg)
            if acc.blocked:
                break
            continue

        if isinstance(content, str):
            new_msg = dict(msg)
            new_msg["content"] = _compress_field(content, target_ratio, govern_mode,
                                                 target_model, acc, compress)
            new_messages.append(new_msg)
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                    new_part = dict(part)
                    new_part["text"] = _compress_field(part["text"], target_ratio,
                                                        govern_mode, target_model, acc, compress)
                    new_parts.append(new_part)
                else:
                    new_parts.append(part)
            new_msg = dict(msg)
            new_msg["content"] = new_parts
            new_messages.append(new_msg)
        else:
            new_messages.append(msg)

        if acc.blocked:
            break

    new_body = dict(body)
    new_body["messages"] = new_messages
    return new_body, acc


def openai_error(message: str, status: int = 400, code: str = "content_policy_violation") -> dict:
    """Shape matches OpenAI's documented error envelope."""
    return {"error": {"message": message, "type": "invalid_request_error",
                      "param": None, "code": code}}


# --------------------------------------------------------------------------- #
# Anthropic  (POST /v1/messages)
# --------------------------------------------------------------------------- #

def rewrite_anthropic_body(body: dict, target_ratio: float, govern_mode: GovernMode,
                           compress: bool, compress_system: bool) -> tuple[dict, RewriteResult]:
    """Rewrite an Anthropic Messages API request body.

    Compresses text blocks of "user" role messages. `system` (top-level,
    string or block list) is left untouched unless `compress_system`. Never
    touches "assistant" messages, `tool_use`/`tool_result` blocks, or `tools`.
    """
    acc = RewriteResult()
    if not compress and govern_mode == "off":
        return body, acc

    target_model = body.get("model")
    new_body = dict(body)

    messages = body.get("messages")
    if isinstance(messages, list):
        new_messages = []
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                new_messages.append(msg)
                continue
            content = msg.get("content")
            if isinstance(content, str):
                new_msg = dict(msg)
                new_msg["content"] = _compress_field(content, target_ratio, govern_mode,
                                                     target_model, acc, compress)
                new_messages.append(new_msg)
            elif isinstance(content, list):
                new_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        new_block = dict(block)
                        new_block["text"] = _compress_field(block["text"], target_ratio,
                                                             govern_mode, target_model, acc, compress)
                        new_blocks.append(new_block)
                    else:
                        new_blocks.append(block)  # image / tool_use / tool_result untouched
                new_msg = dict(msg)
                new_msg["content"] = new_blocks
                new_messages.append(new_msg)
            else:
                new_messages.append(msg)
            if acc.blocked:
                break
        new_body["messages"] = new_messages

    if compress_system and not acc.blocked:
        system = body.get("system")
        if isinstance(system, str) and system.strip():
            new_body["system"] = _compress_field(system, target_ratio, govern_mode,
                                                  target_model, acc, compress)
        elif isinstance(system, list):
            new_blocks = []
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    new_block = dict(block)
                    new_block["text"] = _compress_field(block["text"], target_ratio,
                                                         govern_mode, target_model, acc, compress)
                    new_blocks.append(new_block)
                else:
                    new_blocks.append(block)
            new_body["system"] = new_blocks

    return new_body, acc


def anthropic_error(message: str) -> dict:
    """Shape matches Anthropic's documented error envelope."""
    return {"type": "error", "error": {"type": "invalid_request_error", "message": message}}


# --------------------------------------------------------------------------- #
# Gemini  (POST /v1beta/models/{model}:generateContent)
# --------------------------------------------------------------------------- #

def rewrite_gemini_body(body: dict, target_ratio: float, govern_mode: GovernMode,
                        compress: bool, compress_system: bool,
                        target_model: str | None = None) -> tuple[dict, RewriteResult]:
    """Rewrite a Gemini generateContent request body.

    Compresses text parts of "user" role entries in `contents`.
    `systemInstruction` is left untouched unless `compress_system`. Never
    touches "model"-role entries (prior turns) or `functionCall`/`functionResponse`
    parts, or the top-level `tools` schema.
    """
    acc = RewriteResult()
    if not compress and govern_mode == "off":
        return body, acc

    new_body = dict(body)
    contents = body.get("contents")
    if isinstance(contents, list):
        new_contents = []
        for entry in contents:
            if not isinstance(entry, dict) or entry.get("role") != "user":
                new_contents.append(entry)
                continue
            parts = entry.get("parts")
            if isinstance(parts, list):
                new_parts = []
                for part in parts:
                    if isinstance(part, dict) and "text" in part and part.get("text"):
                        new_part = dict(part)
                        new_part["text"] = _compress_field(part["text"], target_ratio,
                                                            govern_mode, target_model, acc, compress)
                        new_parts.append(new_part)
                    else:
                        new_parts.append(part)  # inlineData / functionCall etc. untouched
                new_entry = dict(entry)
                new_entry["parts"] = new_parts
                new_contents.append(new_entry)
            else:
                new_contents.append(entry)
            if acc.blocked:
                break
        new_body["contents"] = new_contents

    if compress_system and not acc.blocked:
        sysi = body.get("systemInstruction")
        if isinstance(sysi, dict) and isinstance(sysi.get("parts"), list):
            new_parts = []
            for part in sysi["parts"]:
                if isinstance(part, dict) and part.get("text"):
                    new_part = dict(part)
                    new_part["text"] = _compress_field(part["text"], target_ratio,
                                                        govern_mode, target_model, acc, compress)
                    new_parts.append(new_part)
                else:
                    new_parts.append(part)
            new_body["systemInstruction"] = {**sysi, "parts": new_parts}

    return new_body, acc


def gemini_error(message: str) -> dict:
    """Best-effort shape based on Google's standard API error envelope
    (Gemini has not published a dedicated error schema doc as of this
    writing — this mirrors the general Google API error convention)."""
    return {"error": {"code": 400, "message": message, "status": "INVALID_ARGUMENT"}}


# --------------------------------------------------------------------------- #
# Metering — feed the same aggregate store used by /process and /compress.
# --------------------------------------------------------------------------- #

def record_gateway_usage(tenant: str, provider: str, target_model: str | None,
                         acc: "RewriteResult") -> None:
    """Record gateway savings into the shared metrics store + tenant usage
    ledger. Best-effort: metering must never fail the customer's request."""
    from core.store import store

    if acc.original_tokens <= 0:
        return
    try:
        model_name, _ = estimates.resolve_model(target_model)
        saved = acc.tokens_saved
        energy = estimates.est_energy_wh(saved)
        entry = {
            "original_text": f"[gateway:{provider}]",
            "original_tokens": acc.original_tokens,
            "compressed_tokens": acc.sent_tokens,
            "tokens_saved": saved,
            "reduction_pct": round(saved / acc.original_tokens * 100, 1) if acc.original_tokens else 0.0,
            "latency_ms": 0.0,
            "est_cost_saved_usd": estimates.est_cost_usd(saved, model_name, "in"),
            "est_carbon_saved_g": estimates.est_carbon_g(energy),
            "est_gpu_ms_saved": estimates.est_compute(acc.original_tokens, model_name)["est_gpu_ms"]
                - estimates.est_compute(acc.sent_tokens, model_name)["est_gpu_ms"],
            "mode": "gateway",
        }
        store.record(entry)
    except Exception:
        pass
    try:
        from core.plans import record_usage
        record_usage(tenant, acc.original_tokens)
    except Exception:
        pass
