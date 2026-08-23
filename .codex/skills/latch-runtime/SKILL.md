---
name: latch-runtime
description: Run bounded, read-only repository inspections concurrently through latch.
---

# Latch Runtime

Use `latch batch` for a bounded, known set of two or more independent,
read-only repository operations. It returns compact JSON with runtime spans.

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

1. Create a version-1 manifest with only `read_file`, `search`, `git_status`,
   and `pytest` operations.
2. Set conservative `deadline_paths`; do not declare resources, because the
   runtime derives them from validated arguments.
3. Run `latch batch PLAN_PATH --deadline-ms 5000 --mode optimized --json`.
4. Parse stdout as one JSON document. Treat a nonzero exit as structured result
   information, not a reason to retry arbitrary shell work.
5. Summarize outputs and explain skipped or timed-out calls from their spans.

Never add a caller-provided shell string to a manifest. `latch batch` is a
read-only preflight tool; use normal Codex tools for edits.
