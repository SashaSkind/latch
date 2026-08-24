from __future__ import annotations

import asyncio
from pathlib import Path

from app.server import scale_benchmark


ROOT = Path(__file__).resolve().parents[2]


def test_scale_benchmark_exposes_consistent_recorded_metrics() -> None:
    payload = asyncio.run(scale_benchmark())
    latch = payload["latch"]
    plain = payload["plain_codex"]
    comparison = payload["comparison"]

    assert payload["workload"] == {
        "corpus_mib": 1024,
        "checks": 32,
        "deadline_ms": 60_000,
        "operation": "independent full-corpus regex searches",
    }
    assert latch["operations"] == plain["operations"] == 32
    assert latch["deadline_status"] == "met"
    assert latch["reported_failures"] == 0
    assert plain["reported_failures"] == 32
    assert comparison["latency_saved_ms"] == (
        plain["end_to_end_ms"] - latch["end_to_end_ms"]
    )
    assert comparison["end_to_end_speedup"] == round(
        plain["end_to_end_ms"] / latch["end_to_end_ms"],
        2,
    )
    assert comparison["latency_reduction_percent"] == round(
        comparison["latency_saved_ms"] / plain["end_to_end_ms"] * 100,
        2,
    )
    assert comparison["tool_call_compression"] == (
        plain["tool_calls"] / latch["tool_calls"]
    )


def test_dashboard_loads_and_labels_the_scale_benchmark() -> None:
    page = (ROOT / "app" / "index.html").read_text(encoding="utf-8")

    assert "Codex MCP scale benchmark" in page
    assert "fetch('/api/scale-benchmark')" in page
    assert "Token usage comparison" in page
    assert "no synthetic sleeps" in page
