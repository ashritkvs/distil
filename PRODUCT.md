# TraceFlow Compress — Product Documentation

**An LLM governance + prompt-compression gateway.** It screens, optimizes, and
measures prompts before they reach an LLM — delivered as an MCP connector (for
AI agents) and a web tool (for humans).

- **Live app / demo:** https://traceflow-compress.vercel.app
- **MCP connector:** https://traceflow-compress.vercel.app/mcp
- **Source:** https://github.com/ashritkvs/traceflow-compress

---

## 1. The problem it solves

Teams sending prompts to LLMs face three costs at once:
1. **Money & carbon** — verbose prompts waste tokens on every call.
2. **Safety & compliance** — prompts may leak PII/secrets, contain injection
   attacks, request unsafe content, or pull disallowed code packages.
3. **Blindness** — no visibility into what's being spent or sent.

TraceFlow Compress addresses all three in one gateway.

## 2. What happens to a prompt

```
Prompt
  │
  ▼  GOVERN   classify → security scan → moderation → package policy → code scan
  │           → verdict: ALLOW / WARN / BLOCK   (blocked prompts stop here)
  ▼  COMPRESS heuristic (free) · adaptive/LLM (quality) · safe (verified)
  ▼  VERIFY   meaning score 0–100 (optional)
  ▼  MEASURE  tokens · cost · carbon · GPU-load · latency  (+ spans)
  ▼  METER    per-tenant usage vs plan quota
  ▼
Result: {verdict, type, compressed_text, metrics, governance}
```

One tool call — `process_prompt` (MCP) or `POST /process` (REST) — runs the
whole thing.

## 3. Feature inventory

**Compression** — heuristic (free/instant), LLM quality mode, adaptive
(escalates to LLM only when meaning drops), safe mode (never returns a
meaning-breaking result).

**Governance** — prompt classification (enquiry/code/testing/draft/ppt/
refinement), content security (PII/secrets/prompt-injection), moderation
(violence/hate/self-harm), package allow/deny policy, code-vuln scanning,
violation logging.

**Verification** — LLM meaning score with pass/fail; safe-mode fallback.

**Observability** — measured tokens/cost/latency/throughput; estimated
energy/carbon/GPU-load (clearly labeled); distributed-trace spans; anomaly
detection; savings forecast; executive + engineering dashboards.

**Intelligence** — semantic cache (avoid re-runs), multi-model routing
(complexity → small/large model).

**SaaS layer** — multi-tenant API keys, per-tenant rate limiting, plan tiers
(free/pro/enterprise), usage metering, monthly quota enforcement.

## 4. MCP tools (20)

`process_prompt` · `compress_prompt` · `compress_adaptive` · `verify_meaning` ·
`govern_prompt` · `classify_prompt` · `scan_code` · `check_policy` ·
`get_violations` · `route_prompt` · `analyze_prompt` · `estimate_savings` ·
`get_metrics` · `get_top_prompts` · `detect_anomalies` · `forecast_savings` ·
`get_budget` · `get_usage` · `list_plans` · `list_providers`
(+ `metrics://summary` resource)

## 5. HTTP API

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/` , `/engineering` | GET | public | Dashboards (interactive box + charts) |
| `/process` | POST | anon = heuristic; key = LLM | Unified govern + compress |
| `/compress` | POST | anon = heuristic; key = LLM | Compression only |
| `/metrics`, `/records`, `/violations`, `/usage` | GET | public | Observability JSON |
| `/manifest` | GET | public | Connector metadata |
| `/mcp` | POST | key required | MCP connector |

## 6. Access model (credit-safe)

- **Anonymous** → free heuristic compression + full governance. No LLM, no
  credit spend. (This is what powers the public demo box.)
- **With `CONNECTOR_API_KEY`** → unlocks LLM features (quality/adaptive/safe/
  moderation) and the `/mcp` connector.
- Rate-limited and metered per identity; monthly plan quotas enforced.

## 7. Tech & quality

Python · FastMCP (streamable HTTP) · Starlette · tiktoken · Vercel serverless.
**20 tools · 34 tests passing · Semgrep 0 findings.**
Benchmark (32-prompt set): **~41% compression**, **100% governance detection /
0% false-positives on that set**.

## 8. Honest limits (do not overstate)

- Heuristic compression is rule-based and choppier than an ML compressor
  (Headroom-class); the LLM/adaptive tier is the quality path.
- The benchmark is a small, self-made set — cite as "on our N-prompt test set,"
  not "accurate."
- Safety detectors are heuristic (regex/keyword + optional moderation API) —
  effective on common cases, **not an audited enterprise guarantee**.
- Cost/energy/carbon/GPU numbers are **estimates**, clearly labeled; closed-
  model params are assumed.
- Metrics persistence needs Upstash (currently ephemeral on serverless);
  billing is metering only (no payment processor wired).
