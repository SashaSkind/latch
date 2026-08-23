"""In-process batch runtime with the serial execution baseline."""

from __future__ import annotations

import inspect
import time
import uuid
from collections.abc import Sequence

from runtime.contracts import (
    DeadlineState,
    EventCallback,
    ExecutionMode,
    ExecutionResult,
    SpanEvent,
    ToolCall,
    ToolResult,
)
from runtime.registry import ToolRegistry


class Runtime:
    """Execute registered async tool calls and record observable results."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        event_callback: EventCallback | None = None,
    ) -> None:
        self._registry = registry
        self._event_callback = event_callback

    async def execute_batch(
        self,
        calls: Sequence[ToolCall],
        *,
        deadline_ms: int,
        mode: ExecutionMode = "optimized",
        event_callback: EventCallback | None = None,
    ) -> ExecutionResult:
        """Execute calls serially in input order and emit one span per call."""

        if deadline_ms <= 0:
            raise ValueError("deadline_ms must be greater than zero")
        if mode != "serial":
            raise NotImplementedError("optimized mode requires the scheduler")

        callback = (
            event_callback
            if event_callback is not None
            else self._event_callback
        )
        run_id = uuid.uuid4().hex
        batch_started_at_ms = _monotonic_ms()
        outputs: list[ToolResult] = []
        spans: list[SpanEvent] = []

        for call in calls:
            tool = self._registry.get(call.tool_name)
            started_at_ms = _monotonic_ms()

            try:
                output = await tool.handler(**call.arguments)
            except Exception as error:
                result = ToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    output=None,
                    status="error",
                    error=f"{type(error).__name__}: {error}",
                )
            else:
                result = ToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    output=output,
                    status="ok",
                )

            ended_at_ms = _monotonic_ms()
            span = SpanEvent(
                run_id=run_id,
                call_id=call.call_id,
                tool_name=call.tool_name,
                event_type="tool_execution",
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
                duration_ms=ended_at_ms - started_at_ms,
                read_resources=tuple(sorted(tool.read_resources)),
                written_resources=tuple(sorted(tool.written_resources)),
                cache_status="not_checked",
                deadline_path="full",
                remaining_budget_ms=deadline_ms
                - (ended_at_ms - batch_started_at_ms),
                status=result.status,
            )
            outputs.append(result)
            spans.append(span)
            await _emit(callback, span)

        elapsed_ms = _monotonic_ms() - batch_started_at_ms
        deadline_status = (
            DeadlineState.MET
            if elapsed_ms <= deadline_ms
            else DeadlineState.MISSED
        )
        return ExecutionResult(
            tool_outputs=tuple(outputs),
            spans=tuple(spans),
            deadline_path="full",
            elapsed_ms=elapsed_ms,
            deadline_status=deadline_status,
        )


async def _emit(callback: EventCallback | None, event: SpanEvent) -> None:
    if callback is None:
        return

    callback_result = callback(event)
    if inspect.isawaitable(callback_result):
        await callback_result


def _monotonic_ms() -> float:
    return time.perf_counter_ns() / 1_000_000
