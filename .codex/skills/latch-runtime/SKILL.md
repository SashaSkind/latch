---
name: latch-runtime
description: Route bounded, independent repository inspections through the Latch MCP runtime.
---

# Latch Runtime

Use the Latch MCP server's `latch_execute_batch` tool for a bounded, known set
of two or more independent repository operations. It returns tool outputs and
runtime spans from the deadline-aware optimized scheduler.

## Use It When

- You can identify multiple file reads, searches, Git status checks, or focused
  pytest targets before starting.
- The user requested a quick preflight or an explicit deadline.
- Every operation is supported by the manifest allowlist.

## Do Not Use It When

- One operation is enough.
- A later operation depends on interpreting an earlier result.
- The task requires an edit, interactive approval, arbitrary shell command, or
  a native tool artifact that must be preserved.

## Workflow

1. Build a version-1 manifest in memory with only `read_file`, `search`,
   `git_status`, and focused `pytest` operations.
2. Set conservative `deadline_paths`; do not declare resources, because the
   runtime derives them from validated arguments.
3. Call `latch_execute_batch` on the `latch` MCP server with the manifest,
   `deadline_ms`, and `mode: "optimized"`.
4. Treat deadline misses, skips, timeouts, and per-call errors as structured
   result information, not a reason to retry arbitrary shell work.
5. Summarize outputs and explain skipped or timed-out calls from their spans.

If the MCP server is unavailable, fall back to creating a temporary plan and
running `latch batch PLAN_PATH --deadline-ms 5000 --mode optimized --json`.
Never add a caller-provided shell string to a manifest. Latch is a bounded
preflight tool; use normal Codex tools for edits.
