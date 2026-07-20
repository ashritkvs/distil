"""Semantic compression cache (TraceFlow §8.2).

Two-tier, serverless-friendly, in-process cache:
  * Exact match — normalized-hash lookup (free, instant).
  * Similarity  — cosine over vectors:
      - default "lexical" mode: token-frequency cosine (free, no API).
      - "embedding" mode (TF_CACHE_MODE=embedding + OPENAI_API_KEY): true
        semantic matching via OpenAI embeddings.

Entries are namespaced by (target_ratio, quality, model). The cache is
per-process (warm serverless instance), not a cross-instance vector store.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9]+")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _vec_lexical(text: str) -> Counter:
    return Counter(_WORD.findall(text.lower()))


def _vec_embedding(text: str) -> list[float]:
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("TF_EMBED_MODEL", "text-embedding-3-small")
    resp = client.embeddings.create(model=model, input=text[:8000])
    return resp.data[0].embedding


def _cosine(a, b) -> float:
    if isinstance(a, Counter):
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
    else:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class CompressionCache:
    def __init__(self, max_entries: int = 256, threshold: float = 0.92,
                 mode: str | None = None):
        self.max_entries = max_entries
        self.threshold = threshold
        want = (mode or os.getenv("TF_CACHE_MODE", "lexical")).lower()
        self.mode = "embedding" if (want == "embedding" and
                                    os.getenv("OPENAI_API_KEY")) else "lexical"
        self.method = ("embedding:" + os.getenv("TF_EMBED_MODEL",
                       "text-embedding-3-small")) if self.mode == "embedding" \
            else "lexical-cosine"
        self._exact: dict[str, int] = {}
        self._entries: list[dict] = []
        self._vec_memo: dict[str, object] = {}
        self.hits_exact = 0
        self.hits_semantic = 0
        self.misses = 0

    def _vectorize(self, text: str):
        key = _norm(text)
        if key in self._vec_memo:
            return self._vec_memo[key]
        try:
            vec = _vec_embedding(text) if self.mode == "embedding" \
                else _vec_lexical(text)
        except Exception:
            vec = _vec_lexical(text)  # fall back if embeddings fail
        if len(self._vec_memo) > 512:
            self._vec_memo.clear()
        self._vec_memo[key] = vec
        return vec

    @staticmethod
    def _ns(ratio: float, quality: bool, model: str) -> str:
        return f"{round(ratio, 3)}|{int(quality)}|{model}"

    def _key(self, text: str, ns: str) -> str:
        return hashlib.sha256((ns + "::" + _norm(text)).encode()).hexdigest()

    def get(self, text: str, ratio: float, quality: bool, model: str):
        """Return (result_dict, hit_type, similarity) or (None, None, 0.0)."""
        ns = self._ns(ratio, quality, model)
        k = self._key(text, ns)
        if k in self._exact:
            self.hits_exact += 1
            return dict(self._entries[self._exact[k]]["result"]), "exact", 1.0

        vec = self._vectorize(text)
        best, best_sim = None, 0.0
        for e in self._entries:
            if e["ns"] != ns:
                continue
            s = _cosine(vec, e["vec"])
            if s > best_sim:
                best, best_sim = e, s
        if best is not None and best_sim >= self.threshold:
            self.hits_semantic += 1
            return dict(best["result"]), "semantic", round(best_sim, 3)

        self.misses += 1
        return None, None, 0.0

    def put(self, text: str, ratio: float, quality: bool, model: str, result: dict):
        ns = self._ns(ratio, quality, model)
        k = self._key(text, ns)
        if k in self._exact:
            return
        self._entries.append(
            {"key": k, "ns": ns, "vec": self._vectorize(text), "result": result}
        )
        if len(self._entries) > self.max_entries:
            self._entries.pop(0)
        self._exact = {e["key"]: i for i, e in enumerate(self._entries)}

    def stats(self) -> dict:
        total = self.hits_exact + self.hits_semantic + self.misses
        hits = self.hits_exact + self.hits_semantic
        return {
            "size": len(self._entries),
            "hits_exact": self.hits_exact,
            "hits_semantic": self.hits_semantic,
            "misses": self.misses,
            "hit_rate_pct": round(hits / total * 100, 1) if total else 0.0,
            "mode": self.mode,
            "method": self.method,
            "threshold": self.threshold,
        }

    def clear(self):
        self.__init__(self.max_entries, self.threshold, self.mode)


cache = CompressionCache(
    max_entries=int(os.getenv("TF_CACHE_MAX", "256")),
    threshold=float(os.getenv("TF_CACHE_THRESHOLD", "0.92")),
)
