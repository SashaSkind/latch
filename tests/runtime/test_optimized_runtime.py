import asyncio

from runtime.contracts import ResourceKey, ToolCall
from runtime.registry import ToolRegistry
from runtime.runtime import Runtime


def test_independent_reads_overlap_in_optimized_mode() -> None:
    active_calls = 0
    maximum_active_calls = 0

    async def read_fixture(label: str) -> str:
        nonlocal active_calls, maximum_active_calls
        active_calls += 1
        maximum_active_calls = max(maximum_active_calls, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        return label

    registry = ToolRegistry()
    for tool_name, resource_name in (
        ("read_auth", "file:src/auth.py"),
        ("read_tests", "file:tests/test_auth.py"),
        ("search_docs", "docs:local"),
    ):
        registry.register(
            tool_name,
            read_fixture,
            read_resources=[ResourceKey(resource_name)],
        )
    calls = [
        ToolCall("read-auth", "read_auth", {"label": "auth"}),
        ToolCall("read-tests", "read_tests", {"label": "tests"}),
        ToolCall("search-docs", "search_docs", {"label": "docs"}),
    ]

    result = asyncio.run(
        Runtime(registry).execute_batch(
            calls,
            deadline_ms=1_000,
            mode="optimized",
        )
    )

    assert maximum_active_calls == 3
    assert [output.output for output in result.tool_outputs] == [
        "auth",
        "tests",
        "docs",
    ]
    assert max(span.started_at_ms for span in result.spans) < min(
        span.ended_at_ms for span in result.spans
    )
    assert all(0 <= span.started_at_ms for span in result.spans)
    assert all(span.ended_at_ms <= result.elapsed_ms for span in result.spans)
    assert result.elapsed_ms < sum(span.duration_ms for span in result.spans)


def test_conflicts_stay_ordered_and_results_preserve_input_order() -> None:
    auth_resource = ResourceKey("file:src/auth.py")
    activity: list[str] = []
    active_writes = 0
    maximum_active_writes = 0
    state = {"auth": "original"}

    async def write_auth(value: str) -> str:
        nonlocal active_writes, maximum_active_writes
        active_writes += 1
        maximum_active_writes = max(maximum_active_writes, active_writes)
        activity.append(f"start-write:{value}")
        await asyncio.sleep(0.002)
        state["auth"] = value
        activity.append(f"end-write:{value}")
        active_writes -= 1
        return value

    async def read_auth() -> str:
        activity.append("read")
        return state["auth"]

    async def read_docs() -> str:
        activity.append("read-docs")
        return "docs"

    registry = ToolRegistry()
    registry.register(
        "write_auth",
        write_auth,
        written_resources=[auth_resource],
    )
    registry.register(
        "read_auth",
        read_auth,
        read_resources=[auth_resource],
    )
    registry.register(
        "read_docs",
        read_docs,
        read_resources=[ResourceKey("docs:local")],
    )
    calls = [
        ToolCall("write-first", "write_auth", {"value": "first"}),
        ToolCall("write-second", "write_auth", {"value": "second"}),
        ToolCall("read-docs", "read_docs", {}),
        ToolCall("read-after", "read_auth", {}),
    ]

    result = asyncio.run(
        Runtime(registry).execute_batch(
            calls,
            deadline_ms=1_000,
            mode="optimized",
        )
    )

    assert maximum_active_writes == 1
    assert activity == [
        "start-write:first",
        "read-docs",
        "end-write:first",
        "start-write:second",
        "end-write:second",
        "read",
    ]
    assert [output.output for output in result.tool_outputs] == [
        "first",
        "second",
        "docs",
        "second",
    ]
    assert [span.call_id for span in result.spans] == [
        "write-first",
        "write-second",
        "read-docs",
        "read-after",
    ]
    spans_by_call_id = {span.call_id: span for span in result.spans}
    assert (
        spans_by_call_id["write-first"].ended_at_ms
        <= spans_by_call_id["write-second"].started_at_ms
    )
    assert (
        spans_by_call_id["write-second"].ended_at_ms
        <= spans_by_call_id["read-after"].started_at_ms
    )
