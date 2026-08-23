from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

import codex_runtime.operations as operations
from codex_runtime.manifest import parse_manifest
from codex_runtime.operations import (
    MAX_TEXT_FILE_BYTES,
    OperationError,
    execute_manifest,
)
from runtime.contracts import DeadlineState


def _read_manifest(paths: list[str]):
    return parse_manifest(
        {
            "schema_version": 1,
            "calls": [
                {
                    "id": f"read-{index}",
                    "operation": "read_file",
                    "arguments": {"path": path},
                }
                for index, path in enumerate(paths)
            ],
        }
    )


def test_three_independent_reads_overlap_and_preserve_order(
    tmp_path,
    monkeypatch,
) -> None:
    for name in ("one.txt", "two.txt", "three.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    real_read = operations._read_utf8_text

    def delayed_read(path: Path) -> str:
        time.sleep(0.05)
        return real_read(path)

    monkeypatch.setattr(operations, "_read_utf8_text", delayed_read)
    manifest = _read_manifest(["one.txt", "two.txt", "three.txt"])

    optimized = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=1_000,
            mode="optimized",
            repository_root=tmp_path,
        )
    )
    serial = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=1_000,
            mode="serial",
            repository_root=tmp_path,
        )
    )

    expected = ["one.txt", "two.txt", "three.txt"]
    assert [output.output for output in optimized.tool_outputs] == expected
    assert [output.output for output in serial.tool_outputs] == expected
    assert max(span.started_at_ms for span in optimized.spans) < min(
        span.ended_at_ms for span in optimized.spans
    )
    assert optimized.elapsed_ms < serial.elapsed_ms * 0.6


def test_read_file_rejects_symlink_escape(tmp_path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    manifest = _read_manifest(["linked.txt"])

    with pytest.raises(OperationError, match="escapes root"):
        asyncio.run(
            execute_manifest(
                manifest,
                deadline_ms=1_000,
                repository_root=tmp_path,
            )
        )


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"binary\x00data", "binary file is not supported"),
        (b"\xff\xfe", "file is not valid UTF-8"),
        (b"x" * (MAX_TEXT_FILE_BYTES + 1), "file exceeds"),
    ],
)
def test_read_file_rejects_unsupported_input(
    tmp_path,
    contents: bytes,
    message: str,
) -> None:
    (tmp_path / "input.dat").write_bytes(contents)
    manifest = _read_manifest(["input.dat"])

    result = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=1_000,
            repository_root=tmp_path,
        )
    )

    assert result.tool_outputs[0].status == "error"
    assert message in str(result.tool_outputs[0].error)


def test_tight_deadline_cancels_pytest_and_returns_structured_timeout(
    tmp_path,
) -> None:
    test_file = tmp_path / "tests" / "test_slow.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "import time\n\ndef test_slow():\n    time.sleep(5)\n",
        encoding="utf-8",
    )
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "calls": [
                {
                    "id": "slow-tests",
                    "operation": "pytest",
                    "arguments": {"target": "tests", "options": ["-q"]},
                }
            ],
        }
    )
    started = time.perf_counter()

    result = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=100,
            repository_root=tmp_path,
        )
    )
    wall_elapsed_ms = (time.perf_counter() - started) * 1_000

    assert result.deadline_status is DeadlineState.TIMED_OUT
    assert result.tool_outputs[0].status == "timed_out"
    assert result.spans[0].event_type == "deadline_timeout"
    assert wall_elapsed_ms < 1_000


def test_cancelled_subprocess_cannot_finish_later(tmp_path) -> None:
    marker = tmp_path / "late.txt"
    script = (
        "import pathlib,time; "
        "time.sleep(0.5); "
        f"pathlib.Path({str(marker)!r}).write_text('late')"
    )

    async def cancel_process() -> None:
        task = asyncio.create_task(
            operations._run_subprocess(
                (sys.executable, "-c", script),
                cwd=tmp_path,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_process())
    time.sleep(0.6)

    assert not marker.exists()
