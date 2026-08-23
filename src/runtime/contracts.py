"""PROPOSED — pending 5-min lock with Person B.

Shared, serialization-friendly contracts for the in-process runtime and the
demo telemetry boundary. Timestamps are monotonic milliseconds within a run;
they are suitable for durations and ordering, not as wall-clock timestamps.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NewType, Protocol, TypeAlias


ResourceKey = NewType("ResourceKey", str)
ExecutionMode: TypeAlias = Literal["serial", "optimized"]
DeadlinePath: TypeAlias = Literal["full", "fast", "partial"]


class DeadlineState(StrEnum):
    """Terminal relationship between an execution and its deadline."""

    MET = "met"
    MISSED = "missed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One ordered tool invocation supplied to ``execute_batch``."""

    call_id: str
    tool_name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The output or captured failure for one tool invocation."""

    call_id: str
    tool_name: str
    output: object | None
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SpanEvent:
    """One completed runtime event emitted to telemetry."""

    run_id: str
    call_id: str
    tool_name: str
    event_type: str
    started_at_ms: float
    ended_at_ms: float
    duration_ms: float
    read_resources: tuple[ResourceKey, ...]
    written_resources: tuple[ResourceKey, ...]
    cache_status: str
    deadline_path: DeadlinePath
    remaining_budget_ms: float
    status: str


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Complete observable result of one batch execution."""

    tool_outputs: tuple[ToolResult, ...]
    spans: tuple[SpanEvent, ...]
    deadline_path: DeadlinePath
    elapsed_ms: float
    deadline_status: DeadlineState


EventCallback: TypeAlias = Callable[[SpanEvent], None | Awaitable[None]]


class BatchRuntime(Protocol):
    """Stable public execution interface implemented by the runtime."""

    async def execute_batch(
        self,
        calls: Sequence[ToolCall],
        *,
        deadline_ms: int,
        mode: ExecutionMode = "optimized",
        event_callback: EventCallback | None = None,
    ) -> ExecutionResult:
        """Execute an ordered batch within the supplied latency budget."""

