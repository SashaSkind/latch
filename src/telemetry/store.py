"""Small thread-safe-enough in-process trace store for a single demo server."""

from __future__ import annotations

from collections import defaultdict

from .events import SpanEvent


class TraceStore:
    def __init__(self) -> None:
        self._events: dict[str, list[SpanEvent]] = defaultdict(list)

    def record(self, event: SpanEvent) -> None:
        self._events[event.run_id].append(event)

    def events_for(self, run_id: str) -> list[SpanEvent]:
        return list(self._events.get(run_id, ()))

    def all_runs(self) -> dict[str, list[SpanEvent]]:
        return {run_id: list(events) for run_id, events in self._events.items()}

    def clear(self) -> None:
        self._events.clear()
