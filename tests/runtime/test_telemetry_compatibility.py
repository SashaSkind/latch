import asyncio

from runtime.contracts import ToolCall
from runtime.registry import ToolRegistry
from runtime.runtime import Runtime
from telemetry.store import TraceStore


def test_runtime_span_is_compatible_with_dashboard_trace_store() -> None:
    async def inspect_auth() -> str:
        return "auth contents"

    registry = ToolRegistry()
    registry.register("inspect_auth", inspect_auth)
    store = TraceStore()

    result = asyncio.run(
        Runtime(registry).execute_batch(
            [ToolCall("inspect", "inspect_auth", {})],
            deadline_ms=1_200,
            event_callback=store.record,
        )
    )

    recorded = store.events_for(result.spans[0].run_id)
    assert recorded == list(result.spans)
    assert recorded[0].to_dict() == {
        "run_id": result.spans[0].run_id,
        "call_id": "inspect",
        "tool_name": "inspect_auth",
        "event_type": "tool",
        "started_at_ms": result.spans[0].started_at_ms,
        "ended_at_ms": result.spans[0].ended_at_ms,
        "duration_ms": result.spans[0].duration_ms,
        "read_resources": (),
        "written_resources": (),
        "cache_status": "not_cacheable",
        "deadline_path": "full",
        "remaining_budget_ms": result.spans[0].remaining_budget_ms,
        "status": "ok",
    }
