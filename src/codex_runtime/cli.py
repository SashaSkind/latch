"""Agent-friendly ``latch batch`` command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from typing import TextIO

from codex_runtime.manifest import ManifestError, load_manifest
from codex_runtime.operations import OperationError, execute_manifest
from runtime.contracts import DeadlineState, ExecutionResult


EXIT_OK = 0
EXIT_EXECUTION_FAILED = 2
EXIT_USAGE = 64
EXIT_INTERNAL = 70


class _UsageError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def execution_result_to_dict(result: ExecutionResult) -> dict[str, object]:
    """Serialize the locked version-1 CLI result contract."""

    return {
        "schema_version": 1,
        "tool_outputs": [asdict(output) for output in result.tool_outputs],
        "spans": [span.to_dict() for span in result.spans],
        "deadline_path": result.deadline_path,
        "elapsed_ms": result.elapsed_ms,
        "deadline_status": result.deadline_status.value,
    }


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return its locked process exit code."""

    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    parser = _build_parser()
    try:
        arguments = parser.parse_args(argv)
        if arguments.deadline_ms <= 0:
            raise _UsageError("--deadline-ms must be greater than zero")
        manifest = load_manifest(arguments.plan_path)
        result = asyncio.run(
            execute_manifest(
                manifest,
                deadline_ms=arguments.deadline_ms,
                mode=arguments.mode,
            )
        )
        document = execution_result_to_dict(result)
        json.dump(
            document,
            output_stream,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        output_stream.write("\n")
    except (_UsageError, ManifestError, OperationError) as error:
        print(f"latch: {error}", file=error_stream)
        return EXIT_USAGE
    except (KeyboardInterrupt, Exception) as error:
        print(
            f"latch: internal failure: {type(error).__name__}: {error}",
            file=error_stream,
        )
        return EXIT_INTERNAL

    failed_statuses = {"error", "timed_out"}
    execution_failed = (
        result.deadline_status is not DeadlineState.MET
        or any(
            tool_result.status in failed_statuses
            for tool_result in result.tool_outputs
        )
    )
    return EXIT_EXECUTION_FAILED if execution_failed else EXIT_OK


def _build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(prog="latch")
    subcommands = parser.add_subparsers(dest="command", required=True)
    batch = subcommands.add_parser(
        "batch",
        help="execute a validated repository-operation manifest",
    )
    batch.add_argument("plan_path", metavar="PLAN_PATH")
    batch.add_argument("--deadline-ms", type=int, required=True)
    batch.add_argument(
        "--mode",
        choices=("serial", "optimized"),
        default="optimized",
    )
    batch.add_argument("--json", action="store_true", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
