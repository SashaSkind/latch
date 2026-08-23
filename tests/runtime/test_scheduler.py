from runtime.contracts import ResourceKey, ToolCall
from runtime.registry import ToolRegistry
from runtime.scheduler import build_execution_waves


async def _unused_tool() -> None:
    raise AssertionError("wave construction must not execute tools")


def test_independent_reads_share_the_first_wave() -> None:
    registry = ToolRegistry()
    registry.register(
        "read_auth",
        _unused_tool,
        read_resources=[ResourceKey("file:src/auth.py")],
    )
    registry.register(
        "read_tests",
        _unused_tool,
        read_resources=[ResourceKey("file:tests/test_auth.py")],
    )
    registry.register(
        "search_docs",
        _unused_tool,
        read_resources=[ResourceKey("docs:local")],
    )
    calls = [
        ToolCall("read-auth", "read_auth", {}),
        ToolCall("read-tests", "read_tests", {}),
        ToolCall("search-docs", "search_docs", {}),
    ]

    waves = build_execution_waves(calls, registry)

    assert waves == (tuple(calls),)


def test_conflicting_calls_are_ordered_across_waves() -> None:
    auth_resource = ResourceKey("file:src/auth.py")
    registry = ToolRegistry()
    registry.register(
        "read_auth",
        _unused_tool,
        read_resources=[auth_resource],
    )
    registry.register(
        "write_auth",
        _unused_tool,
        written_resources=[auth_resource],
    )
    calls = [
        ToolCall("read-before", "read_auth", {}),
        ToolCall("write-first", "write_auth", {}),
        ToolCall("write-second", "write_auth", {}),
        ToolCall("read-after", "read_auth", {}),
    ]

    waves = build_execution_waves(calls, registry)

    assert waves == tuple((call,) for call in calls)


def test_independent_later_call_uses_its_earliest_safe_wave() -> None:
    auth_resource = ResourceKey("file:src/auth.py")
    registry = ToolRegistry()
    registry.register(
        "write_auth",
        _unused_tool,
        written_resources=[auth_resource],
    )
    registry.register(
        "read_auth",
        _unused_tool,
        read_resources=[auth_resource],
    )
    registry.register(
        "read_docs",
        _unused_tool,
        read_resources=[ResourceKey("docs:local")],
    )
    write_auth = ToolCall("write-auth", "write_auth", {})
    read_auth = ToolCall("read-auth", "read_auth", {})
    read_docs = ToolCall("read-docs", "read_docs", {})

    waves = build_execution_waves(
        [write_auth, read_auth, read_docs],
        registry,
    )

    assert waves == ((write_auth, read_docs), (read_auth,))
