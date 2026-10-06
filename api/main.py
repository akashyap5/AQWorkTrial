import os
import subprocess
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from typing import Literal
from runner import artifacts, store
from runner.service import process_alive

app = FastAPI(title="AfterQuery Task Lab", version="1.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
app.mount("/static", StaticFiles(directory=store.ROOT / "api" / "static"), name="static")


@app.middleware("http")
async def local_access(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/")
def index():
    return FileResponse(store.ROOT / "api" / "static" / "index.html")


@app.get("/api/health")
def health():
    try:
        result = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=3)
        docker = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        docker = False
    records = store.jobs()
    try:
        worker_pid = int((store.DATA / "worker.lock").read_text().strip())
        worker_alive = process_alive(worker_pid, "runner.service")
    except (FileNotFoundError, ValueError):
        worker_alive = False
    return {"status": "ok" if docker and worker_alive else "degraded", "docker": docker,
            "worker_alive": worker_alive,
            "key_present": bool(os.environ.get("OPENROUTER_API_KEY")), "model": store.MODEL,
            "reasoning_effort": store.EFFORT, "max_concurrency": store.CONCURRENCY,
            "active_runs": sum(r["status"] == "running" for j in records for r in j["runs"]),
            "api_base": os.environ.get("OPENROUTER_API_BASE", "https://api.aqinference.com/v1"),
            "data_directory": str(store.DATA)}


@app.get("/api/examples")
def examples():
    return {"examples": store.examples()}


@app.get("/api/jobs")
def jobs():
    return {"jobs": store.jobs()}


class Submission(BaseModel):
    task_id: str
    mode: Literal["full", "controls"] = "full"


@app.post("/api/jobs", status_code=202)
def submit(body: Submission):
    if body.mode == "full" and not os.environ.get("OPENROUTER_API_KEY"):
        raise HTTPException(503, "Set OPENROUTER_API_KEY before starting model evaluations.")
    try:
        return store.create_job(body.task_id, body.mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return store.job(job_id)
    except KeyError:
        raise HTTPException(404, "Unknown job") from None


@app.get("/api/jobs/{job_id}/runs/{run_id}")
def run(job_id: str, run_id: str):
    record = get_job(job_id)
    if run_id not in record["run_ids"]:
        raise HTTPException(404, "Unknown run")
    return artifacts.details(store.directory(job_id) / "runs" / run_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    record = get_job(job_id)
    if record["status"] not in store.JOB_TERMINAL:
        (store.directory(job_id) / "cancel").touch()
    return get_job(job_id)
