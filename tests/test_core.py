"""Phase 1 tests for the compression core (all offline — no API key needed)."""

from __future__ import annotations

import os

import pytest

from core import analyze, compress, count_tokens
from core.compressor import heuristic_compress
from core import estimates

VERBOSE = (
    "Could you please possibly help me understand in a very detailed and "
    "comprehensive way what machine learning actually is, including all the "
    "different types and subtypes if at all possible?"
)


def test_token_counting():
    assert count_tokens("hello world") == 2


def test_analyze_detects_fillers():
    out = analyze("Could you please possibly help me if possible")
    joined = " ".join(out["fillers"])
    assert "please" in joined
    assert "possibly" in joined
    assert out["tokens"] > 0


def test_heuristic_reduces_tokens():
    compressed = heuristic_compress(VERBOSE, 0.5)
    assert count_tokens(compressed) < count_tokens(VERBOSE)
    assert len(compressed.strip()) > 0


def test_compress_result_shape_and_consistency():
    r = compress(VERBOSE, target_ratio=0.5)
    assert r.mode == "heuristic"
    assert r.compressed_tokens < r.original_tokens
    # token accounting is internally consistent
    assert r.original_tokens - r.compressed_tokens == r.tokens_saved
    assert r.reduction_pct > 0
    # estimates present and labeled
    assert r.estimates_meta["estimated"] is True
    assert r.est_cost_saved_usd >= 0
    assert r.est_energy_saved_wh >= 0
    assert r.est_gpu_ms_saved >= 0


def test_compute_estimate_flags_closed_model_params():
    meta = estimates.estimates_meta("gpt-4o-mini")
    assert meta["params_known"] is False  # closed model → assumed params
    open_meta = estimates.estimates_meta("llama-3-8b")
    assert open_meta["params_known"] is True


def test_compute_reduction_tracks_token_reduction():
    r = compress(VERBOSE, target_ratio=0.5)
    # fewer tokens => positive estimated GPU-time savings
    assert r.est_gpu_ms_saved > 0
    assert r.compute_reduction_pct > 0


def test_concise_prompt_stays_valid():
    r = compress("Explain the difference between HTTP and HTTPS.", 0.5)
    assert r.compressed_text.strip()
    assert r.reduction_pct >= 0


def test_measured_metrics_present():
    r = compress(VERBOSE, target_ratio=0.5)
    assert r.latency_ms >= 0
    assert r.cpu_ms >= 0
    assert r.peak_ram_mb > 0  # psutil should report real RSS


def test_exact_cache_hit():
    from core.cache import cache
    cache.clear()
    r1 = compress(VERBOSE, target_ratio=0.5)
    assert r1.cache_hit is False
    r2 = compress(VERBOSE, target_ratio=0.5)
    assert r2.cache_hit is True
    assert r2.cache_type == "exact"
    assert r2.compressed_text == r1.compressed_text


def test_semantic_cache_hit_on_near_duplicate():
    from core.cache import cache
    cache.clear()
    compress("Please explain how neural networks learn using backpropagation.", 0.5)
    # near-identical wording -> lexical-cosine should exceed threshold
    r = compress("Explain how neural networks learn using backpropagation please.", 0.5)
    assert r.cache_hit is True
    assert r.cache_type == "semantic"
    assert r.cache_similarity >= cache.threshold


def test_cache_namespaced_by_ratio():
    from core.cache import cache
    cache.clear()
    compress(VERBOSE, target_ratio=0.5)
    r = compress(VERBOSE, target_ratio=0.3)  # different ratio -> not a hit
    assert r.cache_hit is False


def test_routing_simple_vs_complex():
    from core.routing import route
    simple = route("Define recursion.")
    complex_ = route(
        "Design and analyze a distributed caching architecture, compare "
        "trade-offs step by step, and explain how to optimize each component "
        "for throughput. def cache(): return None"
    )
    assert simple["tier"] == "small"
    assert complex_["tier"] == "large"
    assert complex_["complexity_score"] > simple["complexity_score"]


def test_compress_auto_routing():
    r = compress("Define recursion.", target_model="auto", use_cache=False)
    assert r.routed is not None
    assert r.target_model == r.routed["recommended_model"]


def test_trace_spans_recorded():
    r = compress(VERBOSE, target_ratio=0.5, use_cache=False)
    names = [s["name"] for s in r.spans]
    assert "compress" in names
    assert "token_metrics" in names
    assert "estimates" in names
    assert all(s["ms"] >= 0 for s in r.spans)


def test_aiops_insufficient_then_detects():
    from core.aiops import detect_anomalies
    # too little data
    assert detect_anomalies([], min_samples=8)["status"] == "insufficient_data"
    # a clean baseline plus one clear low-compression outlier
    records = [{"reduction_pct": 50.0, "original_tokens": 40,
                "est_cost_saved_usd": 0.0, "preview": "normal"} for _ in range(12)]
    records.insert(0, {"reduction_pct": 2.0, "original_tokens": 40,
                       "est_cost_saved_usd": 0.0, "preview": "bad one"})
    out = detect_anomalies(records, min_samples=8)
    assert out["status"] == "ok"
    assert any(a["type"] == "low_compression" for a in out["anomalies"])


def test_store_throughput_and_reliability(tmp_path):
    from core.store import MetricsStore
    os.environ["TF_METRICS_PATH"] = str(tmp_path / "m.json")
    s = MetricsStore()
    for _ in range(3):
        s.record({"original_text": "x", "original_tokens": 40, "compressed_tokens": 20,
                  "tokens_saved": 20, "reduction_pct": 50.0, "latency_ms": 4.0,
                  "est_cost_saved_usd": 0.0, "est_carbon_saved_g": 0.0,
                  "est_gpu_ms_saved": 2.0, "mode": "heuristic"})
    s.record_error("ValueError", "bad prompt")
    summ = s.summary()
    assert summ["total_requests"] == 3
    assert summ["errors"] == 1
    assert summ["error_rate_pct"] == 25.0
    assert summ["throughput_tokens_per_sec"] > 0
    assert summ["avg_latency_ms"] == 4.0
    del os.environ["TF_METRICS_PATH"]


def test_forecast_insufficient_then_projects():
    from core.forecast import forecast
    assert forecast([], min_samples=8)["status"] == "insufficient_data"
    import time
    now = time.time()
    recs = [{"kind": "ok", "ts": now - i * 3600, "tokens_saved": 20,
             "est_cost_saved_usd": 0.0001, "est_carbon_saved_g": 0.01}
            for i in range(12)]
    out = forecast(recs, horizon_days=30)
    assert out["status"] == "ok"
    assert out["projected_30d"]["tokens_saved"] > 0
    assert out["estimated"] is True


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_llm_mode_runs():
    r = compress(VERBOSE, target_ratio=0.5, quality=True, use_cache=False)
    assert r.mode == "llm"
    assert r.compressed_text.strip()
