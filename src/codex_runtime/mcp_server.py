"""Minimal STDIO MCP adapter for the deadline-aware runtime."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO, cast

from codex_runtime.cli import execution_result_to_dict
from codex_runtime.manifest import ManifestError, parse_manifest
from codex_runtime.operations import OperationError, execute_manifest
from runtime.contracts import ExecutionMode


JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "latch-runtime"
SERVER_VERSION = "0.1.0"
TOOL_NAME = "latch_execute_batch"
MAX_CALLS = 32
MAX_DEADLINE_MS = 60_000
MAX_REQUEST_BYTES = 1_048_576

_DEADLINE_PATH_SCHEMA: dict[str, object] = {
    "type": "array",
    "items": {"type": "string", "enum": ["full", "fast", "partial"]},
    "minItems": 1,
    "uniqueItems": True,
}


def _call_schema(
    operation: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "operation": {"const": operation},
            "arguments": dict(arguments),
            "deadline_paths": _DEADLINE_PATH_SCHEMA,
        },
        "required": ["id", "operation", "arguments"],
        "additionalProperties": False,
    }


TOOL_DEFINITION: dict[str, object] = {
    "name": TOOL_NAME,
    "title": "Execute an optimized repository batch",
    "description": (
        "Run two or more bounded repository operations through Latch's "
        "deadline-aware scheduler. Use only when all calls are known up front "
        "and independent. Arbitrary shell commands are not accepted."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "schema_version": {"const": 1},
            "calls": {
                "type": "array",
                "minItems": 2,
                "maxItems": MAX_CALLS,
                "items": {
                    "oneOf": [
                        _call_schema(
                            "read_file",
                            {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string", "minLength": 1}
                                },
                                "required": ["path"],
                                "additionalProperties": False,
                            },
                        ),
                        _call_schema(
                            "search",
                            {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "minLength": 1},
                                    "path": {"type": "string", "minLength": 1},
                                },
                                "required": ["query"],
                                "additionalProperties": False,
                            },
                        ),
                        _call_schema(
                            "git_status",
                            {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        ),
                        _call_schema(
                            "pytest",
                            {
                                "type": "object",
                                "properties": {
                                    "target": {"type": "string", "minLength": 1},
                                    "options": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "uniqueItems": True,
                                    },
                                },
                                "required": ["target"],
                                "additionalProperties": False,
                            },
                        ),
                    ]
                },
            },
            "deadline_ms": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_DEADLINE_MS,
            },
            "mode": {
                "type": "string",
                "enum": ["optimized", "serial"],
                "default": "optimized",
            },
        },
        "required": ["schema_version", "calls", "deadline_ms"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}

SERVER_INSTRUCTIONS = (
    "Use latch_execute_batch only when at least two bounded repository "
    "operations are independent and known before execution. Prefer optimized "
    "mode. Supported operations are read_file, search, git_status, and focused "
    "pytest. Never pass shell commands or use the tool for edits."
)


class ToolArgumentError(ValueError):
    """A user-correctable MCP tool argument error."""


async def handle_message(
    message: object,
    *,
    repository_root: str | Path | None = None,
) -> dict[str, object] | None:
    """Handle one decoded MCP JSON-RPC message."""

    if not isinstance(message, dict):
        return _error_response(None, -32600, "Invalid Request")

    request_id = message.get("id")
    has_request_id = "id" in message
    if message.get("jsonrpc") != JSONRPC_VERSION:
        return _error_response(
            request_id if has_request_id else None,
            -32600,
            "Invalid Request",
        )

    method = message.get("method")
    if not isinstance(method, str):
        return _error_response(
            request_id if has_request_id else None,
            -32600,
            "Invalid Request",
        )

    if not has_request_id:
        return None

    if method == "initialize":
        params = message.get("params", {})
        requested_protocol = (
            params.get("protocolVersion")
            if isinstance(params, dict)
            else None
        )
        protocol_version = (
            requested_protocol
            if isinstance(requested_protocol, str) and requested_protocol
            else DEFAULT_PROTOCOL_VERSION
        )
        return _result_response(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "title": "Latch Runtime",
                    "version": SERVER_VERSION,
                },
                "instructions": SERVER_INSTRUCTIONS,
            },
        )

    if method == "ping":
        return _result_response(request_id, {})

    if method == "tools/list":
        return _result_response(request_id, {"tools": [TOOL_DEFINITION]})

    if method == "resources/list":
        return _result_response(request_id, {"resources": []})

    if method == "resources/templates/list":
        return _result_response(request_id, {"resourceTemplates": []})

    if method == "tools/call":
        return await _handle_tool_call(
            request_id,
            message.get("params"),
            repository_root=repository_root,
        )

    return _error_response(request_id, -32601, "Method not found")


async def _handle_tool_call(
    request_id: object,
    params: object,
    *,
    repository_root: str | Path | None,
) -> dict[str, object]:
    if not isinstance(params, dict):
        return _error_response(request_id, -32602, "Invalid params")
    if params.get("name") != TOOL_NAME:
        return _error_response(request_id, -32602, "Unknown tool")

    try:
        manifest_document, deadline_ms, mode = _parse_tool_arguments(
            params.get("arguments")
        )
        manifest = parse_manifest(manifest_document)
        result = await execute_manifest(
            manifest,
            deadline_ms=deadline_ms,
            mode=mode,
            repository_root=repository_root,
        )
        document = cast(
            dict[str, object],
            json.loads(_compact_json(execution_result_to_dict(result))),
        )
        return _result_response(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": _compact_json(document),
                    }
                ],
                "structuredContent": document,
                "isError": False,
            },
        )
    except (ToolArgumentError, ManifestError, OperationError) as error:
        return _result_response(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": f"latch: {error}",
                    }
                ],
                "isError": True,
            },
        )
    except Exception as error:
        return _result_response(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "latch: internal failure: "
                            f"{type(error).__name__}"
                        ),
                    }
                ],
                "isError": True,
            },
        )


def _parse_tool_arguments(
    raw_arguments: object,
) -> tuple[dict[str, object], int, ExecutionMode]:
    if not isinstance(raw_arguments, dict):
        raise ToolArgumentError("arguments must be an object")

    allowed = {"schema_version", "calls", "deadline_ms", "mode"}
    unknown = set(raw_arguments) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ToolArgumentError(f"unknown arguments: {names}")

    required = {"schema_version", "calls", "deadline_ms"}
    missing = required - set(raw_arguments)
    if missing:
        names = ", ".join(sorted(missing))
        raise ToolArgumentError(f"missing arguments: {names}")

    raw_calls = raw_arguments["calls"]
    if not isinstance(raw_calls, list):
        raise ToolArgumentError("calls must be an array")
    if len(raw_calls) < 2:
        raise ToolArgumentError("calls must contain at least two operations")
    if len(raw_calls) > MAX_CALLS:
        raise ToolArgumentError(f"calls cannot exceed {MAX_CALLS} operations")

    deadline_ms = raw_arguments["deadline_ms"]
    if (
        type(deadline_ms) is not int
        or deadline_ms <= 0
        or deadline_ms > MAX_DEADLINE_MS
    ):
        raise ToolArgumentError(
            f"deadline_ms must be between 1 and {MAX_DEADLINE_MS}"
        )

    raw_mode = raw_arguments.get("mode", "optimized")
    if raw_mode not in {"optimized", "serial"}:
        raise ToolArgumentError("mode must be optimized or serial")

    manifest_document = {
        "schema_version": raw_arguments["schema_version"],
        "calls": raw_calls,
    }
    return (
        manifest_document,
        deadline_ms,
        cast(ExecutionMode, raw_mode),
    )


async def serve(
    stdin: TextIO,
    stdout: TextIO,
    *,
    repository_root: str | Path | None = None,
) -> None:
    """Serve newline-delimited MCP messages until stdin closes."""

    for raw_line in stdin:
        if not raw_line.strip():
            continue
        if len(raw_line.encode("utf-8")) > MAX_REQUEST_BYTES:
            _write_message(
                stdout,
                _error_response(None, -32700, "Parse error"),
            )
            continue
        try:
            message = json.loads(
                raw_line,
                parse_constant=_reject_non_json_number,
            )
        except (json.JSONDecodeError, ValueError, RecursionError):
            _write_message(
                stdout,
                _error_response(None, -32700, "Parse error"),
            )
            continue

        response = await handle_message(
            message,
            repository_root=repository_root,
        )
        if response is not None:
            _write_message(stdout, response)


def main() -> int:
    """Run the STDIO server."""

    try:
        asyncio.run(serve(sys.stdin, sys.stdout))
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(
            f"latch-mcp: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    return 0


def _result_response(
    request_id: object,
    result: Mapping[str, object],
) -> dict[str, object]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "result": dict(result),
    }


def _error_response(
    request_id: object,
    code: int,
    message: str,
) -> dict[str, object]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _write_message(stdout: TextIO, message: Mapping[str, object]) -> None:
    stdout.write(_compact_json(message))
    stdout.write("\n")
    stdout.flush()


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _reject_non_json_number(value: str) -> object:
    raise ValueError(f"invalid JSON number: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
