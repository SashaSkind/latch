"""Identical serial and optimized synthetic coding-task orchestration."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Literal

from telemetry.events import EventCallback, SpanEvent

from .tools import SyntheticTools, ToolCall, ToolResult

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
    if budget_ms >= 900:
        return "full"
    if budget_ms >= 500:
        return "fast"
    return "partial"


async def run_task(mode: Mode, budget_ms: int, seed: int = 7, emit: EventCallback | None = None) -> DemoResult:
    run_id = f"{mode}-{uuid.uuid4().hex[:8]}"
    events: list[SpanEvent] = []
    callback = emit or events.append
    path = deadline_path_for(budget_ms)
    started = perf_counter()
    tools = SyntheticTools(seed=seed, emit=callback, run_id=run_id, deadline_path=path)
    callback(SpanEvent(
        run_id=run_id, call_id="deadline", tool_name="deadline_controller", event_type="deadline_decision",
        started_at_ms=0, ended_at_ms=0, duration_ms=0, deadline_path=path,
        remaining_budget_ms=budget_ms, detail=f"selected {path} path for {budget_ms}ms budget",
    ))

    calls = {
        "warm": ToolCall("warm-auth", "read_auth", {"path": "src/auth.py"}, ("file:src/auth.py",)),
        "implementation": ToolCall("inspect-auth", "read_auth", {"path": "src/auth.py"}, ("file:src/auth.py",)),
        "tests": ToolCall("inspect-tests", "read_tests", {"path": "tests/test_auth.py"}, ("file:tests/test_auth.py",)),
        "docs": ToolCall("search-docs", "search_docs", {"query": "auth token"}, ("doc:README.md",)),
        "architecture": ToolCall("read-architecture", "read_architecture", {"path": "architecture.md"}, ("doc:architecture.md",)),
        "edit": ToolCall("edit-auth", "edit_auth", {"path": "src/auth.py"}, written_resources=("file:src/auth.py",)),
        "verify": ToolCall("verify-auth", "read_auth", {"path": "src/auth.py"}, ("file:src/auth.py",)),
        "test": ToolCall("run-auth-tests", "run_tests", {"target": "tests/test_auth.py"}, ("file:src/auth.py",)),
    }
    outputs: dict[str, str] = {}

    async def run(name: str) -> None:
        result: ToolResult = await tools.execute(calls[name])
        outputs[name] = result.output

    try:
        async with asyncio.timeout(budget_ms / 1000):
            await run("warm")
            await run("implementation")  # proves canonical cache reuse before the fan-out
            reads = ["tests", "architecture"] if path == "fast" else ["tests", "docs", "architecture"]
            if path == "partial":
                reads = ["tests"]
            if mode == "optimized":
                await asyncio.gather(*(run(name) for name in reads))
            else:
                for name in reads:
                    await run(name)
            await run("edit")
            await run("verify")
            await run("test")
        deadline_status = "met"
    except TimeoutError:
        deadline_status = "missed"

    elapsed_ms = (perf_counter() - started) * 1000
    all_events = events if emit is None else []
    return DemoResult(run_id, mode, path, elapsed_ms, deadline_status, outputs, [asdict(event) for event in all_events])
