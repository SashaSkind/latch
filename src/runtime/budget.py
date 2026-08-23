"""Deterministic deadline-path selection policy."""

from __future__ import annotations

from dataclasses import dataclass

from runtime.contracts import DeadlinePath


@dataclass(frozen=True, slots=True)
class DeadlinePolicy:
    """Select a static execution path from a request's initial budget.

    Thresholds are constructor parameters so the demo can calibrate them from
    measured seeded trials instead of embedding workload-specific timing in
    the runtime.
    """

    full_path_minimum_ms: int = 900
    fast_path_minimum_ms: int = 500

    def __post_init__(self) -> None:
        if self.fast_path_minimum_ms <= 0:
            raise ValueError("fast path threshold must be greater than zero")
        if self.full_path_minimum_ms <= self.fast_path_minimum_ms:
            raise ValueError(
                "full path threshold must exceed fast path threshold"
            )

    def select_path(self, deadline_ms: int) -> DeadlinePath:
        """Return ``full``, ``fast``, or ``partial`` deterministically."""

        if deadline_ms <= 0:
            raise ValueError("deadline_ms must be greater than zero")
        if deadline_ms >= self.full_path_minimum_ms:
            return "full"
        if deadline_ms >= self.fast_path_minimum_ms:
            return "fast"
        return "partial"
