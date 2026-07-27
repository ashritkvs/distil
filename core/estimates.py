"""Estimation: cost, energy, carbon, and compute-load (GPU replacement).

Every function here returns an ESTIMATE derived from a named, defensible
method. Estimates are clearly separated from measured metrics upstream. None
of these are faked measurements.

Key honesty notes:
  * Closed-model parameter counts (gpt-4o-mini, gpt-4o) are NOT public, so we
    use ASSUMED values flagged `params_known=False`.
  * Energy and carbon factors are configurable defaults from published ranges;
    a live carbon-intensity API can replace the default in a later phase.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Model registry — prices are USD per 1M tokens; params drive compute estimates
# --------------------------------------------------------------------------- #

MODELS: dict[str, dict] = {
    # Closed models: prices public, parameter counts NOT public (assumed).
    "gpt-4o-mini": {"price_in": 0.15, "price_out": 0.60,
                    "params": 8e9, "params_known": False},
    "gpt-4o": {"price_in": 2.50, "price_out": 10.0,
               "params": 200e9, "params_known": False},
    "gpt-4.1": {"price_in": 2.00, "price_out": 8.00,
                "params": 200e9, "params_known": False},
    # Open models: parameter counts are real.
    "llama-3-8b": {"price_in": 0.20, "price_out": 0.20,
                   "params": 8e9, "params_known": True},
    "llama-3-70b": {"price_in": 0.90, "price_out": 0.90,
                    "params": 70e9, "params_known": True},
    "mixtral-8x7b": {"price_in": 0.50, "price_out": 0.50,
                     "params": 46.7e9, "params_known": True},
}

DEFAULT_MODEL = "gpt-4o-mini"

# --------------------------------------------------------------------------- #
# Energy + carbon factors (published-range defaults; configurable)
# --------------------------------------------------------------------------- #

# Approximate inference energy per 1K tokens (Wh). Public estimates vary widely
# by model/hardware; this is a mid-range, clearly-flagged default.
ENERGY_WH_PER_1K_TOKENS = 0.4

# Grid carbon intensity (grams CO2 per kWh). ~global average; a live regional
# API (e.g. UK Carbon Intensity) can replace this in a later phase.
CARBON_G_PER_KWH = 400.0

# --------------------------------------------------------------------------- #
# Compute-load reference (replaces Distil's measured GPU metrics)
# --------------------------------------------------------------------------- #

# Reference accelerator for translating estimated FLOPs -> estimated GPU time.
REFERENCE_GPU = {
    "name": "NVIDIA A100 (FP16)",
    "flops": 312e12,     # peak FP16 FLOPS
    "assumed_mfu": 0.30,  # model FLOPs utilization (realistic effective share)
}


def resolve_model(name: str | None) -> tuple[str, dict]:
    """Return (model_name, spec), falling back to the default model."""
    key = (name or DEFAULT_MODEL).lower()
    if key not in MODELS:
        key = DEFAULT_MODEL
    return key, MODELS[key]


def est_cost_usd(tokens: int, model: str | None, direction: str = "in") -> float:
    """Estimated USD cost for `tokens` on `model` (input or output price)."""
    _, spec = resolve_model(model)
    price = spec["price_in"] if direction == "in" else spec["price_out"]
    return round(tokens / 1_000_000 * price, 8)


def est_energy_wh(tokens: int) -> float:
    """Estimated inference energy (Wh) for `tokens`."""
    return round(tokens / 1000 * ENERGY_WH_PER_1K_TOKENS, 6)


def est_carbon_g(energy_wh: float) -> float:
    """Estimated carbon (g CO2) for a given energy in Wh."""
    return round(energy_wh / 1000 * CARBON_G_PER_KWH, 6)


def est_compute(tokens: int, model: str | None) -> dict:
    """Estimated inference compute load for `tokens` on `model`.

    Uses the standard transformer approximation FLOPs ~= 2 * params * tokens
    (the paper's Compute ~ T x P x L), converted to estimated GPU-milliseconds
    against the reference accelerator.
    """
    name, spec = resolve_model(model)
    flops = 2 * spec["params"] * tokens
    gpu_seconds = flops / (REFERENCE_GPU["flops"] * REFERENCE_GPU["assumed_mfu"])
    return {
        "model": name,
        "params_known": spec["params_known"],
        "est_flops": flops,
        "est_gpu_ms": round(gpu_seconds * 1000, 4),
    }


def estimates_meta(model: str | None) -> dict:
    """Describe the assumptions behind every estimate (for transparency)."""
    name, spec = resolve_model(model)
    return {
        "estimated": True,
        "target_model": name,
        "params": spec["params"],
        "params_known": spec["params_known"],
        "energy_wh_per_1k_tokens": ENERGY_WH_PER_1K_TOKENS,
        "carbon_g_per_kwh": CARBON_G_PER_KWH,
        "reference_gpu": REFERENCE_GPU,
        "note": (
            "Cost/energy/carbon/compute are estimates. Closed-model params are "
            "assumed (params_known=false). Token counts and latency are measured."
        ),
    }
