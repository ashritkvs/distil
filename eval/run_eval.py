"""Measured evaluation of the compression core over sample prompts.

Offline (heuristic path) — no API key needed. Prints a table + summary so the
compression quality is a *measured* number, not a claim.

Usage: python eval/run_eval.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import compress  # noqa: E402

PROMPTS = [
    "Could you please possibly help me understand in a very detailed and "
    "comprehensive way what machine learning actually is, including all the "
    "different types and subtypes if at all possible?",
    "I would like to kindly request that you please write a very detailed and "
    "thorough step by step guide explaining how to bake sourdough from scratch.",
    "Please explain the difference between TCP and UDP protocols and when you "
    "would actually want to use each one in real world networking.",
    "Could you help me understand how neural networks learn, and explain "
    "backpropagation in a fairly detailed way if possible?",
    "Explain the difference between HTTP and HTTPS.",
    "Summarize the theory of relativity in two sentences.",
    "Write a Python function that reverses a string.",
    "Define recursion and give one example.",
]


def main():
    rows = [compress(p, target_ratio=0.5) for p in PROMPTS]

    def pad(v, w):
        s = str(v)
        return (s[: w - 1] + "…" if len(s) > w else s).ljust(w)

    header = pad("#", 3) + pad("ORIG", 6) + pad("COMP", 6) + pad("RED%", 7) + \
        pad("GPU_MS_SAVED", 14) + "PREVIEW"
    print(header)
    print("-" * 70)
    for i, r in enumerate(rows, 1):
        print(pad(i, 3) + pad(r.original_tokens, 6) + pad(r.compressed_tokens, 6)
              + pad(r.reduction_pct, 7) + pad(r.est_gpu_ms_saved, 14)
              + r.compressed_text[:40])

    n = len(rows)
    avg_red = round(sum(r.reduction_pct for r in rows) / n, 1)
    tot_saved = sum(r.tokens_saved for r in rows)
    avg_lat = round(sum(r.latency_ms for r in rows) / n, 2)
    print("\n=== SUMMARY (measured) ===")
    print(f"Prompts:            {n}")
    print(f"Avg reduction:      {avg_red}%")
    print(f"Total tokens saved: {tot_saved}")
    print(f"Avg latency:        {avg_lat} ms")
    print("==========================")


if __name__ == "__main__":
    main()
