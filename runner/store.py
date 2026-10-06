"""Small, file-backed job store. Each trial has its own atomic state file."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("TASKLAB_DATA", ROOT / ".tasklab")).resolve()
MODEL = "openrouter/z-ai/glm-5.3-flash"
EFFORT = "high"
CONCURRENCY = 3
TERMINAL = {"passed", "failed", "error", "timeout", "cancelled", "skipped", "interrupted"}
JOB_TERMINAL = {"completed", "validation_failed", "error", "cancelled", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


def redact(value):
    text = str(value)
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        text = text.replace(key, "[REDACTED]")
    return re.sub(r"sk-or-v1-[A-Za-z0-9_-]+", "[REDACTED]", text)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(redact(json.dumps(value, indent=2)))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def directory(job_id):
    if not re.fullmatch(r"[a-f0-9]{16}", job_id):
        raise KeyError(job_id)
    path = DATA / "jobs" / job_id
    if not (path / "job.json").is_file():
        raise KeyError(job_id)
    return path


def examples():
    import tomllib
    rows = []
    for path in sorted((ROOT / "examples").iterdir()):
        if not (path / "task.toml").is_file():
            continue
        config = tomllib.loads((path / "task.toml").read_text())
        rows.append({"id": path.name, "name": path.name,
                     "description": config.get("task", {}).get("description", ""),
                     "instruction": (path / "instruction.md").read_text()})
    return rows


def create_job(task_id, mode="full", task_path=None):
    if mode not in {"full", "controls"}:
        raise ValueError("Mode must be full or controls")
    if task_path is None:
        if task_id not in {row["id"] for row in examples()}:
            raise ValueError("Unknown example task")
        source = ROOT / "examples" / task_id
    else:
        source = Path(task_path).resolve()
    job_id = uuid.uuid4().hex[:16]
    path = DATA / "jobs" / job_id
    path.mkdir(parents=True)
    snapshot = path / "task"
    shutil.copytree(source, snapshot, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".git"))
    digest = hashlib.sha256()
    for file in sorted(snapshot.rglob("*")):
        if file.is_file():
            digest.update(str(file.relative_to(snapshot)).encode())
            digest.update(file.read_bytes())
    record = {"id": job_id, "task_id": task_id, "task_name": task_id, "mode": mode,
              "status": "queued", "phase": "Waiting for worker", "created_at": now(),
              "updated_at": now(), "error": None, "validation": None,
              "task_sha256": digest.hexdigest(), "model": MODEL, "reasoning_effort": EFFORT,
              "api_base": os.environ.get("OPENROUTER_API_BASE", "https://api.aqinference.com/v1"),
              "max_concurrency": CONCURRENCY, "run_ids": ["oracle", "nop"] + [f"eval-{n}" for n in range(1, 6)]}
    for run_id in record["run_ids"]:
        kind = run_id if run_id in {"oracle", "nop"} else "evaluation"
        number = int(run_id.split("-")[1]) if kind == "evaluation" else None
        write_json(path / "runs" / run_id / "state.json", {
            "id": run_id, "kind": kind, "number": number,
            "label": {"oracle": "Oracle", "nop": "Nop"}.get(kind, f"Run {number}"),
            "status": "skipped" if mode == "controls" and kind == "evaluation" else "queued",
            "reward": None, "expected_reward": 0 if kind == "nop" else 1,
            "passed": None, "started_at": None, "completed_at": None,
            "duration_sec": None, "turns": 0, "error": None, "tests": []})
    write_json(path / "job.json", record)  # Publish only after the snapshot and run slots exist.
    return job(job_id)


def summarize(runs):
    evaluations = [r for r in runs if r["kind"] == "evaluation"]
    valid = [r for r in evaluations if r["status"] in {"passed", "failed"}]
    passes = sum(r["status"] == "passed" for r in valid)
    return {"passes": passes, "failures": len(valid) - passes, "valid_runs": len(valid),
            "total_runs": 5, "learnable": 1 <= passes <= 3 if len(valid) == 5 else None}


def job(job_id):
    path = directory(job_id)
    record = read_json(path / "job.json")
    record["runs"] = [read_json(path / "runs" / rid / "state.json") for rid in record["run_ids"]]
    # Live turn counts can be read without waiting for the Harbor process to finish.
    from runner.artifacts import episodes
    for run in record["runs"]:
        if run["status"] == "running":
            run["turns"] = len(episodes(path / "runs" / run["id"]))
    record["summary"] = summarize(record["runs"])
    return record


def jobs():
    paths = sorted((DATA / "jobs").glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [job(p.parent.name) for p in paths]
