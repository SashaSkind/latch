"""Seeded serial-versus-optimized benchmark runner."""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass

from .task import DemoResult, run_task


@dataclass(frozen=True, slots=True)
class TrialSummary:
    trials: int
    budget_ms: int
    serial_p50_ms: float
    serial_p95_ms: float
    optimized_p50_ms: float
    optimized_p95_ms: float
    speedup: float
    optimized_deadline_hit_rate: float


def percentile(values: list[float], value: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * value)
    return ordered[index]


async def run_trials(trials: int, budget_ms: int) -> TrialSummary:
    serial: list[DemoResult] = []
    optimized: list[DemoResult] = []
    for seed in range(trials):
        serial.append(await run_task("serial", budget_ms, seed))
        optimized.append(await run_task("optimized", budget_ms, seed))
    serial_times = [result.elapsed_ms for result in serial]
    optimized_times = [result.elapsed_ms for result in optimized]
    return TrialSummary(
        trials=trials,
        budget_ms=budget_ms,
        serial_p50_ms=statistics.median(serial_times),
        serial_p95_ms=percentile(serial_times, 0.95),
        optimized_p50_ms=statistics.median(optimized_times),
        optimized_p95_ms=percentile(optimized_times, 0.95),
        speedup=statistics.median(serial_times) / statistics.median(optimized_times),
        optimized_deadline_hit_rate=sum(result.deadline_status == "met" for result in optimized) / trials,
    )
