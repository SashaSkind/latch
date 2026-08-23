from pathlib import Path

import pytest

from runtime.cache import ResourceVersionCache, normalize_resource_key
from runtime.contracts import ResourceKey
from runtime.registry import ToolRegistry


def test_equivalent_arguments_and_resource_paths_share_entry(
    tmp_path: Path,
) -> None:
    cache = ResourceVersionCache(fixture_root=tmp_path)
    relative_resource = ResourceKey("file:src/auth.py")
    absolute_resource = ResourceKey(f"file:{tmp_path}/src/./auth.py")
    first_arguments = {
        "path": "src/auth.py",
        "options": {"encoding": "utf-8", "line_numbers": True},
    }
    equivalent_arguments = {
        "options": {"line_numbers": True, "encoding": "utf-8"},
        "path": "src/auth.py",
    }

    cache.store(
        "read_file",
        first_arguments,
        [relative_resource],
        None,
    )
    lookup = cache.lookup(
        "read_file",
        equivalent_arguments,
        [absolute_resource],
    )

    assert lookup.hit is True
    assert lookup.value is None


def test_resource_version_advance_makes_prior_entry_unreachable(
    tmp_path: Path,
) -> None:
    cache = ResourceVersionCache(fixture_root=tmp_path)
    auth_resource = ResourceKey("file:src/auth.py")
    arguments = {"path": "src/auth.py"}
    cache.store("read_file", arguments, [auth_resource], "old contents")

    cache.advance_versions([auth_resource])

    assert cache.version(auth_resource) == 1
    assert cache.lookup("read_file", arguments, [auth_resource]).hit is False

    cache.store("read_file", arguments, [auth_resource], "new contents")
    fresh_lookup = cache.lookup("read_file", arguments, [auth_resource])
    assert fresh_lookup.hit is True
    assert fresh_lookup.value == "new contents"


def test_registry_uses_fixture_root_resource_normalization(
    tmp_path: Path,
) -> None:
    async def unused_tool() -> None:
        return None

    registry = ToolRegistry(fixture_root=tmp_path)
    registered = registry.register(
        "read_auth",
        unused_tool,
        read_resources=[
            ResourceKey(f"file:{tmp_path}/src/../src/auth.py"),
        ],
    )

    assert registered.read_resources == frozenset(
        {ResourceKey("file:src/auth.py")}
    )


def test_file_resource_cannot_escape_fixture_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside fixture root"):
        normalize_resource_key(
            ResourceKey("file:../outside.py"),
            fixture_root=tmp_path,
        )
