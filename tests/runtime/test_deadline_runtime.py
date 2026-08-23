import asyncio

import pytest

from runtime.contracts import SpanEvent, ToolCall
from runtime.registry import ToolRegistry
from runtime.runtime import Runtime


@pytest.mark.parametrize(
    ("deadline_ms", "expected_path", "expected_statuses", "executed"),
    [
        (
            1_200,
            "full",
            ["ok", "ok", "ok"],
            ["docs", "tests", "essential"],
        ),
        (
            500,
            "fast",
            ["skipped", "ok", "ok"],
            ["tests", "essential"],
        ),
        (
            100,
            "partial",
            ["skipped", "skipped", "ok"],
            ["essential"],
        ),
    ],
)
def test_deadline_path_executes_only_eligible_calls(
    deadline_ms: int,
    expected_path: str,
    expected_statuses: list[str],
    executed: list[str],
) -> None:
    actual_executed: list[str] = []
    emitted: list[SpanEvent] = []

    async def record(label: str) -> str:
        actual_executed.append(label)
        return label

    registry = ToolRegistry()
    registry.register(
        "search_docs",
        record,
        deadline_paths=["full"],
    )
    registry.register(
        "read_tests",
        record,
        deadline_paths=["full", "fast"],
    )
    registry.register("essential", record)
    calls = [
        ToolCall("docs", "search_docs", {"label": "docs"}),
        ToolCall("tests", "read_tests", {"label": "tests"}),
        ToolCall("essential", "essential", {"label": "essential"}),
    ]

    result = asyncio.run(
        Runtime(registry).execute_batch(
            calls,
            deadline_ms=deadline_ms,
            mode="optimized",
            event_callback=emitted.append,
        )
    )

    assert result.deadline_path == expected_path
    assert [output.status for output in result.tool_outputs] == (
        expected_statuses
    )
    assert [span.call_id for span in result.spans] == [
        "docs",
        "tests",
        "essential",
    ]
    assert actual_executed == executed
    assert {span.deadline_path for span in result.spans} == {expected_path}
    assert {span.call_id for span in emitted} == {
        "docs",
        "tests",
        "essential",
    }

    for output, span in zip(
        result.tool_outputs,
        result.spans,
        strict=True,
    ):
        if output.status == "skipped":
            assert output.output is None
            assert span.event_type == "deadline_skip"
            assert span.duration_ms == 0
            assert span.cache_status == "not_applicable"
            assert span.status == "skipped"
        else:
            assert span.event_type == "tool_execution"
            assert span.status == "ok"


def test_registry_rejects_invalid_deadline_paths() -> None:
    async def unused() -> None:
        return None

    registry = ToolRegistry()

    with pytest.raises(ValueError, match="at least one"):
        registry.register("none", unused, deadline_paths=[])
    with pytest.raises(ValueError, match="unknown deadline paths"):
        registry.register("invalid", unused, deadline_paths=["turbo"])
