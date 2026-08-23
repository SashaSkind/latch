"""Safe operation adapters backed by ``Runtime.execute_batch``."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from codex_runtime.manifest import BatchManifest, ManifestCall
from runtime.cache import canonical_arguments
from runtime.contracts import (
    EventCallback,
    ExecutionMode,
    ExecutionResult,
    ResourceKey,
    SpanEvent,
    ToolCall,
)
from runtime.registry import ToolHandler, ToolRegistry
from runtime.runtime import Runtime


MAX_TEXT_FILE_BYTES = 1_048_576
MAX_SUBPROCESS_OUTPUT_BYTES = 1_048_576
_PYTEST_OPTIONS = frozenset(
    {
        "-q",
        "--quiet",
        "-x",
        "--exitfirst",
        "--disable-warnings",
        "--strict-config",
        "--strict-markers",
    }
)


class OperationError(ValueError):
    """A user-correctable operation or repository-boundary error."""


@dataclass(frozen=True, slots=True)
class _PreparedOperation:
    handler: ToolHandler
    arguments: Mapping[str, object]
    read_resources: tuple[ResourceKey, ...] = ()
    written_resources: tuple[ResourceKey, ...] = ()
    cacheable: bool | None = None


async def execute_manifest(
    manifest: BatchManifest,
    *,
    deadline_ms: int,
    mode: ExecutionMode = "optimized",
    event_callback: EventCallback | None = None,
    repository_root: str | Path | None = None,
) -> ExecutionResult:
    """Execute one validated manifest through the existing runtime."""

    root = (
        _validate_repository_root(repository_root)
        if repository_root is not None
        else discover_repository_root()
    )
    registry = ToolRegistry(fixture_root=root)
    runtime_calls: list[ToolCall] = []
    public_names: dict[str, str] = {}

    for manifest_call in manifest.calls:
        prepared = _prepare_operation(manifest_call, root)
        internal_name = _internal_tool_name(
            manifest_call,
            prepared.arguments,
        )
        public_names[internal_name] = manifest_call.operation

        try:
            registry.get(internal_name)
        except KeyError:
            registry.register(
                internal_name,
                prepared.handler,
                read_resources=prepared.read_resources,
                written_resources=prepared.written_resources,
                deadline_paths=manifest_call.deadline_paths,
                cacheable=prepared.cacheable,
            )

        runtime_calls.append(
            ToolCall(
                call_id=manifest_call.call_id,
                tool_name=internal_name,
                arguments=prepared.arguments,
            )
        )

    callback = _public_callback(event_callback, public_names)
    result = await Runtime(registry).execute_batch(
        runtime_calls,
        deadline_ms=deadline_ms,
        mode=mode,
        event_callback=callback,
    )
    return _public_result(result, public_names)


def discover_repository_root(start: str | Path | None = None) -> Path:
    """Find the nearest parent containing Git metadata."""

    current = Path.cwd() if start is None else Path(start)
    try:
        current = current.resolve(strict=True)
    except OSError as error:
        raise OperationError(f"cannot resolve repository start: {error}") from None
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise OperationError(f"no repository root found from: {current}")


def _validate_repository_root(repository_root: str | Path) -> Path:
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as error:
        raise OperationError(f"cannot resolve repository root: {error}") from None
    if not root.is_dir():
        raise OperationError(f"repository root is not a directory: {root}")
    return root


def _prepare_operation(
    call: ManifestCall,
    repository_root: Path,
) -> _PreparedOperation:
    if call.operation == "read_file":
        return _prepare_read_file(call.arguments, repository_root)
    if call.operation == "search":
        return _prepare_search(call.arguments, repository_root)
    if call.operation == "git_status":
        return _prepare_git_status(call.arguments, repository_root)
    if call.operation == "pytest":
        return _prepare_pytest(call.arguments, repository_root)
    raise OperationError(f"unsupported operation: {call.operation}")


def _prepare_read_file(
    arguments: Mapping[str, object],
    repository_root: Path,
) -> _PreparedOperation:
    _require_argument_fields(arguments, required={"path"})
    raw_path = arguments["path"]
    if not isinstance(raw_path, str) or not raw_path:
        raise OperationError("read_file.path must be a non-empty string")

    path = _resolve_repository_path(
        raw_path,
        repository_root=repository_root,
        must_exist=True,
    )
    if not path.is_file():
        raise OperationError(f"read_file path is not a file: {raw_path}")
    relative_path = path.relative_to(repository_root).as_posix()

    async def read_file(path: str) -> str:
        del path
        return await asyncio.to_thread(_read_utf8_text, path_on_disk)

    path_on_disk = path
    return _PreparedOperation(
        handler=read_file,
        arguments={"path": relative_path},
        read_resources=(ResourceKey(f"file:{relative_path}"),),
        cacheable=True,
    )


def _prepare_search(
    arguments: Mapping[str, object],
    repository_root: Path,
) -> _PreparedOperation:
    _require_argument_fields(
        arguments,
        required={"query"},
        optional={"path"},
    )
    query = arguments["query"]
    if not isinstance(query, str) or not query:
        raise OperationError("search.query must be a non-empty string")
    raw_path = arguments.get("path", ".")
    if not isinstance(raw_path, str) or not raw_path:
        raise OperationError("search.path must be a non-empty string")

    path = _resolve_repository_path(
        raw_path,
        repository_root=repository_root,
        must_exist=True,
    )
    relative_path = _relative_repository_path(path, repository_root)

    async def search(query: str, path: str) -> str:
        del query, path
        return await _run_subprocess(
            (
                "rg",
                "--no-heading",
                "--line-number",
                "--color",
                "never",
                "--",
                normalized_query,
                normalized_path,
            ),
            cwd=repository_root,
            success_codes=frozenset({0, 1}),
        )

    normalized_query = query
    normalized_path = relative_path
    return _PreparedOperation(
        handler=search,
        arguments={"query": query, "path": relative_path},
        read_resources=(ResourceKey(f"scope:{relative_path}"),),
        cacheable=False,
    )


def _prepare_git_status(
    arguments: Mapping[str, object],
    repository_root: Path,
) -> _PreparedOperation:
    _require_argument_fields(arguments, required=set())
    if not (repository_root / ".git").exists():
        raise OperationError(
            f"git_status requires a Git repository: {repository_root}"
        )

    async def git_status() -> str:
        return await _run_subprocess(
            (
                "git",
                "-c",
                "color.status=false",
                "status",
                "--short",
                "--branch",
            ),
            cwd=repository_root,
        )

    return _PreparedOperation(
        handler=git_status,
        arguments={},
        read_resources=(ResourceKey("repo:."), ResourceKey("git:.git")),
        cacheable=False,
    )


def _prepare_pytest(
    arguments: Mapping[str, object],
    repository_root: Path,
) -> _PreparedOperation:
    _require_argument_fields(
        arguments,
        required={"target"},
        optional={"options"},
    )
    raw_target = arguments["target"]
    if not isinstance(raw_target, str) or not raw_target:
        raise OperationError("pytest.target must be a non-empty string")
    target = _resolve_repository_path(
        raw_target,
        repository_root=repository_root,
        must_exist=True,
    )
    relative_target = _relative_repository_path(target, repository_root)
    options = _validate_pytest_options(arguments.get("options", []))

    async def pytest(target: str, options: list[str]) -> str:
        del target, options
        try:
            return await _run_subprocess(
                (
                    sys.executable,
                    "-m",
                    "pytest",
                    *normalized_options,
                    "--",
                    normalized_target,
                ),
                cwd=repository_root,
            )
        except OperationError as error:
            raise OperationError(f"pytest failed: {error}") from None

    normalized_target = relative_target
    normalized_options = options
    return _PreparedOperation(
        handler=pytest,
        arguments={
            "target": relative_target,
            "options": list(options),
        },
        read_resources=(ResourceKey("repo:."),),
        written_resources=(
            ResourceKey("process:pytest"),
            ResourceKey("file:.pytest_cache"),
        ),
        cacheable=False,
    )


def _validate_pytest_options(raw_options: object) -> tuple[str, ...]:
    if not isinstance(raw_options, list) or not all(
        isinstance(option, str) for option in raw_options
    ):
        raise OperationError("pytest.options must be an array of strings")
    unsupported = [
        option for option in raw_options if option not in _PYTEST_OPTIONS
    ]
    if unsupported:
        names = ", ".join(unsupported)
        raise OperationError(f"unsupported pytest options: {names}")
    return tuple(raw_options)


async def _run_subprocess(
    command: tuple[str, ...],
    *,
    cwd: Path,
    success_codes: frozenset[int] = frozenset({0}),
) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise OperationError(
            f"cannot start {command[0]}: {error}"
        ) from None

    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise

    if len(stdout) + len(stderr) > MAX_SUBPROCESS_OUTPUT_BYTES:
        raise OperationError(
            f"{command[0]} output exceeds "
            f"{MAX_SUBPROCESS_OUTPUT_BYTES} byte limit"
        )
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode not in success_codes:
        detail = stderr_text or stdout_text.strip() or "no diagnostic output"
        raise OperationError(
            f"{command[0]} exited with {process.returncode}: {detail}"
        )
    return stdout_text


async def _terminate_process(
    process: asyncio.subprocess.Process,
) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        process.kill()
        await process.wait()


def _read_utf8_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise OperationError(f"cannot stat file {path}: {error}") from None
    if size > MAX_TEXT_FILE_BYTES:
        raise OperationError(
            f"file exceeds {MAX_TEXT_FILE_BYTES} byte limit: {path}"
        )

    try:
        contents = path.read_bytes()
    except OSError as error:
        raise OperationError(f"cannot read file {path}: {error}") from None
    if b"\x00" in contents:
        raise OperationError(f"binary file is not supported: {path}")
    try:
        return contents.decode("utf-8")
    except UnicodeDecodeError:
        raise OperationError(f"file is not valid UTF-8: {path}") from None


def _resolve_repository_path(
    raw_path: str,
    *,
    repository_root: Path,
    must_exist: bool,
) -> Path:
    if "\x00" in raw_path:
        raise OperationError("repository path contains a null byte")
    requested = Path(raw_path)
    if requested.is_absolute():
        raise OperationError(f"repository path must be relative: {raw_path}")

    try:
        resolved = (repository_root / requested).resolve(
            strict=must_exist
        )
    except OSError as error:
        raise OperationError(f"cannot resolve repository path {raw_path}: {error}") from None
    try:
        resolved.relative_to(repository_root)
    except ValueError:
        raise OperationError(
            f"repository path escapes root: {raw_path}"
        ) from None
    return resolved


def _relative_repository_path(path: Path, repository_root: Path) -> str:
    relative = path.relative_to(repository_root)
    return relative.as_posix() or "."


def _require_argument_fields(
    arguments: Mapping[str, object],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    missing = required - set(arguments)
    if missing:
        names = ", ".join(sorted(missing))
        raise OperationError(f"missing operation arguments: {names}")
    unknown = set(arguments) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise OperationError(f"unknown operation arguments: {names}")


def _internal_tool_name(
    call: ManifestCall,
    arguments: Mapping[str, object],
) -> str:
    policy = ",".join(call.deadline_paths)
    identity = (
        f"{call.operation}\0{canonical_arguments(arguments)}\0{policy}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{call.operation}@{digest}"


def _public_callback(
    callback: EventCallback | None,
    public_names: Mapping[str, str],
) -> EventCallback | None:
    if callback is None:
        return None

    async def emit_public(event: SpanEvent) -> None:
        callback_result = callback(_public_span(event, public_names))
        if inspect.isawaitable(callback_result):
            await callback_result

    return emit_public


def _public_result(
    result: ExecutionResult,
    public_names: Mapping[str, str],
) -> ExecutionResult:
    return replace(
        result,
        tool_outputs=tuple(
            replace(
                output,
                tool_name=public_names[output.tool_name],
            )
            for output in result.tool_outputs
        ),
        spans=tuple(
            _public_span(span, public_names) for span in result.spans
        ),
    )


def _public_span(
    span: SpanEvent,
    public_names: Mapping[str, str],
) -> SpanEvent:
    return replace(span, tool_name=public_names[span.tool_name])
