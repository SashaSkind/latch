"""Live dashboard API and static page for the deadline-aware runtime demo."""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict

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
