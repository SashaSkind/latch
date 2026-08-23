"""In-process runtime for serial and conflict-aware batch execution."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Sequence

from runtime.budget import DeadlinePolicy
from runtime.cache import ResourceVersionCache
from runtime.contracts import (
    DeadlinePath,
    DeadlineState,
    EventCallback,
    ExecutionMode,
    ExecutionResult,
    SpanEvent,
    ToolCall,
    ToolResult,
)
from runtime.registry import ToolRegistry
from runtime.scheduler import build_execution_waves


class Runtime:
    """Execute registered async tool calls and record observable results."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        cache: ResourceVersionCache | None = None,
        deadline_policy: DeadlinePolicy | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        if cache is not None and cache.fixture_root != registry.fixture_root:
            raise ValueError("cache and registry fixture roots must match")

        self._registry = registry
        self._cache = cache or ResourceVersionCache(
            fixture_root=registry.fixture_root
        )
        self._deadline_policy = deadline_policy or DeadlinePolicy()
        self._event_callback = event_callback

    async def execute_batch(
        self,
        calls: Sequence[ToolCall],
        *,
        deadline_ms: int,
        mode: ExecutionMode = "optimized",
        event_callback: EventCallback | None = None,
    ) -> ExecutionResult:
        """Execute an ordered batch and emit one completed span per call."""

        if deadline_ms <= 0:
            raise ValueError("deadline_ms must be greater than zero")
        if mode not in ("serial", "optimized"):
            raise ValueError(f"unknown execution mode: {mode}")

        deadline_path = self._deadline_policy.select_path(deadline_ms)
        callback = (
            event_callback
            if event_callback is not None
            else self._event_callback
        )
        run_id = uuid.uuid4().hex
        batch_started_at_ms = _monotonic_ms()
        output_slots: list[ToolResult | None] = [None] * len(calls)
        span_slots: list[SpanEvent | None] = [None] * len(calls)
        positions: defaultdict[int, deque[int]] = defaultdict(deque)
        runnable_calls: list[ToolCall] = []

        for position, call in enumerate(calls):
            tool = self._registry.get(call.tool_name)
            if deadline_path in tool.deadline_paths:
                runnable_calls.append(call)
                positions[id(call)].append(position)
                continue

            result, span = self._skipped_call(
                call,
                run_id=run_id,
                batch_started_at_ms=batch_started_at_ms,
                deadline_ms=deadline_ms,
                deadline_path=deadline_path,
            )
            output_slots[position] = result
            span_slots[position] = span
            await _emit(callback, span)

        if mode == "serial":
            waves = tuple((call,) for call in runnable_calls)
        else:
            waves = build_execution_waves(runnable_calls, self._registry)

        for wave in waves:
            indexed_wave = [
                (positions[id(call)].popleft(), call) for call in wave
            ]
            completed_wave = await asyncio.gather(
                *(
                    self._execute_call(
                        call,
                        run_id=run_id,
                        batch_started_at_ms=batch_started_at_ms,
                        deadline_ms=deadline_ms,
                        deadline_path=deadline_path,
                    )
                    for _, call in indexed_wave
                )
            )

            for (position, _), (result, span) in zip(
                indexed_wave,
                completed_wave,
                strict=True,
            ):
                output_slots[position] = result
                span_slots[position] = span
                await _emit(callback, span)

        outputs = tuple(result for result in output_slots if result is not None)
        spans = tuple(span for span in span_slots if span is not None)
        if len(outputs) != len(calls) or len(spans) != len(calls):
            raise RuntimeError("runtime did not complete every scheduled call")

        elapsed_ms = _monotonic_ms() - batch_started_at_ms
        deadline_status = (
            DeadlineState.MET
            if elapsed_ms <= deadline_ms
            else DeadlineState.MISSED
        )
        return ExecutionResult(
            tool_outputs=outputs,
            spans=spans,
            deadline_path=deadline_path,
            elapsed_ms=elapsed_ms,
            deadline_status=deadline_status,
        )

    async def _execute_call(
        self,
        call: ToolCall,
        *,
        run_id: str,
        batch_started_at_ms: float,
        deadline_ms: int,
        deadline_path: DeadlinePath,
    ) -> tuple[ToolResult, SpanEvent]:
        tool = self._registry.get(call.tool_name)
        started_at_ms = _monotonic_ms()
        cacheable_read = bool(tool.read_resources) and not (
            tool.written_resources
        )
        cache_status = "not_cacheable"
        result: ToolResult | None = None

        if cacheable_read:
            lookup = self._cache.lookup(
                call.tool_name,
                call.arguments,
                tool.read_resources,
            )
            if lookup.hit:
                cache_status = "hit"
                result = ToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    output=lookup.value,
                    status="ok",
                )
            else:
                cache_status = "miss"

        if result is None:
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
                if tool.written_resources:
                    cache_status = "not_invalidated"
            else:
                result = ToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    output=output,
                    status="ok",
                )
                if tool.written_resources:
                    self._cache.advance_versions(tool.written_resources)
                    cache_status = "invalidated"
                elif cacheable_read:
                    self._cache.store(
                        call.tool_name,
                        call.arguments,
                        tool.read_resources,
                        output,
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
            cache_status=cache_status,
            deadline_path=deadline_path,
            remaining_budget_ms=deadline_ms
            - (ended_at_ms - batch_started_at_ms),
            status=result.status,
        )
        return result, span

    def _skipped_call(
        self,
        call: ToolCall,
        *,
        run_id: str,
        batch_started_at_ms: float,
        deadline_ms: int,
        deadline_path: DeadlinePath,
    ) -> tuple[ToolResult, SpanEvent]:
        tool = self._registry.get(call.tool_name)
        skipped_at_ms = _monotonic_ms()
        result = ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            output=None,
            status="skipped",
        )
        span = SpanEvent(
            run_id=run_id,
            call_id=call.call_id,
            tool_name=call.tool_name,
            event_type="deadline_skip",
            started_at_ms=skipped_at_ms,
            ended_at_ms=skipped_at_ms,
            duration_ms=0.0,
            read_resources=tuple(sorted(tool.read_resources)),
            written_resources=tuple(sorted(tool.written_resources)),
            cache_status="not_applicable",
            deadline_path=deadline_path,
            remaining_budget_ms=deadline_ms
            - (skipped_at_ms - batch_started_at_ms),
            status="skipped",
        )
        return result, span


async def _emit(callback: EventCallback | None, event: SpanEvent) -> None:
    if callback is None:
        return

    callback_result = callback(event)
    if inspect.isawaitable(callback_result):
        await callback_result


def _monotonic_ms() -> float:
    return time.perf_counter_ns() / 1_000_000
