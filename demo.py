"""Quick CLI demo of the compression core.

Usage:
    python demo.py                 # runs the built-in sample
    python demo.py "your prompt"   # compress your own text
"""

from __future__ import annotations

import json
import sys

from core import compress

SAMPLE = (
    "Could you please possibly help me understand in a very detailed and "
    "comprehensive way what machine learning actually is, including all the "
    "different types and subtypes if at all possible?"
)


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else SAMPLE
    result = compress(text, target_ratio=0.5, quality=False)
    d = result.to_dict()

    print("\n--- ORIGINAL ---")
    print(d["original_text"])
    print("\n--- COMPRESSED ---")
    print(d["compressed_text"])

    print("\n--- MEASURED ---")
    for k in ["mode", "original_tokens", "compressed_tokens", "tokens_saved",
              "reduction_pct", "latency_ms", "cpu_ms", "peak_ram_mb",
              "redundancy_pct"]:
        print(f"  {k:22} {d[k]}")
    print(f"  {'fillers_removed':22} {d['fillers_removed']}")

    print("\n--- ESTIMATED (labeled) ---")
    for k in ["target_model", "est_cost_saved_usd", "est_energy_saved_wh",
              "est_carbon_saved_g", "est_gpu_ms_per_call", "est_gpu_ms_saved",
              "compute_reduction_pct"]:
        print(f"  {k:22} {d[k]}")

    print("\n--- estimates_meta ---")
    print(json.dumps(d["estimates_meta"], indent=2))


if __name__ == "__main__":
    main()
