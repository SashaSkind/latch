"""Strict version-1 manifest parsing for ``latch batch``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias, cast

from runtime.contracts import DeadlinePath


OperationName: TypeAlias = Literal[
    "read_file",
    "search",
    "git_status",
    "pytest",
]

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1_048_576
SUPPORTED_OPERATIONS: frozenset[str] = frozenset(
    {"read_file", "search", "git_status", "pytest"}
)
_ALL_DEADLINE_PATHS: tuple[DeadlinePath, ...] = (
    "full",
    "fast",
    "partial",
)
_TOP_LEVEL_FIELDS = frozenset({"schema_version", "calls"})
_CALL_FIELDS = frozenset(
    {"id", "operation", "arguments", "deadline_paths"}
)


class ManifestError(ValueError):
    """A user-correctable version-1 manifest error."""


@dataclass(frozen=True, slots=True)
class ManifestCall:
    """One validated operation requested by a batch manifest."""

    call_id: str
    operation: OperationName
    arguments: Mapping[str, object]
    deadline_paths: tuple[DeadlinePath, ...]


@dataclass(frozen=True, slots=True)
class BatchManifest:
    """A validated, ordered version-1 batch manifest."""

    schema_version: Literal[1]
    calls: tuple[ManifestCall, ...]


def load_manifest(path: str | Path) -> BatchManifest:
    """Load and validate a UTF-8 JSON manifest from disk."""

    manifest_path = Path(path)
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise ManifestError(
            f"cannot read manifest {manifest_path}: {error}"
        ) from None
    if len(raw_bytes) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"manifest exceeds {MAX_MANIFEST_BYTES} byte limit: "
            f"{manifest_path}"
        )
    try:
        raw_document = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError(f"manifest is not valid UTF-8: {manifest_path}") from None

    try:
        document = json.loads(
            raw_document,
            parse_constant=_reject_non_json_number,
            object_pairs_hook=_reject_duplicate_fields,
        )
    except (json.JSONDecodeError, ManifestError, RecursionError) as error:
        raise ManifestError(f"invalid JSON in {manifest_path}: {error}") from None

    return parse_manifest(document)


def parse_manifest(document: object) -> BatchManifest:
    """Validate an already-decoded version-1 manifest document."""

    root = _require_object(document, "manifest")
    _reject_unknown_fields(root, _TOP_LEVEL_FIELDS, "manifest")
    _require_fields(root, _TOP_LEVEL_FIELDS, "manifest")

    schema_version = root["schema_version"]
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise ManifestError(
            f"schema_version must be {SCHEMA_VERSION}"
        )

    raw_calls = root["calls"]
    if not isinstance(raw_calls, list):
        raise ManifestError("calls must be an array")

    calls: list[ManifestCall] = []
    call_ids: set[str] = set()
    for index, raw_call in enumerate(raw_calls):
        call = _parse_call(raw_call, index)
        if call.call_id in call_ids:
            raise ManifestError(f"duplicate call id: {call.call_id}")
        call_ids.add(call.call_id)
        calls.append(call)

    return BatchManifest(schema_version=1, calls=tuple(calls))


def _parse_call(raw_call: object, index: int) -> ManifestCall:
    location = f"calls[{index}]"
    call = _require_object(raw_call, location)
    _reject_unknown_fields(call, _CALL_FIELDS, location)
    _require_fields(call, {"id", "operation", "arguments"}, location)

    call_id = call["id"]
    if not isinstance(call_id, str) or not call_id.strip():
        raise ManifestError(f"{location}.id must be a non-empty string")

    operation = call["operation"]
    if not isinstance(operation, str) or operation not in SUPPORTED_OPERATIONS:
        supported = ", ".join(sorted(SUPPORTED_OPERATIONS))
        raise ManifestError(
            f"{location}.operation must be one of: {supported}"
        )

    arguments = _require_object(call["arguments"], f"{location}.arguments")
    _validate_json_value(arguments, f"{location}.arguments")

    deadline_paths = _parse_deadline_paths(
        call.get("deadline_paths", list(_ALL_DEADLINE_PATHS)),
        location,
    )
    return ManifestCall(
        call_id=call_id,
        operation=cast(OperationName, operation),
        arguments=dict(arguments),
        deadline_paths=deadline_paths,
    )


def _parse_deadline_paths(
    raw_paths: object,
    location: str,
) -> tuple[DeadlinePath, ...]:
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ManifestError(
            f"{location}.deadline_paths must be a non-empty array"
        )

    paths: list[DeadlinePath] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        if raw_path not in _ALL_DEADLINE_PATHS:
            allowed = ", ".join(_ALL_DEADLINE_PATHS)
            raise ManifestError(
                f"{location}.deadline_paths values must be one of: {allowed}"
            )
        if raw_path in seen:
            raise ManifestError(
                f"{location}.deadline_paths contains duplicate: {raw_path}"
            )
        seen.add(cast(str, raw_path))
        paths.append(cast(DeadlinePath, raw_path))
    return tuple(paths)


def _require_object(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ManifestError(f"{location} must be an object")
    return cast(dict[str, object], value)


def _reject_unknown_fields(
    value: Mapping[str, object],
    allowed: frozenset[str] | set[str],
    location: str,
) -> None:
    unknown = set(value) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ManifestError(f"unknown fields in {location}: {names}")


def _require_fields(
    value: Mapping[str, object],
    required: frozenset[str] | set[str],
    location: str,
) -> None:
    missing = required - set(value)
    if missing:
        names = ", ".join(sorted(missing))
        raise ManifestError(f"missing fields in {location}: {names}")


def _validate_json_value(value: object, location: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ManifestError(f"{location} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{location}[{index}]")
        return
    if isinstance(value, dict) and all(
        isinstance(key, str) for key in value
    ):
        for key, item in value.items():
            _validate_json_value(item, f"{location}.{key}")
        return
    raise ManifestError(f"{location} contains a non-JSON value")


def _reject_non_json_number(value: str) -> object:
    raise ManifestError(f"non-JSON number: {value}")


def _reject_duplicate_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ManifestError(f"duplicate JSON field: {key}")
        value[key] = item
    return value
