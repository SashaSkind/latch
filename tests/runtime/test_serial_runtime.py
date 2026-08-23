import asyncio

from runtime.contracts import DeadlineState, ResourceKey, SpanEvent, ToolCall
from runtime.registry import ToolRegistry
from runtime.runtime import Runtime


def test_serial_batch_runs_in_order_and_emits_timed_trace() -> None:
    activity: list[str] = []
    emitted_events: list[SpanEvent] = []

    async def read_fixture(label: str) -> str:
        activity.append(f"start:{label}")
        await asyncio.sleep(0.002)
        activity.append(f"end:{label}")
        return f"contents:{label}"

    registry = ToolRegistry()
    registry.register(
        "read_fixture",
        read_fixture,
        read_resources=[ResourceKey("file:src/auth.py")],
    )
    runtime = Runtime(registry)
    calls = [
        ToolCall("call-1", "read_fixture", {"label": "implementation"}),
        ToolCall("call-2", "read_fixture", {"label": "tests"}),
    ]

    result = asyncio.run(
        runtime.execute_batch(
            calls,
            deadline_ms=1_000,
            mode="serial",
            event_callback=emitted_events.append,
        )
    )

    assert activity == [
        "start:implementation",
        "end:implementation",
        "start:tests",
        "end:tests",
    ]
    assert [tool_result.output for tool_result in result.tool_outputs] == [
        "contents:implementation",
        "contents:tests",
    ]
    assert [span.call_id for span in result.spans] == ["call-1", "call-2"]
    assert emitted_events == list(result.spans)

    assert result.elapsed_ms > 0
    assert result.elapsed_ms >= sum(span.duration_ms for span in result.spans)
    assert result.deadline_path == "full"
    assert result.deadline_status is DeadlineState.MET

    assert len({span.run_id for span in result.spans}) == 1
    for span in result.spans:
        assert span.event_type == "tool_execution"
        assert span.started_at_ms <= span.ended_at_ms
        assert span.duration_ms > 0
        assert span.read_resources == (ResourceKey("file:src/auth.py"),)
        assert span.written_resources == ()
        assert span.cache_status == "miss"
        assert span.remaining_budget_ms > 0
        assert span.status == "ok"
