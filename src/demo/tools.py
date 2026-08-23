"""Synthetic coding tools with deterministic latency and versioned read caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from telemetry.events import EventCallback, SpanEvent


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    read_resources: tuple[str, ...] = ()
    written_resources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    output: str
    cache_status: str


class SyntheticTools:
    """The fallback demo executor Person B can use before runtime integration."""

    def __init__(self, seed: int, emit: EventCallback, run_id: str, deadline_path: str) -> None:
        self.seed = seed
        self.emit = emit
        self.run_id = run_id
        self.deadline_path = deadline_path
        self.started = perf_counter()
        self.resources = {
            "file:src/auth.py": 'def authenticate(token: str) -> bool:\n    return token == "demo-token"\n',
            "file:tests/test_auth.py": "test valid and invalid authentication tokens\n",
            "doc:README.md": "strip surrounding whitespace before validating tokens\n",
            "doc:architecture.md": "authentication accepts one normalized bearer token\n",
        }
        self.versions = {resource: 0 for resource in self.resources}
        self.cache: dict[tuple[str, str, tuple[tuple[str, int], ...]], str] = {}

    def _elapsed_ms(self) -> float:
        return (perf_counter() - self.started) * 1000

    def _delay_ms(self, call: ToolCall) -> int:
        # Hashing call identity makes jitter stable regardless of task scheduling order.
        digest = hashlib.sha256(f"{self.seed}:{call.call_id}".encode()).digest()[0]
        jitter = digest % 13
        base = 300 if call.tool_name in {"read_tests", "search_docs", "read_architecture"} else 40
        return base + jitter

    def _cache_key(self, call: ToolCall) -> tuple[str, str, tuple[tuple[str, int], ...]]:
        arguments = json.dumps(call.arguments, sort_keys=True, separators=(",", ":"))
        versions = tuple((resource, self.versions[resource]) for resource in call.read_resources)
        return call.tool_name, arguments, versions

    def _event(self, call: ToolCall, event_type: str, started: float, cache_status: str, detail: str = "") -> None:
        ended = self._elapsed_ms()
        self.emit(SpanEvent(
            run_id=self.run_id,
            call_id=call.call_id,
            tool_name=call.tool_name,
            event_type=event_type,  # type: ignore[arg-type]
            started_at_ms=started,
            ended_at_ms=ended,
            duration_ms=ended - started,
            read_resources=call.read_resources,
            written_resources=call.written_resources,
            cache_status=cache_status,
            deadline_path=self.deadline_path,
            remaining_budget_ms=None,
            detail=detail,
        ))

    async def execute(self, call: ToolCall) -> ToolResult:
        started = self._elapsed_ms()
        key = self._cache_key(call) if call.read_resources else None
        if key is not None and key in self.cache:
            output = self.cache[key]
            self._event(call, "cache_hit", started, "hit")
            self._event(call, "tool", started, "hit")
            return ToolResult(call.call_id, output, "hit")

        if key is not None:
            self._event(call, "cache_miss", started, "miss")
        await asyncio.sleep(self._delay_ms(call) / 1000)

        if call.tool_name == "edit_auth":
            self.resources["file:src/auth.py"] = (
                "def authenticate(token: str) -> bool:\n"
                "    return token.strip() == \"demo-token\"\n"
            )
            resource = "file:src/auth.py"
            self.versions[resource] += 1
            self._event(call, "cache_invalidate", started, "invalidated", f"{resource} version {self.versions[resource]}")
            output = "updated src/auth.py to normalize token whitespace"
        elif call.tool_name == "run_tests":
            output = "2 passed" if ".strip()" in self.resources["file:src/auth.py"] else "1 failed"
        else:
            resource = call.read_resources[0] if call.read_resources else ""
            output = self.resources.get(resource, "no result")

        if key is not None:
            self.cache[key] = output
        self._event(call, "tool", started, "miss" if key is not None else "not_applicable")
        return ToolResult(call.call_id, output, "miss" if key is not None else "not_applicable")
