"""Deterministic synthetic coding tools registered with the real runtime."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping

from runtime.contracts import ResourceKey, ToolCall, ToolResult
from runtime.registry import ToolRegistry


class SyntheticTools:
    """In-memory coding tools with seeded, controlled I/O latency."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.resources = {
            "file:src/auth.py": (
                "def authenticate(token: str) -> bool:\n"
                '    return token == "demo-token"\n'
            ),
            "file:tests/test_auth.py": (
                "test valid and invalid authentication tokens\n"
            ),
            "doc:README.md": (
                "strip surrounding whitespace before validating tokens\n"
            ),
            "doc:architecture.md": (
                "authentication accepts one normalized bearer token\n"
            ),
        }

    async def read_auth(
        self,
        path: str,
        options: Mapping[str, object] | None = None,
    ) -> str:
        del path, options
        await self._delay("read_auth")
        return self.resources["file:src/auth.py"]

    async def read_tests(self, path: str) -> str:
        del path
        await self._delay("read_tests")
        return self.resources["file:tests/test_auth.py"]

    async def search_docs(self, query: str) -> str:
        del query
        await self._delay("search_docs")
        return self.resources["doc:README.md"]

    async def read_architecture(self, path: str) -> str:
        del path
        await self._delay("read_architecture")
        return self.resources["doc:architecture.md"]

    async def edit_auth(self, path: str) -> str:
        del path
        await self._delay("edit_auth")
        self.resources["file:src/auth.py"] = (
            "def authenticate(token: str) -> bool:\n"
            '    return token.strip() == "demo-token"\n'
        )
        return "updated src/auth.py to normalize token whitespace"

    async def run_tests(self, target: str) -> str:
        del target
        await self._delay("run_tests")
        if ".strip()" in self.resources["file:src/auth.py"]:
            return "2 passed"
        return "1 failed"

    async def _delay(self, tool_name: str) -> None:
        digest = hashlib.sha256(
            f"{self.seed}:{tool_name}".encode()
        ).digest()[0]
        jitter_ms = digest % 13
        base_ms = (
            300
            if tool_name
            in {"read_tests", "search_docs", "read_architecture"}
            else 40
        )
        await asyncio.sleep((base_ms + jitter_ms) / 1_000)


def build_registry(tools: SyntheticTools) -> ToolRegistry:
    """Register synthetic handlers and conservative scheduling metadata."""

    auth = ResourceKey("file:src/auth.py")
    registry = ToolRegistry()
    registry.register(
        "read_auth",
        tools.read_auth,
        read_resources=[auth],
    )
    registry.register(
        "read_tests",
        tools.read_tests,
        read_resources=[ResourceKey("file:tests/test_auth.py")],
    )
    registry.register(
        "search_docs",
        tools.search_docs,
        read_resources=[ResourceKey("doc:README.md")],
        deadline_paths=["full"],
    )
    registry.register(
        "read_architecture",
        tools.read_architecture,
        read_resources=[ResourceKey("doc:architecture.md")],
        deadline_paths=["full", "fast"],
    )
    registry.register(
        "edit_auth",
        tools.edit_auth,
        written_resources=[auth],
    )
    registry.register(
        "run_tests",
        tools.run_tests,
        read_resources=[auth],
    )
    return registry


__all__ = [
    "SyntheticTools",
    "ToolCall",
    "ToolResult",
    "build_registry",
]
