"""Canonical, resource-versioned cache primitives."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from runtime.contracts import ResourceKey


@dataclass(frozen=True, slots=True)
class CacheLookup:
    """A cache lookup that can distinguish a miss from a cached ``None``."""

    hit: bool
    value: object | None = None


@dataclass(frozen=True, slots=True)
class _CacheKey:
    tool_name: str
    arguments_json: str
    resource_versions: tuple[tuple[ResourceKey, int], ...]


class ResourceVersionCache:
    """Store tool outputs under canonical arguments and resource versions."""

    def __init__(self, *, fixture_root: str | Path = ".") -> None:
        self._fixture_root = Path(fixture_root).resolve(strict=False)
        self._versions: dict[ResourceKey, int] = {}
        self._entries: dict[_CacheKey, object | None] = {}

    @property
    def fixture_root(self) -> Path:
        return self._fixture_root

    def lookup(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        read_resources: Iterable[ResourceKey],
    ) -> CacheLookup:
        """Look up an output using the resources' current versions."""

        key = self._key(tool_name, arguments, read_resources)
        if key not in self._entries:
            return CacheLookup(hit=False)
        return CacheLookup(hit=True, value=self._entries[key])

    def store(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        read_resources: Iterable[ResourceKey],
        value: object | None,
    ) -> None:
        """Store an output using the resources' current versions."""

        key = self._key(tool_name, arguments, read_resources)
        self._entries[key] = value

    def advance_versions(
        self,
        written_resources: Iterable[ResourceKey],
    ) -> None:
        """Advance versions after a successful write."""

        for resource in self._normalize_resources(written_resources):
            self._versions[resource] = self._versions.get(resource, 0) + 1

    def version(self, resource: ResourceKey) -> int:
        """Return the current normalized version for one resource."""

        normalized = normalize_resource_key(
            resource,
            fixture_root=self._fixture_root,
        )
        return self._versions.get(normalized, 0)

    def _key(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        read_resources: Iterable[ResourceKey],
    ) -> _CacheKey:
        resources = self._normalize_resources(read_resources)
        resource_versions = tuple(
            (resource, self._versions.get(resource, 0))
            for resource in resources
        )
        return _CacheKey(
            tool_name=tool_name,
            arguments_json=canonical_arguments(arguments),
            resource_versions=resource_versions,
        )

    def _normalize_resources(
        self,
        resources: Iterable[ResourceKey],
    ) -> tuple[ResourceKey, ...]:
        return tuple(
            sorted(
                {
                    normalize_resource_key(
                        resource,
                        fixture_root=self._fixture_root,
                    )
                    for resource in resources
                }
            )
        )


def canonical_arguments(arguments: Mapping[str, object]) -> str:
    """Serialize JSON-compatible arguments deterministically."""

    return json.dumps(
        dict(arguments),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def normalize_resource_key(
    resource: ResourceKey,
    *,
    fixture_root: str | Path,
) -> ResourceKey:
    """Normalize ``file:`` resources to fixture-root-relative POSIX paths."""

    resource_text = str(resource)
    if not resource_text.startswith("file:"):
        return ResourceKey(resource_text)

    raw_path = resource_text.removeprefix("file:")
    if not raw_path:
        raise ValueError("file resource path must not be empty")

    root = Path(fixture_root).resolve(strict=False)
    path = Path(raw_path)
    resolved = (
        path.resolve(strict=False)
        if path.is_absolute()
        else (root / path).resolve(strict=False)
    )
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"file resource is outside fixture root: {resource_text}"
        ) from None
    return ResourceKey(f"file:{relative.as_posix()}")
