from __future__ import annotations

import io
import json

from codex_runtime.cli import (
    EXIT_EXECUTION_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    main,
)


def _write_manifest(tmp_path, calls: list[dict[str, object]]):
    manifest_path = tmp_path / "plan.json"
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "calls": calls}),
        encoding="utf-8",
    )
    return manifest_path


def test_cli_emits_one_json_document_for_success(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / ".git").mkdir()
    source = tmp_path / "example.txt"
    source.write_text("hello from latch\n", encoding="utf-8")
    manifest_path = _write_manifest(
        tmp_path,
        [
            {
                "id": "read",
                "operation": "read_file",
                "arguments": {"path": "example.txt"},
            }
        ],
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "batch",
            str(manifest_path),
            "--deadline-ms",
            "1000",
            "--mode",
            "serial",
            "--json",
        ],
        stdout=stdout,
        stderr=stderr,
    )
    document = json.loads(stdout.getvalue())

    assert exit_code == EXIT_OK
    assert stderr.getvalue() == ""
    assert stdout.getvalue().count("\n") == 1
    assert document["schema_version"] == 1
    assert document["deadline_status"] == "met"
    assert document["tool_outputs"][0]["output"] == "hello from latch\n"
    assert document["spans"][0]["tool_name"] == "read_file"


def test_cli_returns_usage_code_without_stdout_for_invalid_manifest(
    tmp_path,
) -> None:
    manifest_path = tmp_path / "invalid.json"
    manifest_path.write_text(
        '{"schema_version": 2, "calls": []}',
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "batch",
            str(manifest_path),
            "--deadline-ms",
            "1000",
            "--json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == EXIT_USAGE
    assert stdout.getvalue() == ""
    assert "schema_version" in stderr.getvalue()


def test_cli_returns_usage_code_for_invalid_arguments() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "batch",
            "plan.json",
            "--deadline-ms",
            "0",
            "--json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == EXIT_USAGE
    assert stdout.getvalue() == ""
    assert "greater than zero" in stderr.getvalue()


def test_cli_emits_structured_result_and_code_two_for_tool_failure(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / ".git").mkdir()
    test_file = tmp_path / "tests" / "test_failure.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "def test_failure():\n    assert False\n",
        encoding="utf-8",
    )
    manifest_path = _write_manifest(
        tmp_path,
        [
            {
                "id": "tests",
                "operation": "pytest",
                "arguments": {"target": "tests", "options": ["-q"]},
            }
        ],
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "batch",
            str(manifest_path),
            "--deadline-ms",
            "5000",
            "--json",
        ],
        stdout=stdout,
        stderr=stderr,
    )
    document = json.loads(stdout.getvalue())

    assert exit_code == EXIT_EXECUTION_FAILED
    assert stderr.getvalue() == ""
    assert document["deadline_status"] == "met"
    assert document["tool_outputs"][0]["status"] == "error"
    assert "pytest failed" in document["tool_outputs"][0]["error"]
    assert "exited with 1" in document["tool_outputs"][0]["error"]
