from __future__ import annotations

import asyncio

import pytest

from runtime.contracts import ResourceKey, ToolCall
from runtime.registry import ToolRegistry
from runtime.runtime import Runtime


def test_read_tool_can_explicitly_disable_caching() -> None:
    executions = 0

    async def changing_read() -> int:
        nonlocal executions
        executions += 1
        return executions

    registry = ToolRegistry()
    registry.register(
        "changing_read",
        changing_read,
        read_resources=[ResourceKey("state:changing")],
        cacheable=False,
    )
    result = asyncio.run(
        Runtime(registry).execute_batch(
            [
                ToolCall("first", "changing_read", {}),
                ToolCall("second", "changing_read", {}),
            ],
            deadline_ms=1_000,
            mode="serial",
        )
    )

    assert [output.output for output in result.tool_outputs] == [1, 2]
    assert [span.cache_status for span in result.spans] == [
        "not_cacheable",
        "not_cacheable",
    ]


@pytest.mark.parametrize(
    ("read_resources", "written_resources", "message"),
    [
        ([], [], "cacheable tool must declare read resources"),
        (
            [ResourceKey("state:read")],
            [ResourceKey("state:write")],
            "tool with write resources cannot be cacheable",
        ),
    ],
)
def test_registry_rejects_unsafe_cache_policy(
    read_resources,
    written_resources,
    message: str,
) -> None:
    async def handler() -> None:
        return None

    with pytest.raises(ValueError, match=message):
        ToolRegistry().register(
            "invalid",
            handler,
            read_resources=read_resources,
            written_resources=written_resources,
            cacheable=True,
        )
