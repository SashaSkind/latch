from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from telemetry.codex import load_observations

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "codex_hooks" / "record.py"


def test_hook_records_only_the_locked_observation_schema(tmp_path: Path) -> None:
    destination = tmp_path / "observations.jsonl"
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "tool_use_id": "call-1",
        "tool_name": "Bash",
        "tool_input": {"command": "echo secret-value"},
    }
    completed = subprocess.run(
        [sys.executable, str(HOOK), "--output", str(destination)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout == ""
    observations = load_observations(destination)
    assert len(observations) == 1
    assert observations[0].tool_name == "Bash"
    assert "secret-value" not in destination.read_text()
    assert set(observations[0].to_dict()) == {
        "schema_version",
        "session_id",
        "turn_id",
        "tool_use_id",
        "phase",
        "tool_name",
        "observed_at_ms",
        "status",
    }


def test_hook_ignores_unrelated_events(tmp_path: Path) -> None:
    destination = tmp_path / "observations.jsonl"
    completed = subprocess.run(
        [sys.executable, str(HOOK), "--output", str(destination)],
        input=json.dumps({"hook_event_name": "Stop", "tool_name": "Bash"}),
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout == ""
    assert load_observations(destination) == []


def test_codex_fixture_uses_the_locked_batch_and_observation_schemas() -> None:
    batch = json.loads((ROOT / "fixtures" / "codex" / "batch-result-v1.json").read_text())
    observations = json.loads((ROOT / "fixtures" / "codex" / "observations-v1.json").read_text())

    assert batch["schema_version"] == 1
    assert {"tool_outputs", "spans", "deadline_path", "elapsed_ms", "deadline_status"} <= set(batch)
    assert all(set(item) == {
        "schema_version", "session_id", "turn_id", "tool_use_id", "phase",
        "tool_name", "observed_at_ms", "status",
    } for item in observations)
