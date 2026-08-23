"""Conflict-aware scheduling for ordered tool-call batches."""

from __future__ import annotations

from collections.abc import Sequence

from runtime.contracts import ToolCall
from runtime.registry import RegisteredTool, ToolRegistry


ExecutionWave = tuple[ToolCall, ...]


def build_execution_waves(
    calls: Sequence[ToolCall],
    registry: ToolRegistry,
) -> tuple[ExecutionWave, ...]:
    """Place each call in its earliest safe ordered execution wave.

    Read/read access never conflicts. Any shared resource with at least one
    writer creates an ordering edge from the earlier call to the later call.
    """

    waves: list[list[ToolCall]] = []
    scheduled: list[tuple[int, RegisteredTool]] = []

    for call in calls:
        tool = registry.get(call.tool_name)
        wave_index = 0

        for prior_wave_index, prior_tool in scheduled:
            if _conflicts(prior_tool, tool):
                wave_index = max(wave_index, prior_wave_index + 1)

        if wave_index == len(waves):
            waves.append([])
        waves[wave_index].append(call)
        scheduled.append((wave_index, tool))

    return tuple(tuple(wave) for wave in waves)


def _conflicts(left: RegisteredTool, right: RegisteredTool) -> bool:
    return bool(
        left.written_resources
        & (right.read_resources | right.written_resources)
        or right.written_resources & left.read_resources
    )
