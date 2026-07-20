# TraceFlow Compress

A **serverless prompt-compression MCP connector** that compresses prompts fast
and returns **TraceFlow-style metrics** — tokens, cost, latency, compute-load,
energy, and carbon — where every number is either **measured** or a
**clearly-labeled estimate**. See [SPEC.md](SPEC.md) for the full design.

Built around the TraceFlow whitepaper's *Prompt Intelligence* + token/cost/
compute/energy/carbon layer (the buildable slice — no GPU hardware required).

## Highlights

- **Fast + serverless:** default heuristic compression is pure Python (~3 ms,
  no model, no API key). Optional `gpt-4o-mini` mode for higher quality.
- **MCP connector:** exposes 5 tools + a metrics resource over streamable HTTP.
- **TraceFlow metrics:** token/cost/latency (measured) + energy/carbon/GPU-load
  (estimated, labeled). GPU intent preserved via a compute-load model, not faked.
- **Live dashboard** + public `/metrics` endpoint.
- **Honest by design:** every estimate flagged `estimated: true`; closed-model
  params flagged `params_known: false`.

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
| `get_metrics()` | Aggregate TraceFlow metrics incl. cache hit rate |
| `get_top_prompts(n?)` | Most compressible prompts seen |
| `detect_anomalies()` | AIOps: flag low-compression / token / cost spikes (IQR baseline) |

Resource: `metrics://summary`.

Each `compress_prompt` result also carries **distributed-trace spans** (§2.2) —
measured sub-step timings (`route`, `cache_lookup`, `compress`, `token_metrics`,
`estimates`).

### Semantic caching (§8.2) & multi-model routing (§8.4)

- **Cache** — two-tier, serverless-friendly: exact (normalized hash) + similarity
  (lexical-cosine, `TF_CACHE_THRESHOLD`, default 0.92) so near-identical prompts
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
core/          compression + intelligence + estimates + metrics store
mcp_server.py  FastMCP tools/resource
api/index.py   serverless ASGI entrypoint (MCP + dashboard + /metrics + auth)
dashboard/     static metrics page
eval/          measured evaluation
tests/         unit tests
```

## Reused from the Prompt Compression Agent
tiktoken counting, the filler list + analysis logic, the metrics dataclass
pattern, and the OpenAI wiring (for the optional LLM path).
