"""Command-line comparison used during the two-minute demo."""

from __future__ import annotations

import argparse
import asyncio

from .trials import run_trials


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic serial and optimized trials.")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--budget-ms", type=int, default=1200)
    args = parser.parse_args()
    summary = asyncio.run(run_trials(args.trials, args.budget_ms))
    print(f"Trials: {summary.trials} | Budget: {summary.budget_ms}ms")
    print(f"Serial:    P50 {summary.serial_p50_ms:.0f}ms | P95 {summary.serial_p95_ms:.0f}ms")
    print(f"Optimized: P50 {summary.optimized_p50_ms:.0f}ms | P95 {summary.optimized_p95_ms:.0f}ms")
    print(f"Speedup: {summary.speedup:.2f}x | Deadline hit rate: {summary.optimized_deadline_hit_rate:.0%}")


if __name__ == "__main__":
    main()
