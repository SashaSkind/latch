"""Identical serial and optimized tasks executed by the real runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from runtime.budget import DeadlinePolicy
from runtime.contracts import EventCallback, SpanEvent, ToolCall
from runtime.runtime import Runtime

from .tools import SyntheticTools, build_registry

Mode = Literal["serial", "optimized"]


@dataclass(frozen=True, slots=True)
class DemoResult:
    run_id: str
    mode: Mode
    deadline_path: str
    elapsed_ms: float
    deadline_status: str
    outputs: dict[str, str]
    events: list[dict[str, object]]


def deadline_path_for(budget_ms: int) -> str:
    return DeadlinePolicy().select_path(budget_ms)


async def run_task(
    mode: Mode,
    budget_ms: int,
    seed: int = 7,
    emit: EventCallback | None = None,
) -> DemoResult:
    """Run one seeded coding task through ``Runtime.execute_batch``."""

    events: list[SpanEvent] = []
    callback = events.append if emit is None else emit
    tools = SyntheticTools(seed=seed)
    runtime = Runtime(build_registry(tools))

    await runtime.execute_batch(
        [
            ToolCall(
                "warm-auth",
                "read_auth",
                {"path": "src/auth.py"},
            )
        ],
        deadline_ms=budget_ms,
        mode="serial",
    )

    named_calls = [
        (
            "implementation",
            ToolCall(
                "inspect-auth",
                "read_auth",
                {"path": "src/auth.py"},
            ),
        ),
        (
            "tests",
            ToolCall(
                "inspect-tests",
                "read_tests",
                {"path": "tests/test_auth.py"},
            ),
        ),
        (
            "docs",
            ToolCall(
                "search-docs",
                "search_docs",
                {"query": "auth token"},
            ),
        ),
        (
            "architecture",
            ToolCall(
                "read-architecture",
                "read_architecture",
                {"path": "architecture.md"},
            ),
        ),
        (
            "edit",
            ToolCall(
                "edit-auth",
                "edit_auth",
                {"path": "src/auth.py"},
            ),
        ),
        (
            "verify",
            ToolCall(
                "verify-auth",
                "read_auth",
                {"path": "src/auth.py"},
            ),
        ),
        (
            "test",
            ToolCall(
                "run-auth-tests",
                "run_tests",
                {"target": "tests/test_auth.py"},
            ),
        ),
    ]
    execution = await runtime.execute_batch(
        [call for _, call in named_calls],
        deadline_ms=budget_ms,
        mode=mode,
        event_callback=callback,
    )

    outputs = {
        name: str(result.output)
        for (name, _), result in zip(
            named_calls,
            execution.tool_outputs,
            strict=True,
        )
        if result.status == "ok" and result.output is not None
    }
    serialized_events = (
        [event.to_dict() for event in events] if emit is None else []
    )
    return DemoResult(
        run_id=execution.spans[0].run_id,
        mode=mode,
        deadline_path=execution.deadline_path,
        elapsed_ms=execution.elapsed_ms,
        deadline_status=execution.deadline_status.value,
        outputs=outputs,
        events=serialized_events,
    )
