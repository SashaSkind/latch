"""Live dashboard API and static page for the deadline-aware runtime demo."""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from demo.task import run_task
from demo.trials import run_trials
from telemetry.store import TraceStore

ROOT = Path(__file__).resolve().parent.parent
store = TraceStore()
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
    return {"ok": True}


@app.get("/api/compare")
async def compare(trials: int = 5, budget_ms: int = 1200) -> dict[str, object]:
    return asdict(await run_trials(trials, budget_ms))
