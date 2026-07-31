# Distil

A **serverless prompt-compression MCP connector** that compresses prompts fast
and returns **Distil-style metrics** — tokens, cost, latency, compute-load,
energy, and carbon — where every number is either **measured** or a
**clearly-labeled estimate**. See [SPEC.md](SPEC.md) for the full design.

Built around the source whitepaper's *Prompt Intelligence* + token/cost/
compute/energy/carbon layer (the buildable slice — no GPU hardware required).

## Highlights

- **LLM Gateway:** drop-in proxy for OpenAI/Anthropic/Gemini — point your
  `base_url` at Distil and every request is compressed (optionally governed)
  before it reaches the real provider, streaming included. See below.
- **Fast + serverless:** default heuristic compression is pure Python (~3 ms,
  no model, no API key). Optional `gpt-4o-mini` mode for higher quality.
- **MCP connector:** exposes 5 tools + a metrics resource over streamable HTTP.
- **Distil metrics:** token/cost/latency (measured) + energy/carbon/GPU-load
  (estimated, labeled). GPU intent preserved via a compute-load model, not faked.
- **Live dashboard** + public `/metrics` endpoint.
- **Honest by design:** every estimate flagged `estimated: true`; closed-model
  params flagged `params_known: false`.

## LLM Gateway (drop-in proxy) — the business product

Point your existing OpenAI/Anthropic/Gemini client at Distil instead of the
provider directly. Distil compresses the prompt, forwards it to the real
provider **using your own API key**, and streams the answer straight back —
same request/response shape, so your code doesn't change beyond the base URL.

```
your app → Distil (/v1/...)  →  compress + optional governance  →  real provider  →  same answer back to you
```

**One-line change (OpenAI SDK):**

```python
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_OWN_OPENAI_KEY",       # unchanged — sent straight through, never stored
    base_url="https://getdistil.vercel.app/v1",
)
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Could you please possibly explain, in a very detailed way, what a REST API is?"}],
)
```

**curl (proves compression + a normal answer + savings headers):**

```bash
curl -i https://getdistil.vercel.app/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Could you please possibly explain, in a very detailed way, what a REST API is?"}]
  }'
# Response body is a normal OpenAI chat.completion object.
# Response headers include:
#   x-distil-original-tokens, x-distil-sent-tokens, x-distil-tokens-saved
```

Anthropic and Gemini work the same way — only the base URL/path and auth
header change (your existing client library handles that):

| Provider | Base URL you point at | Your key goes in |
|---|---|---|
| OpenAI | `https://getdistil.vercel.app/v1` | `Authorization: Bearer sk-...` |
| Anthropic | `https://getdistil.vercel.app/v1/messages` | `x-api-key: sk-ant-...` (+ `anthropic-version`) |
| Gemini | `https://getdistil.vercel.app/v1beta/models/{model}:generateContent?key=...` | `?key=...` or `x-goog-api-key` |

### Behavior

- **Your key, your bill.** Distil forwards the Authorization/`x-api-key`/`key`
  you send on every request straight to the real provider. Distil never
  stores it — only a one-way hash is kept in memory, used solely as a
  rate-limit/metering identity.
- **What's compressed by default:** the text of every `user`-role message
  (OpenAI/Anthropic) or `user`-role `contents` entry (Gemini) — covers both
  "the latest message" and any large context/documents pasted into it.
  `system`/`system_instruction` and prior `assistant`/`model` turns are left
  untouched. Function/tool schemas (`tools`, `tool_calls`, `tool_result`
  blocks) are never touched.
- **Fail-safe:** if compression or governance throws for any reason, Distil
  forwards your original, uncompressed request rather than breaking the call.
- **Streaming:** `"stream": true` is compressed once up front, then the
  provider's SSE response is relayed back chunk-by-chunk, unbuffered
  (verified locally against a slow test source — chunks arrive on the
  provider's own cadence, not batched).
- **Governance modes** via `x-distil-govern`: `off` (default `log`) never
  blocks; `log` runs classify/PII/injection/moderation checks and records
  violations but still forwards the request; `enforce` returns a
  provider-shaped 4xx error instead of forwarding when the verdict is `block`.

### Config headers (all optional)

| Header | Default | Effect |
|---|---|---|
| `x-distil-ratio` | `0.5` | Target fraction of tokens to *keep* (0.05–1.0) |
| `x-distil-govern` | `log` | `off` / `log` / `enforce` |
| `x-distil-compress` | `on` | `on` / `off` — governance still runs independently of this |
| `x-distil-compress-system` | `off` | also compress `system`/`systemInstruction` text |

### Honesty notes

- Compression is **heuristic only** in the gateway (no per-request LLM call
  to compress — that would double your latency and cost). It can read
  slightly choppy; tune `x-distil-ratio` up (e.g. `0.7`) if answer quality
  degrades on your prompts, and test before relying on it in production.
- **Verified against the live provider APIs**, not guessed: OpenAI and
  Anthropic request/response/error/SSE shapes were confirmed by sending real
  requests to `api.openai.com` and `api.anthropic.com` (with an invalid key,
  to observe the real error envelope) and inspecting the response byte-for-
  byte. Gemini's `generateContent` request/response/error shape was verified
  the same way; its streaming framing (`:streamGenerateContent?alt=sse`) is
  the SSE mode documented in Google's REST examples, but was **not** verified
  live against a valid Gemini key — test this path before depending on it.
- The `usage`/token-count fields inside the provider's own response body are
  the provider's real, authoritative numbers (Distil doesn't touch them).
  The `x-distil-*` headers are Distil's own count of what it compressed.

## Quick start (local)

```bash
pip install -r requirements.txt
python demo.py                    # try the core on a sample
python eval/run_eval.py           # measured eval over sample prompts
pytest tests/                     # test suite
python mcp_server.py              # run the MCP server over stdio
uvicorn api.index:app --port 8000 # run the HTTP server + dashboard
# → open http://localhost:8000/  (dashboard) and /mcp (connector)
```

## MCP tools

| Tool | Purpose |
|---|---|
| `compress_prompt(text, target_ratio?, quality?, target_model?, use_cache?)` | Compress + full metrics. `target_model="auto"` routes by complexity |
| `route_prompt(text)` | Recommend a small/large model by complexity + cost transparency |
| `analyze_prompt(text)` | Tokens, fillers, redundancy (no compression) |
| `estimate_savings(text, calls_per_day?, target_model?)` | Projected monthly cost/carbon savings |
| `get_metrics()` | Aggregate Distil metrics incl. cache hit rate |
| `get_top_prompts(n?)` | Most compressible prompts seen |
| `detect_anomalies()` | AIOps: flag low-compression / token / cost spikes (IQR baseline) |

Resource: `metrics://summary`.

Each `compress_prompt` result also carries **distributed-trace spans** (§2.2) —
measured sub-step timings (`route`, `cache_lookup`, `compress`, `token_metrics`,
`estimates`).

### Semantic caching (§8.2) & multi-model routing (§8.4)

- **Cache** — two-tier, serverless-friendly: exact (normalized hash) + similarity
  (lexical-cosine, `DISTIL_CACHE_THRESHOLD`, default 0.92) so near-identical prompts
  reuse a prior compression. Namespaced by (ratio, quality, model). Per warm
  instance. Hit rate is shown on the dashboard.
- **Routing** — `route_prompt` / `target_model="auto"` scores prompt complexity
  (reasoning verbs, code, structure, length) and picks a small vs large model,
  with per-model cost estimates so the choice is transparent.

## Deploy (serverless, Vercel)

1. Push to GitHub, import into Vercel (Python / Fluid Compute — auto-detected).
2. Set env vars: `CONNECTOR_API_KEY` (gates `/mcp`), optional `OPENAI_API_KEY`
   (quality mode), optional `UPSTASH_REDIS_REST_URL` + `_TOKEN` (persistent
   metrics; a local JSON file is used otherwise).
3. Add to Claude via connector settings → `https://<app>.vercel.app/mcp`.

Metrics dashboard: `https://<app>.vercel.app/`.

## Metrics reference

| Measured (real) | Estimated (labeled) |
|---|---|
| tokens in/out/saved, reduction % | cost saved (USD) |
| latency (ms) | energy saved (Wh) |
| CPU time, peak RAM | carbon saved (g CO₂) |
| fillers removed, redundancy % | GPU-ms load + reduction % (`2×params×tokens`) |

## Layout

```
core/                 compression + intelligence + estimates + metrics store
core/gateway.py        LLM Gateway request rewriting (no networking; pure logic)
mcp_server.py          FastMCP tools/resource
api/index.py            serverless ASGI entrypoint (MCP + dashboard + /metrics + auth)
api/gateway_routes.py   LLM Gateway HTTP routes (/v1/chat/completions, /v1/messages, /v1beta/...)
dashboard/             static metrics page
eval/                  measured evaluation
tests/                 unit tests (tests/test_gateway.py covers the gateway)
```

## Reused from the Prompt Compression Agent
tiktoken counting, the filler list + analysis logic, the metrics dataclass
pattern, and the OpenAI wiring (for the optional LLM path).
