import asyncio
from pathlib import Path

from runtime.cache import ResourceVersionCache
from runtime.contracts import ResourceKey, ToolCall
from runtime.registry import ToolRegistry
from runtime.runtime import Runtime


def test_successful_write_invalidates_cached_read(tmp_path: Path) -> None:
    auth_resource = ResourceKey("file:src/auth.py")
    state = {"auth": "old contents"}
    read_count = 0

    async def read_auth(path: str) -> str:
        nonlocal read_count
        read_count += 1
        return state["auth"]

    async def write_auth(contents: str) -> str:
        state["auth"] = contents
        return contents

    registry = ToolRegistry(fixture_root=tmp_path)
    registry.register(
        "read_auth",
        read_auth,
        read_resources=[auth_resource],
    )
    registry.register(
        "write_auth",
        write_auth,
        written_resources=[auth_resource],
    )
    cache = ResourceVersionCache(fixture_root=tmp_path)
    runtime = Runtime(registry, cache=cache)
    read_call = ToolCall(
        "warm-read",
        "read_auth",
        {"path": "src/auth.py"},
    )

    cold_result = asyncio.run(
        runtime.execute_batch([read_call], deadline_ms=1_000)
    )
    warm_result = asyncio.run(
        runtime.execute_batch(
            [
                ToolCall(
                    "cached-read",
                    "read_auth",
                    {"path": "src/auth.py"},
                )
            ],
            deadline_ms=1_000,
        )
    )
    edited_result = asyncio.run(
        runtime.execute_batch(
            [
                ToolCall(
                    "edit-auth",
                    "write_auth",
                    {"contents": "new contents"},
                ),
                ToolCall(
                    "fresh-read",
                    "read_auth",
                    {"path": "src/auth.py"},
                ),
            ],
            deadline_ms=1_000,
        )
    )
    fresh_cached_result = asyncio.run(
        runtime.execute_batch(
            [
                ToolCall(
                    "fresh-cached-read",
                    "read_auth",
                    {"path": "src/auth.py"},
                )
            ],
            deadline_ms=1_000,
        )
    )

    assert cold_result.tool_outputs[0].output == "old contents"
    assert cold_result.spans[0].cache_status == "miss"
    assert warm_result.tool_outputs[0].output == "old contents"
    assert warm_result.spans[0].cache_status == "hit"
    assert [output.output for output in edited_result.tool_outputs] == [
        "new contents",
        "new contents",
    ]
    assert [span.cache_status for span in edited_result.spans] == [
        "invalidated",
        "miss",
    ]
    assert fresh_cached_result.tool_outputs[0].output == "new contents"
    assert fresh_cached_result.spans[0].cache_status == "hit"
    assert cache.version(auth_resource) == 1
    assert read_count == 2


def test_failed_write_does_not_invalidate_cached_read(tmp_path: Path) -> None:
    auth_resource = ResourceKey("file:src/auth.py")
    read_count = 0

    async def read_auth(path: str) -> str:
        nonlocal read_count
        read_count += 1
        return "valid contents"

    async def fail_write(contents: str) -> None:
        raise OSError("synthetic edit failure")

    registry = ToolRegistry(fixture_root=tmp_path)
    registry.register(
        "read_auth",
        read_auth,
        read_resources=[auth_resource],
    )
    registry.register(
        "fail_write",
        fail_write,
        written_resources=[auth_resource],
    )
    cache = ResourceVersionCache(fixture_root=tmp_path)
    runtime = Runtime(registry, cache=cache)
    asyncio.run(
        runtime.execute_batch(
            [
                ToolCall(
                    "warm-read",
                    "read_auth",
                    {"path": "src/auth.py"},
                )
            ],
            deadline_ms=1_000,
        )
    )

    result = asyncio.run(
        runtime.execute_batch(
            [
                ToolCall(
                    "failed-edit",
                    "fail_write",
                    {"contents": "bad contents"},
                ),
                ToolCall(
                    "read-after-failure",
                    "read_auth",
                    {"path": "src/auth.py"},
                ),
            ],
            deadline_ms=1_000,
        )
    )

    assert result.tool_outputs[0].status == "error"
    assert result.tool_outputs[1].output == "valid contents"
    assert [span.cache_status for span in result.spans] == [
        "not_invalidated",
        "hit",
    ]
    assert cache.version(auth_resource) == 0
    assert read_count == 1
