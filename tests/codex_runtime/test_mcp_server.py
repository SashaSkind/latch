from __future__ import annotations

import asyncio
import io
import json

from codex_runtime.mcp_server import (
    MAX_CALLS,
    TOOL_NAME,
    handle_message,
    serve,
)


def _request(
    method: str,
    *,
    request_id: int = 1,
    params: object | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def test_initialize_negotiates_protocol_and_advertises_tools() -> None:
    response = asyncio.run(
        handle_message(
            _request(
                "initialize",
                params={
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
        )
    )

    assert response is not None
    result = response["result"]
    assert isinstance(result, dict)
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert "at least two" in str(result["instructions"])


def test_tools_list_exposes_one_bounded_batch_tool() -> None:
    response = asyncio.run(handle_message(_request("tools/list")))

    assert response is not None
    result = response["result"]
    assert isinstance(result, dict)
    tools = result["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, dict)
    assert tool["name"] == TOOL_NAME
    schema = tool["inputSchema"]
    assert isinstance(schema, dict)
    calls = schema["properties"]["calls"]
    assert calls["minItems"] == 2
    assert calls["maxItems"] == MAX_CALLS
    assert tool["annotations"]["readOnlyHint"] is False


def test_tool_call_executes_validated_batch_and_returns_locked_result(
    tmp_path,
) -> None:
    (tmp_path / "one.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("two\n", encoding="utf-8")
    response = asyncio.run(
        handle_message(
            _request(
                "tools/call",
                params={
                    "name": TOOL_NAME,
                    "arguments": {
                        "schema_version": 1,
                        "calls": [
                            {
                                "id": "one",
                                "operation": "read_file",
                                "arguments": {"path": "one.txt"},
                            },
                            {
                                "id": "two",
                                "operation": "read_file",
                                "arguments": {"path": "two.txt"},
                            },
                        ],
                        "deadline_ms": 1_000,
                        "mode": "optimized",
                    },
                },
            ),
            repository_root=tmp_path,
        )
    )

    assert response is not None
    result = response["result"]
    assert isinstance(result, dict)
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["schema_version"] == 1
    assert structured["deadline_status"] == "met"
    assert [item["output"] for item in structured["tool_outputs"]] == [
        "one\n",
        "two\n",
    ]
    assert len(structured["spans"]) == 2
    content = result["content"]
    assert json.loads(content[0]["text"]) == structured


def test_tool_call_rejects_single_operation_without_running_it(tmp_path) -> None:
    (tmp_path / "one.txt").write_text("one\n", encoding="utf-8")
    response = asyncio.run(
        handle_message(
            _request(
                "tools/call",
                params={
                    "name": TOOL_NAME,
                    "arguments": {
                        "schema_version": 1,
                        "calls": [
                            {
                                "id": "one",
                                "operation": "read_file",
                                "arguments": {"path": "one.txt"},
                            }
                        ],
                        "deadline_ms": 1_000,
                    },
                },
            ),
            repository_root=tmp_path,
        )
    )

    assert response is not None
    result = response["result"]
    assert isinstance(result, dict)
    assert result["isError"] is True
    assert "at least two" in result["content"][0]["text"]


def test_stdio_server_recovers_from_parse_error_and_ignores_notifications() -> None:
    stdin = io.StringIO(
        "not json\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )
        + "\n"
        + json.dumps(_request("ping", request_id=7))
        + "\n"
    )
    stdout = io.StringIO()

    asyncio.run(serve(stdin, stdout))

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        },
        {"jsonrpc": "2.0", "id": 7, "result": {}},
    ]
