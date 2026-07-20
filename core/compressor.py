"""Compression core: heuristic (default) + optional LLM, with full metrics.

The heuristic path is pure Python (serverless-friendly, instant). The optional
LLM path uses gpt-4o-mini for higher quality. Both return the same
`CompressionResult` carrying measured metrics (tokens, latency, cpu/ram) and
clearly-labeled estimates (cost, energy, carbon, compute-load).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass, field

from core import estimates
from core.cache import cache
from core.intelligence import (
    FILLER_PATTERNS,
    FILLERS,
    STOPWORDS,
    VAGUE_WORDS,
    count_tokens,
    find_fillers,
    redundancy_pct,
)

try:  # psutil is optional; metrics degrade gracefully without it.
    import psutil

    _PROC = psutil.Process()
except Exception:  # pragma: no cover
    psutil = None
    _PROC = None

_WORD_RE = re.compile(r"\S+")
# Regex patterns first (verbose constructions), then literal fillers longest-first.
_PATTERN_RES = [re.compile(p, re.IGNORECASE) for p in FILLER_PATTERNS]
_FILLER_RES = [
    re.compile(r"\b" + re.escape(f) + r"\b", re.IGNORECASE)
    for f in sorted(FILLERS, key=len, reverse=True)
]


@dataclass
class CompressionResult:
    original_text: str
    compressed_text: str
    mode: str
    target_model: str
    # --- measured ---
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    reduction_pct: float
    latency_ms: float
    cpu_ms: float
    peak_ram_mb: float
    fillers_removed: list[str]
    redundancy_pct: float
    # --- estimated ---
    est_cost_saved_usd: float
    est_energy_saved_wh: float
    est_carbon_saved_g: float
    est_gpu_ms_per_call: float
    est_gpu_ms_saved: float
    compute_reduction_pct: float
    estimates_meta: dict = field(default_factory=dict)
    # --- caching / routing ---
    cache_hit: bool = False
    cache_type: str | None = None
    cache_similarity: float = 0.0
    routed: dict | None = None
    # --- distributed-trace spans (measured sub-step timings, ms) ---
    spans: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Heuristic compression (pure Python)
# --------------------------------------------------------------------------- #

def _word_score(word: str) -> float:
    """Importance score for a word. Higher = more likely to keep."""
    bare = word.lower().strip(".,!?;:\"'()[]{}")
    if not bare:
        return 0.0
    if bare in VAGUE_WORDS:
        return 0.1  # low-information content word — drop early
    if bare in STOPWORDS:
        return 0.15
    if any(c.isdigit() for c in bare):
        return 1.6  # numbers / quantities are load-bearing
    if word[:1].isupper():
        return 1.5  # likely proper noun / entity
    return 1.0 + min(len(bare), 12) / 24.0  # mild length bonus for content words


def heuristic_compress(text: str, target_ratio: float) -> str:
    """Compress by removing fillers/redundancy then trimming low-value tokens.

    `target_ratio` is the fraction of tokens to RETAIN (0-1), matching the
    LLMLingua convention.
    """
    # 1) Strip verbose regex constructions, then literal filler phrases.
    stripped = text
    for rx in _PATTERN_RES:
        stripped = rx.sub(" ", stripped)
    for rx in _FILLER_RES:
        stripped = rx.sub(" ", stripped)

    # 2) Tokenize, collapse consecutive duplicates, and drop vague words
    #    (they add no meaning even when under the token target).
    words = _WORD_RE.findall(stripped)
    deduped: list[str] = []
    for w in words:
        if deduped and w.lower() == deduped[-1].lower():
            continue
        if w.lower().strip(".,!?;:\"'()[]{}") in VAGUE_WORDS:
            continue
        deduped.append(w)

    if not deduped:
        return _tidy(text)

    # 3) If still over target, drop the lowest-scoring words (preserve order).
    #    Short prompts get a gentler ratio so the core instruction survives.
    orig_tokens = count_tokens(text)
    effective_ratio = max(target_ratio, 0.7) if orig_tokens <= 12 else target_ratio
    target_tokens = max(3, round(orig_tokens * effective_ratio))

    # Protect the leading content word (usually the imperative verb — the core
    # instruction) so compression never strips "Explain"/"Write"/"Design".
    protected: set[int] = set()
    for i, w in enumerate(deduped):
        if not _is_func(w):
            protected.add(i)
            break

    if count_tokens(" ".join(deduped)) > target_tokens:
        scored = sorted(
            (i for i in range(len(deduped)) if i not in protected),
            key=lambda i: _word_score(deduped[i]),
        )
        drop: set[int] = set()
        for idx in scored:
            drop.add(idx)
            kept = " ".join(deduped[i] for i in range(len(deduped)) if i not in drop)
            if count_tokens(kept) <= target_tokens:
                break
        deduped = [deduped[i] for i in range(len(deduped)) if i not in drop]

    return _tidy(" ".join(_cleanup_orphans(deduped)))


def _is_func(word: str) -> bool:
    return word.lower().strip(".,!?;:\"'()[]{}") in STOPWORDS


def _cleanup_orphans(words: list[str]) -> list[str]:
    """Drop function words left stranded next to other function words, and trim
    leading/trailing function words (fixes fragments like 'a and')."""
    keep: list[str] = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if _is_func(w) and ((keep and _is_func(keep[-1])) or _is_func(nxt)):
            continue
        keep.append(w)
    while keep and _is_func(keep[0]):
        keep.pop(0)
    while keep and _is_func(keep[-1]):
        keep.pop()
    return keep or words


def _tidy(text: str) -> str:
    """Collapse whitespace, fix punctuation spacing, capitalize the first word."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    text = re.sub(r"([,;:])(?=\S)", r"\1 ", text)  # ensure space after comma
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


# --------------------------------------------------------------------------- #
# LLM compression (optional, gpt-4o-mini)
# --------------------------------------------------------------------------- #

def llm_compress(text: str, target_ratio: float) -> str:
    """Compress via a single gpt-4o-mini call. Requires OPENAI_API_KEY."""
    from openai import OpenAI  # imported lazily so heuristic path needs no key

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    pct = int(round((1 - target_ratio) * 100))
    system = (
        "You are a prompt compressor. Rewrite the user's prompt using the fewest "
        "words while preserving EVERY instruction, entity, constraint, number, "
        "and specific detail. Keep it grammatical and unambiguous. Remove only "
        f"filler, politeness, and redundancy. Target ~{pct}% fewer tokens. "
        "Output ONLY the compressed prompt — no preamble, quotes, or explanation.\n\n"
        "Example:\n"
        "Input: Could you please possibly help me understand, in a very detailed "
        "way, what a REST API is and how it works?\n"
        "Output: Explain in detail what a REST API is and how it works.\n\n"
        "Example:\n"
        "Input: I would really like you to kindly write a Python function that "
        "takes a list of integers and returns the sum of the even ones.\n"
        "Output: Write a Python function returning the sum of even integers in a list."
    )
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        max_tokens=1024,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def compress(
    text: str,
    target_ratio: float = 0.5,
    quality: bool = False,
    target_model: str | None = None,
    use_cache: bool = True,
) -> CompressionResult:
    """Compress `text` and return full metrics.

    Args:
        text: the prompt to compress.
        target_ratio: fraction of tokens to retain (0-1).
        quality: if True, use the gpt-4o-mini LLM path; else heuristic.
        target_model: model the compressed prompt will be sent to (drives
            cost/energy/carbon/compute estimates). Pass "auto" to route by
            complexity. Defaults to gpt-4o-mini.
        use_cache: consult/populate the semantic cache (default True).
    """
    spans: list[dict] = []

    def _span(name, start):
        spans.append({"name": name, "ms": round((time.perf_counter() - start) * 1000, 3)})

    routed = None
    if (target_model or "").lower() == "auto":
        from core.routing import route

        _s = time.perf_counter()
        routed = route(text)
        target_model = routed["recommended_model"]
        _span("route", _s)

    model_name, _ = estimates.resolve_model(target_model)

    # --- semantic cache lookup ---
    if use_cache:
        _s = time.perf_counter()
        cached, ctype, sim = cache.get(text, target_ratio, quality, model_name)
        _span("cache_lookup", _s)
        if cached is not None:
            r = CompressionResult(**cached)
            r.cache_hit = True
            r.cache_type = ctype
            r.cache_similarity = sim
            r.latency_ms = 0.0
            r.cpu_ms = 0.0
            r.routed = routed
            r.spans = spans
            return r

    # --- measure the work ---
    cpu_before = time.process_time()
    ram_before = _PROC.memory_info().rss if _PROC else 0
    t0 = time.perf_counter()

    if quality:
        compressed = llm_compress(text, target_ratio)
        mode = "llm"
    else:
        compressed = heuristic_compress(text, target_ratio)
        mode = "heuristic"

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    _span("compress", t0)
    cpu_ms = round((time.process_time() - cpu_before) * 1000, 2)
    ram_after = _PROC.memory_info().rss if _PROC else 0
    peak_ram_mb = round(max(ram_before, ram_after) / 1_048_576, 1)

    # --- measured token metrics ---
    _s = time.perf_counter()
    orig_tokens = count_tokens(text)
    comp_tokens = count_tokens(compressed)
    saved = orig_tokens - comp_tokens
    reduction = round(saved / orig_tokens * 100, 1) if orig_tokens else 0.0
    _span("token_metrics", _s)

    # --- estimates (savings from fewer tokens sent downstream) ---
    _s = time.perf_counter()
    cost_saved = estimates.est_cost_usd(max(saved, 0), model_name, "in")
    energy_saved = estimates.est_energy_wh(max(saved, 0))
    carbon_saved = estimates.est_carbon_g(energy_saved)
    compute_full = estimates.est_compute(orig_tokens, model_name)
    compute_comp = estimates.est_compute(comp_tokens, model_name)
    gpu_ms_full = compute_full["est_gpu_ms"]
    gpu_ms_saved = round(gpu_ms_full - compute_comp["est_gpu_ms"], 4)
    compute_reduction = (
        round(gpu_ms_saved / gpu_ms_full * 100, 1) if gpu_ms_full else 0.0
    )
    _span("estimates", _s)

    result = CompressionResult(
        original_text=text,
        compressed_text=compressed,
        mode=mode,
        target_model=model_name,
        original_tokens=orig_tokens,
        compressed_tokens=comp_tokens,
        tokens_saved=saved,
        reduction_pct=reduction,
        latency_ms=latency_ms,
        cpu_ms=cpu_ms,
        peak_ram_mb=peak_ram_mb,
        fillers_removed=find_fillers(text),
        redundancy_pct=redundancy_pct(text),
        est_cost_saved_usd=cost_saved,
        est_energy_saved_wh=energy_saved,
        est_carbon_saved_g=carbon_saved,
        est_gpu_ms_per_call=gpu_ms_full,
        est_gpu_ms_saved=gpu_ms_saved,
        compute_reduction_pct=compute_reduction,
        estimates_meta=estimates.estimates_meta(model_name),
        routed=routed,
        spans=spans,
    )

    if use_cache:
        # store a clean (non-cache-flagged) copy for future hits
        cache.put(text, target_ratio, quality, model_name, result.to_dict())
    return result
