"""Privacy-preserving telemetry for observed Codex lifecycle hooks."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class CodexObservation:
    """One lifecycle observation, deliberately excluding tool payloads."""

    schema_version: int
    session_id: str
    turn_id: str
    tool_use_id: str
    phase: str
    tool_name: str
    observed_at_ms: float
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def observation_from_hook_payload(payload: Mapping[str, object]) -> CodexObservation | None:
    """Convert supported lifecycle payloads without retaining their contents."""

    phase = payload.get("hook_event_name")
    if phase not in {"PreToolUse", "PostToolUse"}:
        return None

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return None

    def string_field(name: str, fallback: str) -> str:
        value = payload.get(name)
        return value if isinstance(value, str) and value else fallback

    return CodexObservation(
        schema_version=1,
        session_id=string_field("session_id", "unknown"),
        turn_id=string_field("turn_id", "unknown"),
        tool_use_id=string_field("tool_use_id", string_field("tool_call_id", "unknown")),
        phase=phase,
        tool_name=tool_name,
        observed_at_ms=time.time_ns() / 1_000_000,
        status="observed",
    )


def append_observation(destination: Path, observation: CodexObservation) -> None:
    """Append one compact JSON record for the dashboard to read later."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(observation.to_dict(), separators=(",", ":")) + "\n")


def load_observations(source: Path) -> list[CodexObservation]:
    """Ignore malformed lines so telemetry can never take down the dashboard."""

    if not source.exists():
        return []

    observations: list[CodexObservation] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            observations.append(CodexObservation(**record))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return observations


def clear_observations(destination: Path) -> None:
    if destination.exists():
        destination.unlink()
