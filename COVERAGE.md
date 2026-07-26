# Coverage — product vs. the specs

Maps the shipped product against (A) the "API-based Token Utilization
Observability & Orchestration" spec and (B) the TraceFlow whitepaper.
Live: https://traceflow-compress.vercel.app

Legend: ✅ built · 🟡 built with a caveat · ❌ not code / not built

---

## A) API-based solution spec

### Functional requirements

| # | Requirement | Status | How it's delivered | Caveat |
|---|---|---|---|---|
| 1 | Total tokens used / available | ✅ | `get_budget` / `/metrics.budget` — used, available, utilization, saved-by-compression | "Available" is vs a **configured** budget (`TF_TOKEN_BUDGET`), not live provider quota |
| 2 | Prompt types (enquiry/code/testing/draft/ppt/refinement) | ✅ | `classify_prompt` | — |
| 3 | Store used prompts / avoid re-run | ✅ | Semantic cache (exact + similarity) | Caches compressions; per warm instance until Upstash |
| 4 | Prompt optimization for tokens | ✅ | Compression: heuristic / LLM / adaptive / safe | Heuristic < ML quality |
| 5 | Security checks for content | ✅ | `scan_content` — PII, secrets, prompt-injection | Heuristic; not an audited guarantee |
| 6 | Non-vulnerable code | 🟡 | `scan_code` — flags eval/shell/pickle/SQLi/secrets/weak-hash | **Flags** risky code; doesn't generate or guarantee-safe |
| 7 | Non-violent content | ✅ | `moderate` — OpenAI moderation API or keyword heuristic | Heuristic without a key |
| 8 | Alert mechanism for violations | 🟡 | Verdict logging + `get_violations` + `/violations` | No external delivery (webhook/Slack) yet |
| 9 | LLM integration feasibility | 🟡 | `list_providers` + MCP connector (any agent) | Only OpenAI is actually **called** today |

### Design & architecture aspects

| Aspect | Status | Delivered |
|---|---|---|
| Product design & architecture | ✅ | `SPEC.md`, `PRODUCT.md` |
| Infrastructure requirements | 🟡 | Vercel serverless (live). Persistence ephemeral until Upstash |
| Convert to connector | ✅ | Live MCP connector at `/mcp` (20 tools) |
| Marketplace requirements | 🟡 | `/manifest` + `MARKETPLACE.md` checklist (not yet submitted) |
| Package allow/deny policy (code agents) | ✅ | `check_policy` + governance enforcement (blocks denied deps) |

**Net: every functional item and design aspect is addressed** — 6 fully, the
rest with honest caveats (external alert delivery, live provider quota,
multi-provider calls, persistence, marketplace submission).

---

## B) TraceFlow whitepaper (~70% of substance)

| Area | Status |
|---|---|
| Token-centric premise, token metrics, cost/energy/carbon math | ✅ |
| Prompt intelligence (redundancy, compression, top-prompts) | ✅ |
| Distributed-trace spans (compression pipeline) | ✅ |
| Correlation (tokens → cost/energy/carbon/GPU-load) | 🟡 |
| AIOps detection + optimization (trim/route/cache) | ✅ |
| Dashboards (executive + engineering + prompt intelligence) | ✅ |
| Multi-model routing, semantic caching, forecasting | ✅ |
| Real GPU/VRAM/KV telemetry | ❌ needs GPU hardware |
| Passive live-traffic interception | ❌ needs a serving stack |
| Token-aware autoscaling of infra | ❌ needs real infra |

The unbuilt ~30% is the live-inference-observability + physical-infrastructure
platform — blocked by hardware/traffic, not effort. GPU intent is preserved via
an **estimated** compute-load model + real CPU/RAM, clearly labeled.

---

## Honest limits (carry into any pitch)
Heuristic compression is choppier than an ML compressor; the benchmark is a
small self-made set; safety detectors are heuristic (not audited); cost/carbon/
GPU figures are labeled estimates; persistence needs Upstash; billing is
metering only (no payment processor).
