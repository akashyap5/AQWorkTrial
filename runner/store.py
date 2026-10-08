"""Small, file-backed job store. Each trial has its own atomic state file."""
import copy
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
CONCURRENCY = 5
TERMINAL = {"passed", "failed", "error", "timeout", "budget_exhausted", "cancelled", "skipped", "interrupted"}
JOB_TERMINAL = {"completed", "validation_failed", "error", "cancelled", "interrupted"}
DEMO_BUDGET = {"agent_timeout_sec": 300, "cost_usd": 0.05,
               "max_completion_tokens": 16384,
               "max_price_per_million": {"prompt": 0.3, "completion": 1.0}}
# The installed LiteLLM OpenRouter catalog declares 131072 output tokens for
# this model. Search keeps that native maximum, without the demo's short cap.
SEARCH_BUDGET = {"agent_timeout_sec": 600, "cost_usd": 0.20,
                 "max_completion_tokens": 131072,
                 "provider_sort": "throughput",
                 "max_price_per_million": {"prompt": 0.3, "completion": 1.0}}
METERED_PROFILES = {"demo", "search"}


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


# Accepted generated tasks are listed after the supplied examples.
LEARNABLE = ROOT / "output" / "learnable"
GOLDEN = ROOT / "golden"


def task_dirs():
    """Yield (id, name, group, path) for every selectable task."""
    for path in sorted((ROOT / "examples").iterdir()):
        if (path / "task.toml").is_file():
            yield path.name, path.name, "Examples", path
    generated = GOLDEN if GOLDEN.is_dir() else LEARNABLE  # the frozen golden set; newer tasks are not selectable
    if generated.is_dir():
        for path in sorted(generated.iterdir()):
            if (path / "task.toml").is_file():
                yield f"gen-{path.name}", path.name, "Generated", path


def examples():
    import tomllib
    rows = []
    for task_id, name, group, path in task_dirs():
        config = tomllib.loads((path / "task.toml").read_text())
        readme = path / "README.md"
        rows.append({"id": task_id, "name": name, "group": group,
                     "description": config.get("task", {}).get("description", ""),
                     "metadata": config.get("metadata", {}),
                     "readme": readme.read_text() if readme.is_file() else "",
                     "instruction": (path / "instruction.md").read_text()})
    return rows


def create_job(task_id, mode="full", task_path=None, *, profile="calibration", metadata=None):
    if mode not in {"full", "controls"}:
        raise ValueError("Mode must be full or controls")
    if profile not in {"calibration", "demo", "search"}:
        raise ValueError("Profile must be calibration, demo, or search")
    if task_path is None:
        sources = {tid: path for tid, _, _, path in task_dirs()}
        if task_id not in sources:
            raise ValueError("Unknown example task")
        source = sources[task_id]
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
              "profile": profile, "metadata": metadata or {},
              "budget": copy.deepcopy({"demo": DEMO_BUDGET, "search": SEARCH_BUDGET}.get(profile)),
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


def summarize(runs, profile="calibration"):
    evaluations = [r for r in runs if r["kind"] == "evaluation"]
    valid = [r for r in evaluations if r["status"] in {"passed", "failed"}]
    passes = sum(r["status"] == "passed" for r in valid)
    band = 1 <= passes <= 3 if len(valid) == 5 else None
    summary = {"passes": passes, "failures": len(valid) - passes, "valid_runs": len(valid),
               "total_runs": 5, "learnable": band if profile in {"calibration", "search"} else None}
    if profile == "demo":
        summary.update(observed_demo_band=band, budget_exhausted=sum(
            r["status"] == "budget_exhausted" for r in evaluations))
    if profile == "search":
        controls = {r["kind"]: r["status"] for r in runs if r["kind"] in {"oracle", "nop"}}
        controls_passed = controls == {"oracle": "passed", "nop": "passed"}
        summary.update(controls_passed=controls_passed,
                       learnable=band if controls_passed else None,
                       inconclusive_runs=sum(r["status"] in TERMINAL - {"passed", "failed"} for r in evaluations),
                       budget_exhausted=sum(r["status"] == "budget_exhausted" for r in evaluations))
    return summary


def job(job_id):
    path = directory(job_id)
    record = read_json(path / "job.json")
    record["runs"] = [read_json(path / "runs" / rid / "state.json") for rid in record["run_ids"]]
    # Live turn counts can be read without waiting for the Harbor process to finish.
    from runner.artifacts import episodes
    for run in record["runs"]:
        budget = read_json(path / "runs" / run["id"] / "budget.json")
        if budget:
            run["budget"] = budget
            run["cost_usd"] = budget["spent_usd"]
            run["reserved_cost_usd"] = budget["reserved_usd"]
        if run["status"] == "running":
            run["turns"] = len(episodes(path / "runs" / run["id"]))
    record["summary"] = summarize(record["runs"], record.get("profile", "calibration"))
    return record


def jobs():
    paths = sorted((DATA / "jobs").glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [job(p.parent.name) for p in paths]
