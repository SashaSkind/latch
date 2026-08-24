from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

import codex_runtime.operations as operations
from codex_runtime.manifest import parse_manifest
from codex_runtime.operations import OperationError, execute_manifest


def _manifest(*calls: dict[str, object]):
    return parse_manifest(
        {
            "schema_version": 1,
            "calls": list(calls),
        }
    )


def test_search_uses_fixed_rg_arguments_and_is_not_cached(
    tmp_path,
    monkeypatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_subprocess(
        command: tuple[str, ...],
        *,
        cwd: Path,
        success_codes=frozenset({0}),
    ) -> str:
        del success_codes
        assert cwd == tmp_path
        commands.append(command)
        return "src/example.py:1:execute_batch\n"

    monkeypatch.setattr(operations, "_run_subprocess", fake_subprocess)
    (tmp_path / "src").mkdir()
    manifest = _manifest(
        {
            "id": "first",
            "operation": "search",
            "arguments": {"query": "execute_batch", "path": "src"},
        },
        {
            "id": "second",
            "operation": "search",
            "arguments": {"path": "./src", "query": "execute_batch"},
        },
    )

    result = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=1_000,
            mode="serial",
            repository_root=tmp_path,
        )
    )

    assert commands == [
        (
            "rg",
            "--no-heading",
            "--line-number",
            "--color",
            "never",
            "--",
            "execute_batch",
            "src",
        ),
        (
            "rg",
            "--no-heading",
            "--line-number",
            "--color",
            "never",
            "--",
            "execute_batch",
            "src",
        ),
    ]
    assert [span.cache_status for span in result.spans] == [
        "not_cacheable",
        "not_cacheable",
    ]
    assert all(span.read_resources == ("scope:src",) for span in result.spans)


def test_search_rejects_shell_shaped_extra_argument(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    manifest = _manifest(
        {
            "id": "unsafe",
            "operation": "search",
            "arguments": {
                "query": "needle",
                "path": "src",
                "command": "rm -rf ignored",
            },
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


def test_git_status_reports_repository_state_and_is_not_cached(tmp_path) -> None:
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    manifest = _manifest(
        {
            "id": "status",
            "operation": "git_status",
            "arguments": {},
        }
    )

    result = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=1_000,
            repository_root=tmp_path,
        )
    )

    output = result.tool_outputs[0]
    assert output.status == "ok"
    assert "?? new.txt" in str(output.output)
    assert result.spans[0].cache_status == "not_cacheable"
    assert set(result.spans[0].read_resources) == {"git:.git", "repo:."}


def test_pytest_runs_validated_target(tmp_path) -> None:
    test_file = tmp_path / "tests" / "test_sample.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_sample():\n    assert 2 + 2 == 4\n", encoding="utf-8")
    manifest = _manifest(
        {
            "id": "tests",
            "operation": "pytest",
            "arguments": {"target": "tests", "options": ["-q"]},
        }
    )

    result = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=5_000,
            repository_root=tmp_path,
        )
    )

    assert result.tool_outputs[0].status == "ok"
    assert "1 passed" in str(result.tool_outputs[0].output)
    assert set(result.spans[0].written_resources) == {
        "file:.pytest_cache",
        "process:pytest",
    }


def test_pytest_rejects_non_allowlisted_options(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    manifest = _manifest(
        {
            "id": "unsafe-tests",
            "operation": "pytest",
            "arguments": {
                "target": "tests",
                "options": ["--basetemp=/tmp/escape"],
            },
        }
    )

    with pytest.raises(
        OperationError,
        match="unsupported pytest options",
    ):
        asyncio.run(
            execute_manifest(
                manifest,
                deadline_ms=1_000,
                repository_root=tmp_path,
            )
        )


def test_pytest_operations_never_overlap(tmp_path, monkeypatch) -> None:
    active = 0
    maximum_active = 0

    async def fake_subprocess(
        command: tuple[str, ...],
        *,
        cwd: Path,
        success_codes=frozenset({0}),
    ) -> str:
        nonlocal active, maximum_active
        del command, cwd, success_codes
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "1 passed\n"

    monkeypatch.setattr(operations, "_run_subprocess", fake_subprocess)
    (tmp_path / "tests").mkdir()
    manifest = _manifest(
        {
            "id": "tests-first",
            "operation": "pytest",
            "arguments": {"target": "tests"},
        },
        {
            "id": "tests-second",
            "operation": "pytest",
            "arguments": {"target": "./tests"},
        },
    )

    result = asyncio.run(
        execute_manifest(
            manifest,
            deadline_ms=1_000,
            mode="optimized",
            repository_root=tmp_path,
        )
    )

    assert maximum_active == 1
    assert [output.status for output in result.tool_outputs] == ["ok", "ok"]
    assert result.spans[0].ended_at_ms <= result.spans[1].started_at_ms
