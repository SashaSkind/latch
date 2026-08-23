from __future__ import annotations

import asyncio

import pytest

from codex_runtime.cli import execution_result_to_dict
from codex_runtime.manifest import parse_manifest
from codex_runtime.operations import OperationError, execute_manifest
from runtime.contracts import DeadlineState, SpanEvent


def test_read_file_runs_through_serial_runtime_and_serializes_result(
    tmp_path,
) -> None:
    source = tmp_path / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("answer = 42\n", encoding="utf-8")
    emitted: list[SpanEvent] = []
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "calls": [
                {
                    "id": "read-example",
                    "operation": "read_file",
                    "arguments": {"path": "src/example.py"},
                }
            ],
        }
    )

    result = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=1_000,
            mode="serial",
            event_callback=emitted.append,
            repository_root=tmp_path,
        )
    )
    document = execution_result_to_dict(result)

    assert result.deadline_status is DeadlineState.MET
    assert result.tool_outputs[0].call_id == "read-example"
    assert result.tool_outputs[0].tool_name == "read_file"
    assert result.tool_outputs[0].output == "answer = 42\n"
    assert result.spans[0].tool_name == "read_file"
    assert result.spans[0].read_resources == ("file:src/example.py",)
    assert emitted == list(result.spans)
    assert document["schema_version"] == 1
    assert document["deadline_status"] == "met"
    assert document["tool_outputs"] == [
        {
            "call_id": "read-example",
            "tool_name": "read_file",
            "output": "answer = 42\n",
            "status": "ok",
            "error": None,
        }
    ]
    assert isinstance(document["spans"], list)


def test_equivalent_serial_reads_reuse_runtime_cache(tmp_path) -> None:
    source = tmp_path / "example.txt"
    source.write_text("same contents", encoding="utf-8")
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "calls": [
                {
                    "id": "first",
                    "operation": "read_file",
                    "arguments": {"path": "example.txt"},
                },
                {
                    "id": "second",
                    "operation": "read_file",
                    "arguments": {"path": "./example.txt"},
                },
            ],
        }
    )

    result = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=1_000,
            mode="serial",
            repository_root=tmp_path,
        )
    )

    assert [output.output for output in result.tool_outputs] == [
        "same contents",
        "same contents",
    ]
    assert [span.cache_status for span in result.spans] == ["miss", "hit"]


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/outside.txt"])
def test_read_file_rejects_paths_outside_repository(tmp_path, path: str) -> None:
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "calls": [
                {
                    "id": "escape",
                    "operation": "read_file",
                    "arguments": {"path": path},
                }
            ],
        }
    )

    with pytest.raises(OperationError, match="repository path"):
        asyncio.run(
            execute_manifest(
                manifest,
                deadline_ms=1_000,
                repository_root=tmp_path,
            )
        )


def test_read_file_rejects_unknown_arguments(tmp_path) -> None:
    source = tmp_path / "example.txt"
    source.write_text("contents", encoding="utf-8")
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "calls": [
                {
                    "id": "unsafe",
                    "operation": "read_file",
                    "arguments": {
                        "path": "example.txt",
                        "command": "echo not allowed",
                    },
                }
            ],
        }
    )

    with pytest.raises(
        OperationError,
        match="unknown operation arguments: command",
    ):
        asyncio.run(
            execute_manifest(
                manifest,
                deadline_ms=1_000,
                repository_root=tmp_path,
            )
        )
