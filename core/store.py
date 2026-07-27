"""Aggregate metrics store with two backends.

  * Upstash Redis (serverless) when UPSTASH_REDIS_REST_URL + _TOKEN are set.
  * Local JSON file otherwise (dev / stdio use).

Stores a capped list of records (successful compressions + error events) and
derives Distil-style aggregate metrics: totals, averages, throughput
(tokens/sec), and reliability (error rate).
"""

from __future__ import annotations

import json
import os
import statistics
import time
from threading import Lock

_CAP = 500
_LIST_KEY = "distil:records"


def entry_from_result(result: dict) -> dict:
    """Slim success record kept for aggregation."""
    return {
        "ts": time.time(),
        "kind": "ok",
        "preview": (result.get("original_text") or "")[:60],
        "original_tokens": result.get("original_tokens", 0),
        "compressed_tokens": result.get("compressed_tokens", 0),
        "tokens_saved": result.get("tokens_saved", 0),
        "reduction_pct": result.get("reduction_pct", 0.0),
        "latency_ms": result.get("latency_ms", 0.0),
        "est_cost_saved_usd": result.get("est_cost_saved_usd", 0.0),
        "est_carbon_saved_g": result.get("est_carbon_saved_g", 0.0),
        "est_gpu_ms_saved": result.get("est_gpu_ms_saved", 0.0),
        "mode": result.get("mode", "heuristic"),
    }


def error_entry(error_type: str, preview: str = "") -> dict:
    return {"ts": time.time(), "kind": "error",
            "error_type": error_type, "preview": preview[:60]}


def violation_entry(verdict: str, reasons: list[str], preview: str = "") -> dict:
    return {"ts": time.time(), "kind": "violation", "verdict": verdict,
            "reasons": reasons, "preview": preview[:60]}


def _summarize(records: list[dict]) -> dict:
    ok = [r for r in records if r.get("kind") == "ok"]
    errors = [r for r in records if r.get("kind") == "error"]
    total = len(ok) + len(errors)
    if not ok:
        base = {
            "total_requests": 0, "total_tokens_saved": 0,
            "total_cost_saved_usd": 0.0, "total_carbon_saved_g": 0.0,
            "total_gpu_ms_saved": 0.0, "avg_reduction_pct": 0.0,
            "avg_latency_ms": 0.0, "throughput_tokens_per_sec": 0.0,
        }
    else:
        total_latency_s = sum(r.get("latency_ms", 0.0) for r in ok) / 1000
        tokens_processed = sum(r.get("original_tokens", 0) for r in ok)
        base = {
            "total_requests": len(ok),
            "total_tokens_saved": sum(r["tokens_saved"] for r in ok),
            "total_cost_saved_usd": round(sum(r["est_cost_saved_usd"] for r in ok), 6),
            "total_carbon_saved_g": round(sum(r["est_carbon_saved_g"] for r in ok), 4),
            "total_gpu_ms_saved": round(sum(r["est_gpu_ms_saved"] for r in ok), 2),
            "avg_reduction_pct": round(
                statistics.mean(r["reduction_pct"] for r in ok), 1),
            "avg_latency_ms": round(
                statistics.mean(r.get("latency_ms", 0.0) for r in ok), 2),
            "throughput_tokens_per_sec": round(
                tokens_processed / total_latency_s, 1) if total_latency_s > 0 else 0.0,
        }
    violations = [r for r in records if r.get("kind") == "violation"]
    base["errors"] = len(errors)
    base["error_rate_pct"] = round(len(errors) / total * 100, 1) if total else 0.0
    base["violations"] = len(violations)
    return base


class _LocalBackend:
    def __init__(self, path: str):
        self._path = path
        self._lock = Lock()

    def _load(self) -> list[dict]:
        try:
            with open(self._path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def push(self, entry: dict):
        with self._lock:
            records = self._load()
            records.append(entry)
            records = records[-_CAP:]
            with open(self._path, "w") as f:
                json.dump(records, f)

    def all(self) -> list[dict]:
        with self._lock:
            return list(reversed(self._load()))  # newest-first


class _UpstashBackend:
    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/")
        self._token = token

    def _cmd(self, *args) -> dict:
        import httpx

        r = httpx.post(self._url, headers={"Authorization": f"Bearer {self._token}"},
                       json=list(args), timeout=10.0)
        r.raise_for_status()
        return r.json()

    def push(self, entry: dict):
        self._cmd("LPUSH", _LIST_KEY, json.dumps(entry))
        self._cmd("LTRIM", _LIST_KEY, 0, _CAP - 1)

    def all(self) -> list[dict]:
        res = self._cmd("LRANGE", _LIST_KEY, 0, _CAP - 1).get("result", [])
        return [json.loads(x) for x in res]  # newest-first (LPUSH order)


class MetricsStore:
    def __init__(self):
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        if url and token:
            self._backend = _UpstashBackend(url, token)
            self.backend_name = "upstash"
        else:
            path = os.getenv("DISTIL_METRICS_PATH", ".distil_metrics.json")
            self._backend = _LocalBackend(path)
            self.backend_name = "local-json"

    def record(self, result: dict):
        try:
            self._backend.push(entry_from_result(result))
        except Exception:
            pass

    def record_error(self, error_type: str, preview: str = ""):
        try:
            self._backend.push(error_entry(error_type, preview))
        except Exception:
            pass

    def record_violation(self, verdict: str, reasons: list[str], preview: str = ""):
        try:
            self._backend.push(violation_entry(verdict, reasons, preview))
        except Exception:
            pass

    def violations(self, n: int = 50) -> list[dict]:
        try:
            return [r for r in self._backend.all()
                    if r.get("kind") == "violation"][:n]
        except Exception:
            return []

    def summary(self) -> dict:
        try:
            s = _summarize(self._backend.all())
        except Exception:
            s = _summarize([])
        s["backend"] = self.backend_name
        return s

    def top_prompts(self, n: int = 5) -> list[dict]:
        try:
            ok = [r for r in self._backend.all() if r.get("kind") == "ok"]
        except Exception:
            return []
        return sorted(ok, key=lambda r: r["tokens_saved"], reverse=True)[:n]

    def all_records(self) -> list[dict]:
        try:
            return [r for r in self._backend.all() if r.get("kind") == "ok"]
        except Exception:
            return []


store = MetricsStore()
