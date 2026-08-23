from __future__ import annotations

import json
import re

import pytest

from codex_runtime.manifest import (
    ManifestError,
    load_manifest,
    parse_manifest,
)


def test_parse_valid_version_one_manifest_preserves_call_order() -> None:
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "calls": [
                {
                    "id": "read-runtime",
                    "operation": "read_file",
                    "arguments": {"path": "src/runtime/runtime.py"},
                    "deadline_paths": ["full", "fast"],
                },
                {
                    "id": "runtime-tests",
                    "operation": "pytest",
                    "arguments": {"target": "tests/runtime"},
                },
            ],
        }
    )

    assert manifest.schema_version == 1
    assert [call.call_id for call in manifest.calls] == [
        "read-runtime",
        "runtime-tests",
    ]
    assert manifest.calls[0].operation == "read_file"
    assert manifest.calls[0].deadline_paths == ("full", "fast")
    assert manifest.calls[1].deadline_paths == (
        "full",
        "fast",
        "partial",
    )


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            {"schema_version": 2, "calls": []},
            "schema_version must be 1",
        ),
        (
            {"schema_version": 1, "calls": [], "extra": True},
            "unknown fields in manifest: extra",
        ),
        (
            {
                "schema_version": 1,
                "calls": [
                    {
                        "id": "call",
                        "operation": "shell",
                        "arguments": {"command": "echo unsafe"},
                    }
                ],
            },
            "operation must be one of",
        ),
        (
            {
                "schema_version": 1,
                "calls": [
                    {
                        "id": "call",
                        "operation": "read_file",
                        "arguments": {"path": "README.md"},
                        "read_resources": ["file:README.md"],
                    }
                ],
            },
            "unknown fields in calls[0]: read_resources",
        ),
        (
            {
                "schema_version": 1,
                "calls": [
                    {
                        "id": "call",
                        "operation": "read_file",
                        "arguments": {},
                        "deadline_paths": ["full", "full"],
                    }
                ],
            },
            "deadline_paths contains duplicate: full",
        ),
    ],
)
def test_parse_rejects_contract_violations(
    document: object,
    message: str,
) -> None:
    with pytest.raises(ManifestError, match=re.escape(message)):
        parse_manifest(document)


def test_parse_rejects_duplicate_call_ids() -> None:
    call = {
        "id": "duplicate",
        "operation": "git_status",
        "arguments": {},
    }

    with pytest.raises(ManifestError, match="duplicate call id: duplicate"):
        parse_manifest(
            {
                "schema_version": 1,
                "calls": [call, dict(call)],
            }
        )


def test_load_manifest_reports_invalid_json(tmp_path) -> None:
    manifest_path = tmp_path / "plan.json"
    manifest_path.write_text('{"schema_version": NaN}', encoding="utf-8")

    with pytest.raises(ManifestError, match="invalid JSON"):
        load_manifest(manifest_path)


def test_load_manifest_reads_utf8_json(tmp_path) -> None:
    manifest_path = tmp_path / "plan.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calls": [
                    {
                        "id": "unicode",
                        "operation": "search",
                        "arguments": {"query": "café", "path": "src"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.calls[0].arguments["query"] == "café"
