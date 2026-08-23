from __future__ import annotations

import asyncio

from demo.task import deadline_path_for, run_task
from demo.tools import SyntheticTools, ToolCall, build_registry
from runtime.runtime import Runtime


SPAN_EVENT_FIELDS = {
    "run_id",
    "call_id",
    "tool_name",
    "event_type",
    "started_at_ms",
    "ended_at_ms",
    "duration_ms",
    "read_resources",
    "written_resources",
    "cache_status",
    "deadline_path",
    "remaining_budget_ms",
    "status",
}


def test_optimized_reads_overlap_and_preserve_correctness() -> None:
    result = asyncio.run(run_task("optimized", budget_ms=1200, seed=3))
    spans = [event for event in result.events if event["event_type"] == "tool"]
    fanout = [
        span
        for span in spans
        if span["tool_name"]
        in {"read_tests", "search_docs", "read_architecture"}
    ]
    assert result.deadline_status == "met"
    assert result.outputs["test"] == "2 passed"
    assert ".strip()" in result.outputs["verify"]
    assert max(float(span["started_at_ms"]) for span in fanout) - min(
        float(span["started_at_ms"]) for span in fanout
    ) < 25


def test_demo_exposes_locked_runtime_event_contract() -> None:
    result = asyncio.run(run_task("optimized", budget_ms=1200, seed=3))

    assert result.events
    assert all(set(event) == SPAN_EVENT_FIELDS for event in result.events)
    assert len({event["run_id"] for event in result.events}) == 1


def test_read_after_write_cannot_use_old_cache_entry() -> None:
    result = asyncio.run(run_task("optimized", budget_ms=1200, seed=3))
    verify = next(
        event
        for event in result.events
        if event["call_id"] == "verify-auth" and event["event_type"] == "tool"
    )
    assert verify["cache_status"] == "miss"
    assert ".strip()" in result.outputs["verify"]


def test_equivalent_normalized_arguments_share_cache_key() -> None:
    tools = SyntheticTools(seed=1)
    runtime = Runtime(build_registry(tools))
    first = ToolCall(
        "first",
        "read_auth",
        {"path": "src/auth.py", "options": {"a": 1, "b": 2}},
    )
    equivalent = ToolCall(
        "second",
        "read_auth",
        {"options": {"b": 2, "a": 1}, "path": "src/auth.py"},
    )

    async def execute() -> tuple[str, str]:
        first_result = await runtime.execute_batch(
            [first],
            deadline_ms=1_200,
        )
        second_result = await runtime.execute_batch(
            [equivalent],
            deadline_ms=1_200,
        )
        return (
            first_result.spans[0].cache_status,
            second_result.spans[0].cache_status,
        )

    assert asyncio.run(execute()) == ("miss", "hit")


def test_full_fast_and_partial_paths_are_deterministic() -> None:
    assert deadline_path_for(1200) == "full"
    assert deadline_path_for(700) == "fast"
    assert deadline_path_for(300) == "partial"


def test_fast_path_skips_thorough_docs_and_meets_deadline() -> None:
    result = asyncio.run(run_task("optimized", budget_ms=700, seed=3))
    docs_span = next(
        event for event in result.events if event["call_id"] == "search-docs"
    )

    assert result.deadline_path == "fast"
    assert result.deadline_status == "met"
    assert result.elapsed_ms < 700
    assert docs_span["status"] == "skipped"
    assert result.outputs["test"] == "2 passed"


def test_optimized_is_at_least_twice_as_fast_for_seeded_scenario() -> None:
    async def compare() -> tuple[float, float, dict[str, str], dict[str, str]]:
        serial = await run_task("serial", budget_ms=1200, seed=5)
        optimized = await run_task("optimized", budget_ms=1200, seed=5)
        return (
            serial.elapsed_ms,
            optimized.elapsed_ms,
            serial.outputs,
            optimized.outputs,
        )

    serial_ms, optimized_ms, serial_outputs, optimized_outputs = asyncio.run(compare())
    assert serial_ms / optimized_ms >= 2
    assert optimized_outputs == serial_outputs
