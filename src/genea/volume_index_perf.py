"""Best-effort operational measurements, separate from research evidence."""
from __future__ import annotations

import math
import time
from pathlib import Path


UNKNOWN = "unknown"


def clock() -> float | None:
    try:
        return time.perf_counter()
    except Exception:
        return None


def elapsed(start: float | None) -> float | str:
    try:
        end = clock()
        if start is None or end is None:
            return UNKNOWN
        return max(0.0, end - start)
    except Exception:
        return UNKNOWN


def output_bytes(root: Path) -> int | str:
    try:
        return sum(path.stat().st_size for path in root.rglob("*")
                   if path.is_file() and not path.is_symlink())
    except Exception:
        return UNKNOWN


def artifact_bytes(paths: list[Path]) -> int | str:
    try:
        return sum(path.stat().st_size for path in set(paths))
    except Exception:
        return UNKNOWN


def evaluate_performance_gate(
    baseline_seconds: float | None,
    candidate_seconds: float | None,
    *,
    equivalent_outputs: bool | None,
    minimum_speedup: float = 1.2,
) -> dict:
    """Evaluate supplied benchmark observations; never invent missing measurements.

    Callers must compare source/config fingerprints, locators, regions and search
    results before setting equivalent_outputs. Timing alone cannot pass the gate.
    This pure stdlib helper neither runs a benchmark nor installs a toolchain.
    """
    if not math.isfinite(minimum_speedup) or minimum_speedup < 1:
        raise ValueError("minimum_speedup must be finite and >= 1")
    measured = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) and value > 0
                   for value in (baseline_seconds, candidate_seconds))
    speedup = baseline_seconds / candidate_seconds if measured else UNKNOWN
    reasons = []
    if equivalent_outputs is not True:
        reasons.append("output_equivalence_not_verified")
    if not measured:
        reasons.append("timings_unknown")
    elif speedup < minimum_speedup:
        reasons.append("speedup_below_threshold")
    return {"status": "passed" if not reasons else "blocked", "speedup": speedup,
            "minimum_speedup": minimum_speedup, "reasons": reasons,
            "scope": "operational_benchmark_not_research_quality"}
