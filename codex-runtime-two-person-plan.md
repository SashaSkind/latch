# Deadline-Aware Runtime for Codex — Two-Person Integration Plan

## Status

PROPOSED — lock the CLI manifest and output contract together during the
first 15 minutes. After that lock, neither person changes the shared schema
without a short sync.

## Goal

Make the existing deadline-aware runtime useful during real Codex repository
work without building another agent loop.

The MVP should let Codex intentionally invoke one safe batch command for
bounded repository operations:

```bash
latch batch plan.json --deadline-ms 5000 --mode optimized --json
```

That command should execute independent operations concurrently, preserve
resource correctness, enforce the deadline, and return compact JSON plus the
existing `SpanEvent` trace.

The Codex integration follows the pattern recommended in the official
OpenAI documentation: expose a composable CLI and teach Codex when to use it
through a companion skill. Codex lifecycle hooks are a separate,
telemetry-only layer in this MVP.

References:

- [Create a CLI Codex can use](https://learn.chatgpt.com/use-cases/agent-friendly-clis)
- [Codex hooks](https://learn.chatgpt.com/docs/hooks)

## Scope

The MVP includes:

- A `latch batch` CLI backed by the existing `Runtime.execute_batch`
- A versioned JSON manifest
- Repository-root path validation
- A small allowlist of safe operations
- Runtime-derived read/write resource declarations
- JSON results and the locked `SpanEvent` schema
- A project-local Codex skill that selects the CLI for suitable tasks
- Optional telemetry-only `PreToolUse` and `PostToolUse` hooks
- A dashboard view for real runtime batches and observed Codex calls
- End-to-end tests and three consecutive rehearsals

The MVP excludes:

- Replacing or intercepting Codex's native scheduler
- A custom LLM or agent loop
- Codex SDK orchestration
- MCP
- Arbitrary shell execution through the batch manifest
- Automated source-file writes through the CLI
- Hooks that approve, deny, or rewrite Codex tool calls
- Distributed workers, Redis, or persistent shared caching
- A claim that synthetic speedups transfer directly to model latency

## Architecture

```text
User prompt
    |
    v
Codex companion skill
    |
    v
latch batch CLI
    |
    +--> manifest validation and repository boundary checks
    |
    +--> allowlisted operation adapters
    |
    v
Runtime.execute_batch
    |
    +--> conflict-aware scheduling
    +--> resource-versioned caching
    +--> deadline paths and hard timeout
    |
    v
JSON result + SpanEvents --> dashboard

Codex PreToolUse/PostToolUse hooks --> observed session trace --> dashboard
```

The hook path observes Codex lifecycle activity. It does not schedule runtime
work and does not mutate native Codex tool calls.

## Shared contract lock — first 15 minutes

### Stable command

```bash
latch batch PLAN_PATH \
  --deadline-ms INTEGER \
  --mode serial|optimized \
  --json
```

Rules:

- `--json` writes exactly one JSON document to stdout.
- Human-readable diagnostics go to stderr.
- Paths resolve relative to the discovered repository root.
- The command never executes a caller-provided shell string.
- The existing `Runtime.execute_batch` signature does not change.

### Manifest schema

```json
{
  "schema_version": 1,
  "calls": [
    {
      "id": "read-runtime",
      "operation": "read_file",
      "arguments": {
        "path": "src/runtime/runtime.py"
      },
      "deadline_paths": ["full", "fast", "partial"]
    },
    {
      "id": "search-execute-batch",
      "operation": "search",
      "arguments": {
        "query": "execute_batch",
        "path": "src"
      },
      "deadline_paths": ["full", "fast"]
    },
    {
      "id": "runtime-tests",
      "operation": "pytest",
      "arguments": {
        "target": "tests/runtime"
      },
      "deadline_paths": ["full"]
    }
  ]
}
```

Manifest rules:

- Required fields are `schema_version`, `calls`, `id`, `operation`, and
  `arguments`.
- `id` is unique within one manifest.
- `deadline_paths` is optional and defaults to all paths supported by that
  operation.
- The caller may choose deadline eligibility but may not declare resources.
- The operation adapter derives conservative resources from validated
  arguments.
- Unknown top-level and call-level fields are rejected for schema version 1.

### MVP operations

`read_file`

- Reads one UTF-8 text file inside the repository.
- Declares `file:<normalized-path>` as a read resource.
- Is cacheable.
- Rejects directories, symlink escapes, binary input, and oversized input.

`search`

- Runs a fixed `rg` argument template; it never accepts a shell command.
- Searches only within a validated repository-relative path.
- Declares the normalized search scope as a read resource.
- Is not cached in the first version.

`git_status`

- Runs the fixed equivalent of `git status --short --branch`.
- Accepts no command arguments.
- Declares repository and Git metadata read resources.
- Is not cached.

`pytest`

- Runs pytest against a validated repository-relative target.
- Accepts only an allowlisted set of options.
- Declares source/test reads plus conservative pytest cache writes.
- Is not cached.
- Only one pytest operation may execute at a time.

### Result schema

```json
{
  "schema_version": 1,
  "tool_outputs": [
    {
      "call_id": "read-runtime",
      "tool_name": "read_file",
      "output": "...",
      "status": "ok",
      "error": null
    }
  ],
  "spans": [],
  "deadline_path": "full",
  "elapsed_ms": 123.4,
  "deadline_status": "met"
}
```

The serialized span fields remain exactly:

```text
run_id
call_id
tool_name
event_type
started_at_ms
ended_at_ms
duration_ms
read_resources
written_resources
cache_status
deadline_path
remaining_budget_ms
status
```

### Exit codes

- `0`: Valid result, deadline met, and no required operation failed.
- `2`: Valid structured result, but the deadline timed out or an operation
  failed.
- `64`: Invalid CLI usage or manifest.
- `70`: Internal failure prevented a valid structured result.

### Hook trace contract

Hook observations use a separate schema rather than pretending to be runtime
`SpanEvent` records:

```text
schema_version
session_id
turn_id
tool_use_id
phase
tool_name
observed_at_ms
status
```

For the MVP, hooks do not persist raw prompts, full tool arguments, tool
responses, secrets, or file contents.

## Person A — Runtime and CLI

### Responsibilities

- Own manifest parsing and validation.
- Own repository-root discovery and path containment.
- Implement the allowlisted operation adapters.
- Derive conservative resource sets from validated operation arguments.
- Bridge manifest calls to the existing runtime without changing its stable
  public interface.
- Serialize `ExecutionResult` as the locked JSON result.
- Keep stdout machine-readable and send diagnostics to stderr.
- Add security, scheduling, cache, timeout, and CLI tests.
- Own CLI packaging and the console entry point.

### File ownership

```text
src/runtime/*
src/codex_runtime/__init__.py
src/codex_runtime/manifest.py
src/codex_runtime/operations.py
src/codex_runtime/cli.py
tests/runtime/*
tests/codex_runtime/*
pyproject.toml
```

### Required output

```python
result = await execute_manifest(
    manifest,
    deadline_ms=5000,
    mode="optimized",
    event_callback=callback,
)
```

Person A must prove:

- Three independent reads can overlap.
- Equivalent file reads can reuse a valid cache entry.
- Invalid paths cannot escape the repository.
- Arbitrary commands cannot enter an operation adapter.
- Pytest invocations do not overlap each other.
- Tight deadlines produce a structured terminal result.
- Serial and optimized modes receive the identical ordered manifest.

## Person B — Codex integration and visibility

### Responsibilities

- Create the project-local companion skill.
- Define when Codex should and should not use `latch batch`.
- Develop against a checked-in version-1 result fixture until the CLI lands.
- Add telemetry-only Codex lifecycle hooks.
- Correlate hook observations by session, turn, and tool-use ID.
- Display real batch SpanEvents and observed Codex calls separately.
- Add end-to-end integration tests.
- Own setup instructions, examples, reset behavior, and rehearsal.

### File ownership

```text
.codex/skills/latch-runtime/SKILL.md
.codex/hooks.json
scripts/codex_hooks/*
src/telemetry/*
app/*
fixtures/codex/*
tests/integration/*
README.md
.gitignore
```

### Companion skill behavior

The skill should select `latch batch` when:

- The task contains two or more bounded repository inspections.
- The operations can be known before the batch starts.
- The user requests a deadline or quick preflight.
- The batch uses only the allowlisted operations.

The skill should not select it when:

- One operation is sufficient.
- The next operation depends on interpreting the previous result.
- The task requires interactive approval.
- The task needs file edits or an unsupported command.
- Preserving a native tool artifact is more important than compact JSON.

### Hook behavior

- Observe only `PreToolUse` and `PostToolUse` during the MVP.
- Record timing and correlation identifiers only.
- Do not return approval decisions.
- Do not return `updatedInput`.
- Do not block tool results.
- Fail open if telemetry storage is unavailable.
- Keep hook execution fast and bounded.

## Timeline

### 0:00–0:15 — Contract lock

Both people:

- Review the command, manifest, result, exit codes, and hook trace schema.
- Confirm file ownership.
- Commit the locked plan before branching.
- Person B creates a fixture conforming to the locked schemas.

Gate: both people can build without editing the same files.

### 0:15–1:00 — Independent baselines

Person A:

- Scaffold `src/codex_runtime`.
- Parse and validate a manifest.
- Implement `read_file` and JSON output.
- Run one serial batch through the real runtime.

Person B:

- Create the companion skill.
- Add fixture-backed dashboard support.
- Add the hook trace schema and recorder skeleton.

Gate: a fixture and a real CLI run have the same JSON shape.

### 1:00–1:45 — Operations and hooks

Person A:

- Add `search`, `git_status`, and `pytest`.
- Add resource derivation and path containment.
- Add optimized execution and deadline tests.

Person B:

- Add telemetry-only `PreToolUse` and `PostToolUse` hooks.
- Correlate lifecycle events.
- Render observed Codex calls separately from runtime spans.

Gate: the CLI performs a useful repository preflight and hooks record one
Codex turn without changing its behavior.

### 1:45–2:15 — Integration

Both people:

- Replace the fixture playback with one real CLI result.
- Invoke the CLI from a Codex session using the companion skill.
- Verify JSON parsing, trace rendering, cache behavior, and deadlines.
- Fix only integration defects within each person's owned files.

Gate: one Codex prompt causes one safe runtime batch and returns a useful
answer.

### 2:15–3:00 — Hardening and rehearsal

Person A:

- Complete CLI and security tests.
- Verify timeout cleanup and subprocess termination.
- Benchmark serial and optimized execution on a controlled real-repository
  preflight.

Person B:

- Finish setup and troubleshooting instructions.
- Verify hooks fail open.
- Run the complete workflow three consecutive times.

Gate: three consecutive Codex-driven runs finish without manual repair.

## Git and merge rules

1. Commit this locked contract to `main` first.
2. Person A branches to `feature/runtime-cli`.
3. Person B branches to `feature/codex-integration`.
4. Neither person edits the other person's files.
5. Person B uses fixtures until Person A's CLI is merged.
6. Merge Person A's CLI first.
7. Rebase Person B's branch, run the integration tests, and merge it second.
8. Keep commits small and use clean messages without generated attribution.
9. Push each green commit so the teammate can integrate continuously.

## Acceptance criteria

The MVP is complete when:

- `latch batch` works from the repository root with one documented command.
- Codex can discover and intentionally invoke it through the companion skill.
- The manifest supports only the four allowlisted operations.
- Repository path escape attempts are rejected.
- No caller-provided shell command can execute.
- Independent reads overlap in optimized mode.
- Repeated eligible reads show correct cache behavior.
- Pytest operations never overlap each other.
- Tight deadlines return structured output instead of hanging.
- stdout remains valid versioned JSON.
- All runtime spans preserve the locked `SpanEvent` fields.
- Hooks observe a Codex turn without approving, blocking, or rewriting calls.
- The dashboard distinguishes runtime spans from native Codex observations.
- Serial and optimized runs use the identical manifest.
- Three consecutive Codex-driven demonstrations complete without repair.

## Cut order

If time runs short, cut in this order:

1. Codex lifecycle hooks
2. Native Codex trace visualization
3. Persistent cache across separate CLI processes
4. `git_status`
5. Dashboard polish

Do not cut:

- Manifest validation
- Repository boundary enforcement
- The arbitrary-shell prohibition
- Machine-readable JSON
- Runtime scheduling and deadline enforcement
- The companion skill
- End-to-end invocation from a Codex session

## Handoff message for Person B

You own the Codex-facing layer: the companion skill, telemetry-only hooks,
fixtures, dashboard integration, end-to-end tests, and documentation. Develop
against the version-1 schemas in this plan and do not import runtime internals
outside the locked result and event contracts. The CLI will be merged first;
after rebasing, replace fixture playback with the real `latch batch` result
and run the three-rehearsal gate.
