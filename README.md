# Deadline-Aware Agent Runtime Demo

A deterministic hackathon demonstration of conflict-aware tool scheduling, resource-versioned read caching, and deadline-aware execution paths. The I/O delay is synthetic and seeded so the dashboard measures a controlled workload, not real filesystem performance.

## Run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m demo.compare --trials 25 --budget-ms 1200
.venv/bin/uvicorn app.server:app --reload
```

Open `http://127.0.0.1:8000`, then select **Run serial vs optimized**. The dashboard executes the identical full task in both modes, shows the optimized timeline, cache events, invalidation after edit, and the selected deadline route.

Use **Run tight deadline** to run the 700ms budget and visibly select the `fast` route, which skips the thorough documentation search. **Replay fixture** works before runtime integration using `fixtures/events/optimized-trace.json`.

## Reset

Restarting the server clears the in-memory trace store. Each task run creates a fresh fixture repository state and cache, so trials are isolated without a manual filesystem reset.

## Two-minute script

1. Run the serial-versus-optimized comparison at 1200ms. Point out that both runs modify `src/auth.py`, reread it, and pass the same test result.
2. Compare headline latency. The full route has three independent 300ms-class reads; the optimized timeline fans them out while the serial baseline waits for each.
3. Point out the initial cache hit, the write invalidation, and the post-write cache miss proving the updated bytes were read.
4. Run the 700ms scenario. The route changes to `fast`, skips the documentation branch, and returns under its tighter budget.

## Claim boundary

This demo supports a measured P95 below a configured deadline for a controlled workload with declared resources and seeded latency. It does not claim a universal latency guarantee for arbitrary coding agents or production workloads.
