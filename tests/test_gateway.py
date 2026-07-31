"""Tests for the LLM Gateway (core.gateway) — request rewriting, fail-safe
passthrough, and governance modes. All offline: no real provider is called;
httpx forwarding in api/gateway_routes.py is exercised via ASGI TestClient
with a stubbed transport so no network egress happens."""

from __future__ import annotations

import json

import httpx
import pytest

from core import gateway as gw

SSN_PROMPT = "My SSN is 123-45-6789, please summarize this."
VERBOSE_PROMPT = (
    "Could you please possibly help me understand in a very detailed and "
    "comprehensive way what machine learning actually is?"
)


# --------------------------------------------------------------------------- #
# core.gateway — request rewriting
# --------------------------------------------------------------------------- #

def test_openai_rewrite_compresses_user_not_system():
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": VERBOSE_PROMPT},
            {"role": "user", "content": VERBOSE_PROMPT},
        ],
    }
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "off", True, False)
    assert new_body["messages"][0]["content"] == VERBOSE_PROMPT  # system untouched
    assert new_body["messages"][1]["content"] != VERBOSE_PROMPT  # user compressed
    assert acc.original_tokens > 0
    assert acc.sent_tokens < acc.original_tokens
    assert acc.tokens_saved > 0


def test_openai_rewrite_compress_system_header():
    body = {"model": "gpt-4o-mini", "messages": [
        {"role": "system", "content": VERBOSE_PROMPT},
        {"role": "user", "content": "hi"},
    ]}
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "off", True, True)
    assert new_body["messages"][0]["content"] != VERBOSE_PROMPT


def test_openai_rewrite_never_touches_tool_calls_or_tool_role():
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": "call the weather tool"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "1", "type": "function",
                             "function": {"name": "get_weather", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": VERBOSE_PROMPT},
        ],
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
    }
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "off", True, False)
    assert new_body["messages"][1] == body["messages"][1]
    assert new_body["messages"][2]["content"] == VERBOSE_PROMPT  # tool role untouched
    assert new_body["tools"] == body["tools"]  # schema untouched


def test_openai_rewrite_multimodal_content_parts():
    body = {"model": "gpt-4o-mini", "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": VERBOSE_PROMPT},
            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
        ]},
    ]}
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "off", True, False)
    parts = new_body["messages"][0]["content"]
    assert parts[0]["text"] != VERBOSE_PROMPT
    assert parts[1] == {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}


def test_openai_rewrite_compress_off_leaves_text_unchanged():
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": VERBOSE_PROMPT}]}
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "off", False, False)
    assert new_body["messages"][0]["content"] == VERBOSE_PROMPT
    assert acc.original_tokens == 0
    assert acc.sent_tokens == 0


def test_anthropic_rewrite_compresses_user_leaves_system_and_assistant():
    body = {
        "model": "claude-3-5-sonnet-20241022",
        "system": "You are a helpful assistant.",
        "messages": [
            {"role": "user", "content": VERBOSE_PROMPT},
            {"role": "assistant", "content": VERBOSE_PROMPT},
        ],
    }
    new_body, acc = gw.rewrite_anthropic_body(body, 0.5, "off", True, False)
    assert new_body["system"] == "You are a helpful assistant."
    assert new_body["messages"][0]["content"] != VERBOSE_PROMPT
    assert new_body["messages"][1]["content"] == VERBOSE_PROMPT  # assistant untouched


def test_anthropic_rewrite_leaves_tool_use_blocks():
    body = {"model": "claude-3-5-sonnet-20241022", "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": VERBOSE_PROMPT},
            {"type": "tool_result", "tool_use_id": "t1", "content": "42"},
        ]},
    ]}
    new_body, acc = gw.rewrite_anthropic_body(body, 0.5, "off", True, False)
    blocks = new_body["messages"][0]["content"]
    assert blocks[0]["text"] != VERBOSE_PROMPT
    assert blocks[1] == {"type": "tool_result", "tool_use_id": "t1", "content": "42"}


def test_gemini_rewrite_compresses_user_leaves_model_turns():
    body = {"contents": [
        {"role": "user", "parts": [{"text": VERBOSE_PROMPT}]},
        {"role": "model", "parts": [{"text": VERBOSE_PROMPT}]},
    ]}
    new_body, acc = gw.rewrite_gemini_body(body, 0.5, "off", True, False, "gemini-1.5-flash")
    assert new_body["contents"][0]["parts"][0]["text"] != VERBOSE_PROMPT
    assert new_body["contents"][1]["parts"][0]["text"] == VERBOSE_PROMPT


def test_gemini_rewrite_system_instruction_opt_in():
    body = {
        "systemInstruction": {"parts": [{"text": VERBOSE_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    }
    off, _ = gw.rewrite_gemini_body(body, 0.5, "off", True, False)
    assert off["systemInstruction"]["parts"][0]["text"] == VERBOSE_PROMPT
    on, _ = gw.rewrite_gemini_body(body, 0.5, "off", True, True)
    assert on["systemInstruction"]["parts"][0]["text"] != VERBOSE_PROMPT


# --------------------------------------------------------------------------- #
# Fail-safe: compression/governance errors never break the request.
# --------------------------------------------------------------------------- #

def test_fail_safe_on_compression_exception(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("compression exploded")
    monkeypatch.setattr(gw, "_compress", boom)
    r = gw.govern_and_compress("hello there friend", 0.5, "off")
    assert r["blocked"] is False
    assert r["compressed_text"] == "hello there friend"  # fell back to original


def test_fail_safe_on_governance_exception(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("governance exploded")
    monkeypatch.setattr(gw, "govern", boom)
    r = gw.govern_and_compress(VERBOSE_PROMPT, 0.5, "enforce")
    # governance failed -> treated as allow -> compression still runs normally
    assert r["blocked"] is False
    assert r["verdict"] == "allow"


def test_fail_safe_never_ships_empty_compressed_text(monkeypatch):
    class FakeResult:
        compressed_text = "   "
        compressed_tokens = 0
    monkeypatch.setattr(gw, "_compress", lambda *a, **kw: FakeResult())
    r = gw.govern_and_compress("some real content here", 0.5, "off")
    assert r["compressed_text"] == "some real content here"


def test_openai_rewrite_survives_field_level_exception(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(gw, "_compress", boom)
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": VERBOSE_PROMPT}]}
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "off", True, False)
    assert new_body["messages"][0]["content"] == VERBOSE_PROMPT


# --------------------------------------------------------------------------- #
# Governance modes: off / log / enforce
# --------------------------------------------------------------------------- #

def test_govern_off_never_blocks_even_dangerous_text():
    r = gw.govern_and_compress(SSN_PROMPT, 0.5, "off")
    assert r["blocked"] is False
    assert r["verdict"] == "allow"  # governance didn't even run


def test_govern_log_flags_but_does_not_block():
    r = gw.govern_and_compress(SSN_PROMPT, 0.5, "log")
    assert r["blocked"] is False
    assert r["verdict"] == "block"  # verdict computed and surfaced...
    assert r["reasons"]
    assert r["compressed_text"]  # ...but the request is not stopped


def test_govern_enforce_blocks():
    r = gw.govern_and_compress(SSN_PROMPT, 0.5, "enforce")
    assert r["blocked"] is True
    assert r["verdict"] == "block"
    assert r["compressed_text"] == SSN_PROMPT  # original text carried through, uncompressed


def test_openai_rewrite_enforce_blocks_whole_request():
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": SSN_PROMPT}]}
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "enforce", True, False)
    assert acc.blocked is not None
    assert acc.blocked["verdict"] == "block"


def test_compress_off_govern_log_still_governs():
    """The two knobs are independent: compress=off must not silently also
    disable governance."""
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": SSN_PROMPT}]}
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "log", False, False)
    assert new_body["messages"][0]["content"] == SSN_PROMPT  # not compressed
    # governance still ran (log mode never blocks, but doesn't crash / skip either)


def test_compress_off_govern_enforce_still_blocks():
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": SSN_PROMPT}]}
    new_body, acc = gw.rewrite_openai_body(body, 0.5, "enforce", False, False)
    assert acc.blocked is not None


# --------------------------------------------------------------------------- #
# Tenant hashing — never store the raw provider key.
# --------------------------------------------------------------------------- #

def test_tenant_id_is_a_stable_hash_not_the_raw_key():
    key = "sk-super-secret-openai-key"
    tid = gw.tenant_id(key)
    assert tid != key
    assert key not in tid
    assert tid == gw.tenant_id(key)  # stable
    assert gw.tenant_id(None) == "anon"


# --------------------------------------------------------------------------- #
# HTTP layer — ASGI routes with a stubbed upstream transport (no real network).
# --------------------------------------------------------------------------- #

@pytest.fixture
def app_client(monkeypatch):
    """Build a Starlette TestClient with the real gateway routes but a fake
    httpx transport standing in for the real provider APIs."""
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("DISTIL_API_KEYS", raising=False)
    from starlette.applications import Starlette
    from starlette.routing import Route
    from api import gateway_routes as routes

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        return httpx.Response(200, json={"echo": payload, "url": str(request.url)})

    fake_transport = httpx.MockTransport(handler)
    routes._client = httpx.AsyncClient(transport=fake_transport)

    app = Starlette(routes=[
        Route("/v1/chat/completions", routes.openai_chat_completions, methods=["POST"]),
        Route("/v1/messages", routes.anthropic_messages, methods=["POST"]),
        Route("/v1beta/models/{model_action}", routes.gemini_generate_content, methods=["POST"]),
    ])
    from starlette.testclient import TestClient
    with TestClient(app) as client:
        yield client
    routes._client = None


def test_openai_route_forwards_compressed_body_and_sets_headers(app_client):
    resp = app_client.post("/v1/chat/completions",
                           headers={"Authorization": "Bearer sk-test123"},
                           json={"model": "gpt-4o-mini",
                                "messages": [{"role": "user", "content": VERBOSE_PROMPT}]})
    assert resp.status_code == 200
    assert int(resp.headers["x-distil-original-tokens"]) > int(resp.headers["x-distil-sent-tokens"])
    assert int(resp.headers["x-distil-tokens-saved"]) > 0
    forwarded = resp.json()["echo"]
    assert forwarded["messages"][0]["content"] != VERBOSE_PROMPT


def test_openai_route_missing_key_returns_401(app_client):
    resp = app_client.post("/v1/chat/completions",
                           json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "missing_api_key"


def test_openai_route_enforce_governance_blocks_before_forwarding(app_client):
    resp = app_client.post("/v1/chat/completions",
                           headers={"Authorization": "Bearer sk-test123",
                                   "x-distil-govern": "enforce"},
                           json={"model": "gpt-4o-mini",
                                "messages": [{"role": "user", "content": SSN_PROMPT}]})
    assert resp.status_code == 400
    assert "distil governance" in resp.json()["error"]["message"].lower()


def test_anthropic_route_forwards_and_sets_headers(app_client):
    resp = app_client.post("/v1/messages",
                           headers={"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"},
                           json={"model": "claude-3-5-sonnet-20241022", "max_tokens": 100,
                                "messages": [{"role": "user", "content": VERBOSE_PROMPT}]})
    assert resp.status_code == 200
    assert int(resp.headers["x-distil-tokens-saved"]) > 0


def test_gemini_route_forwards_and_sets_headers(app_client):
    resp = app_client.post("/v1beta/models/gemini-1.5-flash:generateContent?key=abc123",
                           json={"contents": [{"role": "user", "parts": [{"text": VERBOSE_PROMPT}]}]})
    assert resp.status_code == 200
    assert int(resp.headers["x-distil-tokens-saved"]) > 0
    forwarded = resp.json()["echo"]
    assert forwarded["contents"][0]["parts"][0]["text"] != VERBOSE_PROMPT


def test_gateway_compress_off_header_disables_compression(app_client):
    resp = app_client.post("/v1/chat/completions",
                           headers={"Authorization": "Bearer sk-test123",
                                   "x-distil-compress": "off"},
                           json={"model": "gpt-4o-mini",
                                "messages": [{"role": "user", "content": VERBOSE_PROMPT}]})
    assert resp.status_code == 200
    forwarded = resp.json()["echo"]
    assert forwarded["messages"][0]["content"] == VERBOSE_PROMPT
    assert resp.headers["x-distil-tokens-saved"] == "0"
