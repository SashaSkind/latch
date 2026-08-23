"""Registry for async tools and their declared resource access."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from runtime.cache import normalize_resource_key
from runtime.contracts import ResourceKey


ToolHandler: TypeAlias = Callable[..., Awaitable[object]]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """An async tool together with its conservative resource declaration."""

    name: str
    handler: ToolHandler
    read_resources: frozenset[ResourceKey]
    written_resources: frozenset[ResourceKey]


class ToolRegistry:
    """Name-based registry of tools available to the runtime."""

    def __init__(self, *, fixture_root: str | Path = ".") -> None:
        self._fixture_root = Path(fixture_root).resolve(strict=False)
        self._tools: dict[str, RegisteredTool] = {}

    @property
    def fixture_root(self) -> Path:
        return self._fixture_root

    def register(
        self,
        name: str,
        handler: ToolHandler,
        *,
        read_resources: Iterable[ResourceKey] = (),
        written_resources: Iterable[ResourceKey] = (),
    ) -> RegisteredTool:
        """Register one uniquely named tool and its declared resources."""

        if not name:
            raise ValueError("tool name must not be empty")
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")

        tool = RegisteredTool(
            name=name,
            handler=handler,
            read_resources=self._normalize_resources(read_resources),
            written_resources=self._normalize_resources(written_resources),
        )
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> RegisteredTool:
        """Return a registered tool or raise a descriptive ``KeyError``."""

        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool: {name}") from None

    def _normalize_resources(
        self,
        resources: Iterable[ResourceKey],
    ) -> frozenset[ResourceKey]:
        return frozenset(
            normalize_resource_key(
                resource,
                fixture_root=self._fixture_root,
            )
            for resource in resources
        )
