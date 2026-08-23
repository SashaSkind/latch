"""Command-line serialization helpers for ``latch batch``."""

from __future__ import annotations

from dataclasses import asdict

from runtime.contracts import ExecutionResult


def execution_result_to_dict(result: ExecutionResult) -> dict[str, object]:
    """Serialize the locked version-1 CLI result contract."""

    return {
        "schema_version": 1,
        "tool_outputs": [asdict(output) for output in result.tool_outputs],
        "spans": [span.to_dict() for span in result.spans],
        "deadline_path": result.deadline_path,
        "elapsed_ms": result.elapsed_ms,
        "deadline_status": result.deadline_status.value,
    }
