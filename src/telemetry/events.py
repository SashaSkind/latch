"""Stable event contract shared between the runtime and dashboard."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Literal

EventType = Literal["tool", "cache_hit", "cache_miss", "cache_invalidate", "deadline_decision"]
Status = Literal["ok", "error", "skipped"]


@dataclass(frozen=True, slots=True)
class SpanEvent:
    run_id: str
    call_id: str
    tool_name: str
    event_type: EventType
    started_at_ms: float
    ended_at_ms: float
    duration_ms: float
    read_resources: tuple[str, ...] = ()
    written_resources: tuple[str, ...] = ()
    cache_status: str = "not_applicable"
    deadline_path: str = "full"
    remaining_budget_ms: float | None = None
    status: Status = "ok"
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


EventCallback = Callable[[SpanEvent], None]
