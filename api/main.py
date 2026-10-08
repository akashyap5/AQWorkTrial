import json
import copy
import os
import subprocess
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from markdown_it import MarkdownIt
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
    rows = store.examples()
    for row in rows:
        row["readme_html"] = render_markdown(row["readme"])
    return {"examples": rows}


def render_markdown(text):
    return MarkdownIt("commonmark", {"html": False}).disable("image").render(text)


def snapshot_text(task_root, filename):
    """Read only a bounded file inside the frozen task snapshot."""
    target = task_root / filename
    try:
        if not target.resolve().is_relative_to(task_root.resolve()):
            return None
        with target.open("r", encoding="utf-8", errors="replace") as source:
            return store.redact(source.read(256_000))
    except OSError:
        return None


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


def phase2_controller():
    # The API only submits durable work; generation never runs in an HTTP request.
    from phase2 import controller
    return controller


@app.get("/api/phase2")
def phase2_status():
    controller = phase2_controller()
    result = copy.deepcopy(controller.status())
    for batch in result.get("batches", []):
        # Fingerprint shingles are durable worker evidence, not poll payloads.
        batch.pop("archive", None)
        try:
            observations = store.read_json(controller._path(batch["id"]) / "observations.json", {})
        except (KeyError, OSError):
            observations = {}
        if isinstance(observations, dict) and isinstance(observations.get("observations"), list):
            batch["runtime_observations"] = observations["observations"]
        for task in batch.get("tasks", []):
            task.pop("fingerprint", None)
            # These are presentation fields, never trusted from an author response.
            task.pop("readme_html", None)
            task.pop("instruction", None)
            if not task.get("job_id"):
                continue
            try:
                task_root = store.directory(task["job_id"]) / "task"
            except KeyError:
                continue
            readme = snapshot_text(task_root, "README.md")
            instruction = snapshot_text(task_root, "instruction.md")
            if readme is not None:
                task["readme_html"] = render_markdown(readme)
            if instruction is not None:
                task["instruction"] = instruction
    return result


@app.post("/api/phase2/batches", status_code=202)
def phase2_start():
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise HTTPException(503, "Set OPENROUTER_API_KEY before starting task generation.")
    try:
        return phase2_controller().start_batch()
    except ValueError as exc:
        raise HTTPException(409, store.redact(str(exc))) from None


@app.post("/api/phase2/batches/{batch_id}/stop")
def phase2_stop(batch_id: str):
    try:
        return phase2_controller().stop_batch(batch_id)
    except KeyError:
        raise HTTPException(404, "Unknown batch") from None
    except ValueError as exc:
        raise HTTPException(422, store.redact(str(exc))) from None


def search_controller():
    from phase2 import search
    return search


@app.get("/api/prompts")
def prompt_versions():
    """Generator prompt versions, newest first, plus the measured bug-class stats."""
    folder = store.ROOT / "prompts" / "fast_author_versions"
    rows = []
    for path in sorted(folder.glob("v*.json"), reverse=True):
        try:
            rows.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    stats_file = store.ROOT / "prompts" / "bug_class_stats.json"
    try:
        stats = json.loads(stats_file.read_text())
    except (OSError, ValueError):
        stats = {"classes": {}, "probes": []}
    return {"versions": rows, "current": rows[0]["version"] if rows else None, "stats": stats}


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def author_spec(slug):
    return read_json(store.ROOT / "output" / "candidates" / slug / ".author.json", {})


GOLDEN = store.ROOT / "golden"


@app.get("/api/shipped")
def shipped():
    """The frozen golden set (golden/manifest.json): fixed tasks and measurements that later runs never change."""
    manifest = read_json(GOLDEN / "manifest.json", None)
    if manifest:
        return {"shipped": manifest["shipped"], "frozen_at": manifest.get("frozen_at")}
    return live_shipped()


def live_shipped():
    """Gated learnable tasks with every measurement of the shipped text: the probe that shipped it, then later re-runs."""
    import tomllib
    by_task = {}
    for job in store.jobs():
        name = job.get("task_name") or ""
        by_task.setdefault(name.removeprefix("gen-"), []).append(job)
    rows = []
    for toml_path in sorted((store.ROOT / "output" / "learnable").glob("*/task.toml")):
        slug = toml_path.parent.name
        config = tomllib.loads(toml_path.read_text())
        meta = config.get("metadata", {})
        spec = author_spec(slug)
        verdict = read_json(toml_path.parent / "verdict.json", {})
        jobs = by_task.get(slug, [])  # probes, stability re-runs, and runs launched from this page ("gen-" ids)
        probe = next((j for j in jobs if j["id"] == meta.get("probe_job")), None)
        # Later runs of the same task; skip jobs that ended without a single valid attempt (they measured nothing).
        later = sorted((j for j in jobs if probe and j["id"] != probe["id"] and j["created_at"] > probe["created_at"]
                        and not (j["status"] in store.JOB_TERMINAL and not (j.get("summary") or {}).get("valid_runs"))),
                       key=lambda j: j["created_at"])
        audit = verdict.get("fairness_audit") or {}
        rows.append({
            "slug": slug, "description": config.get("task", {}).get("description", ""),
            "measured_passes": meta.get("measured_passes"), "probe_job": meta.get("probe_job"),
            "family": meta.get("family"), "authoring_model": meta.get("authoring_model"),
            "prompt_version": spec.get("prompt_version"), "derived_from": spec.get("derived_from"),
            "tweaks": [{k: t.get(k) for k in ("direction", "change", "by")} for t in spec.get("tweak_history", [])],
            "gate": {"policy": verdict.get("gate_policy"), "traced_runs": audit.get("traced_runs"), "verdict": audit.get("verdict")},
            "measurements": [{"id": j["id"], "created_at": j["created_at"], "status": j["status"], "summary": j.get("summary"),
                              "source": (j.get("metadata") or {}).get("source") or "ui", "shipping": j is probe}
                             for j in ([probe] if probe else []) + later],
        })
    return {"shipped": rows}


@app.get("/api/history")
def history():
    """Checklist-prompt versions with first-probe results, and the strategy library's evolution."""
    first = {}
    for job in store.jobs():
        if (job.get("metadata") or {}).get("source") != "fast_author" or job.get("status") != "completed":
            continue
        name = job.get("task_name")
        if name not in first or job["created_at"] < first[name]["created_at"]:
            first[name] = job
    stats, shipped_by = {}, {}
    for name, job in first.items():
        version = author_spec(name).get("prompt_version")
        if not version:
            continue
        summary = job.get("summary") or {}
        passes, valid = summary.get("passes") or 0, summary.get("valid_runs") or 0
        row = stats.setdefault(version, {"probes": 0, "in_band": 0, "too_easy": 0, "too_hard": 0, "inconclusive": 0})
        row["probes"] += 1
        row["too_easy" if passes >= 4 else "inconclusive" if valid < 5 else "too_hard" if passes == 0 else "in_band"] += 1
    for toml_path in (store.ROOT / "output" / "learnable").glob("*/task.toml"):
        version = author_spec(toml_path.parent.name).get("prompt_version")
        shipped_by[version] = shipped_by.get(version, 0) + 1
    versions = []
    for path in sorted((store.ROOT / "prompts" / "trap_author_versions").glob("t*.json"), reverse=True):
        v = read_json(path, None)
        if not v:
            continue
        versions.append({key: v.get(key) for key in ("version", "created_at", "base_of", "changes", "hash", "base_prompt")}
                        | {"stats": stats.get(v.get("version"), {}), "shipped": shipped_by.get(v.get("version"), 0)})
    lib = read_json(store.ROOT / "prompts" / "strategies.json", {})
    return {"prompt_versions": versions, "strategy_version": lib.get("version"), "strategy_updated": lib.get("updated"),
            "strategies": [{key: s.get(key) for key in ("id", "kind", "status", "name", "how", "stats", "previous_how")}
                           for s in lib.get("strategies", [])],
            "strategy_history": list(reversed(lib.get("history", [])))}


@app.get("/api/search")
def search_status():
    return search_controller().status()


@app.post("/api/search/start", status_code=202)
def search_start():
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise HTTPException(503, "Set OPENROUTER_API_KEY before starting task generation.")
    try:
        return search_controller().start_search()
    except ValueError as exc:
        raise HTTPException(409, store.redact(str(exc))) from None


@app.post("/api/search/stop")
def search_stop():
    try:
        return search_controller().stop_search()
    except KeyError:
        raise HTTPException(404, "No search to stop") from None
    except ValueError as exc:
        raise HTTPException(422, store.redact(str(exc))) from None
