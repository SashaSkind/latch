from __future__ import annotations

import asyncio

from demo.task import deadline_path_for, run_task
from demo.tools import SyntheticTools, ToolCall


def test_optimized_reads_overlap_and_preserve_correctness() -> None:
    result = asyncio.run(run_task("optimized", budget_ms=1200, seed=3))
    spans = [event for event in result.events if event["event_type"] == "tool"]
    fanout = [span for span in spans if span["tool_name"] in {"read_tests", "search_docs", "read_architecture"}]
    assert result.deadline_status == "met"
    assert result.outputs["test"] == "2 passed"
    assert ".strip()" in result.outputs["verify"]
    assert max(float(span["started_at_ms"]) for span in fanout) - min(float(span["started_at_ms"]) for span in fanout) < 25


def test_read_after_write_cannot_use_old_cache_entry() -> None:
    result = asyncio.run(run_task("optimized", budget_ms=1200, seed=3))
    verify = next(event for event in result.events if event["call_id"] == "verify-auth" and event["event_type"] == "tool")
    assert verify["cache_status"] == "miss"
    assert ".strip()" in result.outputs["verify"]


def test_equivalent_normalized_arguments_share_cache_key() -> None:
    events = []
    tools = SyntheticTools(seed=1, emit=events.append, run_id="test", deadline_path="full")
    first = ToolCall("first", "read_auth", {"path": "src/auth.py", "options": {"a": 1, "b": 2}}, ("file:src/auth.py",))
    equivalent = ToolCall("second", "read_auth", {"options": {"b": 2, "a": 1}, "path": "src/auth.py"}, ("file:src/auth.py",))

    async def execute() -> tuple[str, str]:
        return (await tools.execute(first)).cache_status, (await tools.execute(equivalent)).cache_status

    assert asyncio.run(execute()) == ("miss", "hit")


def test_full_fast_and_partial_paths_are_deterministic() -> None:
    assert deadline_path_for(1200) == "full"
    assert deadline_path_for(700) == "fast"
    assert deadline_path_for(300) == "partial"


def test_optimized_is_at_least_twice_as_fast_for_seeded_scenario() -> None:
    async def compare() -> tuple[float, float]:
        serial = await run_task("serial", budget_ms=1200, seed=5)
        optimized = await run_task("optimized", budget_ms=1200, seed=5)
        return serial.elapsed_ms, optimized.elapsed_ms

    serial_ms, optimized_ms = asyncio.run(compare())
    assert serial_ms / optimized_ms >= 2
