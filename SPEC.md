# TraceFlow Compress — Serverless Prompt-Compression MCP Connector

A serverless MCP connector that compresses prompts fast and returns
**TraceFlow-style metrics** (tokens, cost, latency, compute-load, energy, carbon,
plus prompt intelligence). Every number is either **measured** or a
**clearly-labeled estimate** using a named method — nothing faked.

Implements the buildable slice of the TraceFlow whitepaper: the *Prompt
Intelligence* layer plus token / cost / compute / energy / carbon metrics.

---

## Compression core (hybrid)

- **Default — heuristic (`quality=false`):** pure Python, no ML model, instant.
  1. Filler / politeness removal
  2. Redundancy / duplicate collapse
  3. Statistical token-ranking → drop lowest-information tokens to hit target ratio
- **Optional — LLM (`quality=true`):** one `gpt-4o-mini` call. ~$0.001, ~1s.

## Metrics per compression

| Field | Method | Real / Est. |
|---|---|---|
| `original_tokens`, `compressed_tokens`, `tokens_saved`, `reduction_pct` | tiktoken | **Real** |
| `latency_ms` | wall clock | **Real** |
| `cpu_ms`, `peak_ram_mb` | psutil on the compression step | **Real** |
| `est_cost_saved_usd` | tokens_saved × model price | Estimated |
| `est_energy_saved_wh` | tokens_saved × per-token energy | Estimated |
| `est_carbon_saved_g` | energy × carbon intensity | Estimated |
| `est_gpu_ms_per_call`, `est_gpu_ms_saved`, `compute_reduction_pct` | `2 × params × tokens` ÷ GPU throughput | Estimated |
| `fillers_removed`, `redundancy_pct` | heuristic analysis | **Real** |

### Compute-Load module (replaces TraceFlow's GPU metrics)

TraceFlow measured GPU utilization to (1) gauge inference compute load,
(2) measure efficiency, (3) detect saturation, and (4) prove *"compression
reduces GPU load."* We can't measure OpenAI's GPUs, so we preserve the
**function**, not fake the measurement:

1. **Estimated compute load** via the paper's own model (Compute ∝ T×P×L):
   inference FLOPs ≈ `2 × model_params × tokens`, converted to estimated
   GPU-milliseconds against a reference GPU. Yields `est_gpu_ms` and
   `compute_reduction_pct` — reproducing the headline insight as a labeled
   estimate.
2. **Real resource telemetry** we *can* measure: `cpu_ms` + `peak_ram_mb`
   (psutil) of the compression step.
3. **Optional real GPU mode** (Phase 4): run a small local model on a free
   GPU (HF ZeroGPU / Colab T4) and read real utilization/VRAM via `pynvml`.

> Closed-model parameter counts (e.g. gpt-4o-mini) are **not public**, so the
> compute estimate uses **assumed** params, flagged `params_known: false` in
> the output. Open models (Llama-3-8B, etc.) use real param counts.

## MCP tools (Phase 2)

| Tool | Input | Output |
|---|---|---|
| `compress_prompt` | text, target_ratio?, quality?, target_model? | compressed text + full metrics |
| `analyze_prompt` | text | tokens, fillers, redundancy, est. cost (no compression) |
| `estimate_savings` | text, calls_per_day?, target_model? | projected monthly cost + carbon saved |
| `get_metrics` | timeframe? | aggregate TraceFlow dashboard (+ cache stats) |
| `get_top_prompts` | n? | most expensive / compressible prompts seen |
| `route_prompt` | text | recommend small vs large model by complexity |

**Semantic caching (§8.2):** exact + lexical-cosine similarity cache, namespaced
by (ratio, quality, model), per warm instance. **Multi-model routing (§8.4):**
complexity score → small/large model, exposed via `route_prompt` and
`target_model="auto"`.

## Architecture

```
Claude (MCP client) → [MCP over streamable HTTP] → Vercel Functions (Python)
   → MCP server (FastMCP) → Compression core → Metrics/Estimation
   → Upstash Redis (aggregate metrics)  → (optional) static dashboard
```

## Reused from the Prompt Compression Agent
tiktoken counting, the `FILLERS` list + analyze logic, the metrics dataclass
shape, and the OpenAI wiring (for `quality=true`). **Not** reused: LLMLingua-2,
FastAPI app, WebSocket, old frontend.

## Honesty guardrails
- Every estimated field carries `"estimated": true` and its method in
  `estimates_meta`.
- No faked GPU measurement; assumed model params are flagged.
- Compression quality validated by a real measured eval, not claimed numbers.

## Build phases
1. **Core library** — heuristic + LLM compression, metrics, estimation. ← *this phase*
2. **MCP server** — FastMCP tools/resources; test locally in Claude.
3. **Serverless deploy** — Vercel HTTP transport + Upstash metrics + auth + CI security.
4. **Polish** — optional dashboard, promptfoo eval, optional real-GPU mode.
