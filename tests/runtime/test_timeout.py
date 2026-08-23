import asyncio
from time import perf_counter

from runtime.cache import ResourceVersionCache
from runtime.contracts import DeadlineState, ResourceKey, SpanEvent, ToolCall
from runtime.registry import ToolRegistry
from runtime.runtime import Runtime


def test_timeout_preserves_completed_call_and_marks_unfinished_call() -> None:
    slow_call_cancelled = False
    emitted: list[SpanEvent] = []

    async def fast_tool() -> str:
        return "fast result"

    async def slow_tool() -> str:
        nonlocal slow_call_cancelled
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            slow_call_cancelled = True
            raise
        return "late result"

    registry = ToolRegistry()
    registry.register("fast", fast_tool)
    registry.register("slow", slow_tool)
    calls = [
        ToolCall("fast-call", "fast", {}),
        ToolCall("slow-call", "slow", {}),
    ]

    started = perf_counter()
    result = asyncio.run(
        Runtime(registry).execute_batch(
            calls,
            deadline_ms=20,
            mode="optimized",
            event_callback=emitted.append,
        )
    )
    wall_elapsed_ms = (perf_counter() - started) * 1_000

    assert wall_elapsed_ms < 150
    assert result.elapsed_ms < 150
    assert result.deadline_status is DeadlineState.TIMED_OUT
    assert result.deadline_path == "partial"
    assert [output.status for output in result.tool_outputs] == [
        "ok",
        "timed_out",
    ]
    assert result.tool_outputs[0].output == "fast result"
    assert result.tool_outputs[1].error == "deadline exceeded"
    assert [span.event_type for span in result.spans] == [
        "tool",
        "deadline_timeout",
    ]
    assert [event.call_id for event in emitted] == [
        "fast-call",
        "slow-call",
    ]
    assert slow_call_cancelled is True


def test_timeout_prevents_later_conflicting_wave_from_starting() -> None:
    auth_resource = ResourceKey("file:src/auth.py")
    read_started = False

    async def slow_write() -> None:
        await asyncio.sleep(0.2)

    async def read_auth() -> str:
        nonlocal read_started
        read_started = True
        return "contents"

    registry = ToolRegistry()
    registry.register(
        "slow_write",
        slow_write,
        written_resources=[auth_resource],
    )
    registry.register(
        "read_auth",
        read_auth,
        read_resources=[auth_resource],
    )
    cache = ResourceVersionCache(fixture_root=registry.fixture_root)
    result = asyncio.run(
        Runtime(registry, cache=cache).execute_batch(
            [
                ToolCall("write", "slow_write", {}),
                ToolCall("read", "read_auth", {}),
            ],
            deadline_ms=20,
            mode="optimized",
        )
    )

    assert read_started is False
    assert [output.status for output in result.tool_outputs] == [
        "timed_out",
        "timed_out",
    ]
    assert [span.event_type for span in result.spans] == [
        "deadline_timeout",
        "deadline_timeout",
    ]
    assert result.deadline_status is DeadlineState.TIMED_OUT
    assert cache.version(auth_resource) == 0
