"""Live dashboard API and static page for the deadline-aware runtime demo."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from demo.task import run_task
from demo.trials import run_trials
from telemetry.codex import clear_observations, load_observations
from telemetry.events import SpanEvent
from telemetry.store import TraceStore

ROOT = Path(__file__).resolve().parent.parent
store = TraceStore()
CODEX_OBSERVATIONS = ROOT / ".latch" / "codex-observations.jsonl"
app = FastAPI(title="Deadline-Aware Agent Runtime")

SCALE_BENCHMARK: dict[str, object] = {
    "recorded_at": "2026-08-23",
    "workload": {
        "corpus_mib": 1024,
        "checks": 32,
        "deadline_ms": 60_000,
        "operation": "independent full-corpus regex searches",
    },
    "latch": {
        "end_to_end_ms": 31_163,
        "runtime_ms": 5_936.0735421180725,
        "tool_calls": 1,
        "operations": 32,
        "input_tokens": 86_964,
        "cached_input_tokens": 69_632,
        "output_tokens": 770,
        "reasoning_tokens": 322,
        "matched_ids": 0,
        "reported_failures": 0,
        "deadline_status": "met",
    },
    "plain_codex": {
        "end_to_end_ms": 38_087,
        "tool_calls": 32,
        "operations": 32,
        "input_tokens": 41_134,
        "cached_input_tokens": 29_184,
        "output_tokens": 1_472,
        "reasoning_tokens": 1_083,
        "matched_ids": 0,
        "reported_failures": 32,
    },
    "comparison": {
        "end_to_end_speedup": 1.22,
        "latency_saved_ms": 6_924,
        "latency_reduction_percent": 18.18,
        "tool_call_compression": 32,
        "tool_calls_eliminated": 31,
        "input_token_overhead": 45_830,
        "output_tokens_saved": 702,
    },
    "scheduler": {
        "trials": 10,
        "budget_ms": 1_200,
        "serial_p50_ms": 1_074,
        "serial_p95_ms": 1_085,
        "optimized_p50_ms": 411,
        "optimized_p95_ms": 417,
        "speedup": 2.62,
        "deadline_hit_rate_percent": 100,
    },
}


class RunRequest(BaseModel):
    mode: str = Field(pattern="^(serial|optimized)$")
    budget_ms: int = Field(default=1200, ge=100, le=10_000)
    seed: int = Field(default=7, ge=0)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (ROOT / "app" / "index.html").read_text()


@app.get("/api/fixture")
async def fixture() -> dict[str, object]:
    events = json.loads((ROOT / "fixtures" / "events" / "optimized-trace.json").read_text())
    return {"events": events}


@app.get("/api/codex/fixture")
async def codex_fixture() -> dict[str, object]:
    return {
        "batch": json.loads(
            (ROOT / "fixtures" / "codex" / "batch-result-v1.json").read_text()
        ),
        "observations": json.loads(
            (ROOT / "fixtures" / "codex" / "observations-v1.json").read_text()
        ),
    }


@app.get("/api/codex/observations")
async def codex_observations() -> dict[str, object]:
    return {"observations": [item.to_dict() for item in load_observations(CODEX_OBSERVATIONS)]}


@app.get("/api/scale-benchmark")
async def scale_benchmark() -> dict[str, object]:
    """Return the recorded Codex MCP scale comparison."""

    return SCALE_BENCHMARK


@app.post("/api/codex/batch")
async def ingest_codex_batch(batch: dict[str, object]) -> dict[str, object]:
    """Accept one version-1 CLI result so the dashboard can render its spans."""

    if batch.get("schema_version") != 1 or not isinstance(batch.get("spans"), list):
        raise HTTPException(status_code=422, detail="expected a version-1 batch result")
    try:
        spans = [SpanEvent(**span) for span in batch["spans"]]
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"invalid batch spans: {error}") from error

    for span in spans:
        store.record(span)
    return {
        "run_ids": sorted({span.run_id for span in spans}),
        "span_count": len(spans),
    }


@app.post("/api/run")
async def execute(request: RunRequest) -> dict[str, object]:
    result = await run_task(request.mode, request.budget_ms, request.seed, store.record)
    return {
        "run_id": result.run_id,
        "mode": result.mode,
        "deadline_path": result.deadline_path,
        "elapsed_ms": result.elapsed_ms,
        "deadline_status": result.deadline_status,
        "outputs": result.outputs,
        "events": [event.to_dict() for event in store.events_for(result.run_id)],
    }


@app.get("/api/runs")
async def runs() -> dict[str, object]:
    return {run_id: [event.to_dict() for event in events] for run_id, events in store.all_runs().items()}


@app.post("/api/reset")
async def reset() -> dict[str, bool]:
    store.clear()
    clear_observations(CODEX_OBSERVATIONS)
    return {"ok": True}


@app.get("/api/compare")
async def compare(trials: int = 5, budget_ms: int = 1200) -> dict[str, object]:
    return asdict(await run_trials(trials, budget_ms))
