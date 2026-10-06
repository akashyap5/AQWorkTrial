"""Single durable worker; at most three independent Harbor processes at once."""
import argparse
import ast
import concurrent.futures
import fcntl
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from runner import artifacts
from runner.store import (CONCURRENCY, DATA, EFFORT, JOB_TERMINAL, MODEL, ROOT,
                          TERMINAL, now, read_json, redact, write_json)
from validator.validate import validate_task


def validate(task):
    result = validate_task(str(task))
    for script in [task / "solution" / "solve.sh", task / "tests" / "test.sh"]:
        if script.is_file():
            check = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            if check.returncode:
                result["errors"].append(check.stderr)
    for file in (task / "tests").glob("*.py"):
        try:
            ast.parse(file.read_text(), filename=str(file))
        except (SyntaxError, UnicodeError) as exc:
            result["errors"].append(str(exc))
    result["passed"] = not result["errors"]
    return result


def command(job_dir, run_id, kind):
    args = [str(ROOT / ".venv" / "bin" / "harbor"), "run", "-p", str(job_dir / "task"),
            "-a", "terminus-2" if kind == "evaluation" else kind,
            "--jobs-dir", str(job_dir / "runs" / run_id / "harbor"),
            "--job-name", f"{job_dir.name}-{run_id}", "--n-attempts", "1",
            "--n-concurrent", "1", "--max-retries", "0", "--force-build"]
    if kind == "evaluation":
        args += ["-m", MODEL, "--ak", f"reasoning_effort={EFFORT}"]
    return args


def process_alive(pid, marker):
    if not pid:
        return False
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return result.returncode == 0 and marker in result.stdout


def recover_pid(marker):
    """Reconcile the small window between launching Harbor and saving its PID."""
    result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if str(ROOT / ".venv" / "bin" / "harbor") in line and f"--job-name {marker}" in line:
            return int(line.strip().split(None, 1)[0])
    return None


def execute(job_dir, run_id):
    run_dir = job_dir / "runs" / run_id
    state_path = run_dir / "state.json"
    state = read_json(state_path)
    if state["status"] in TERMINAL:
        return
    marker = f"{job_dir.name}-{run_id}"
    previous_trial = artifacts.trial_directory(run_dir)
    previous_result = read_json(previous_trial / "result.json") if previous_trial else None
    proc = None
    log = None
    if previous_result:
        pass  # Reconcile completed work before making another paid request.
    elif state["status"] == "running":
        if not process_alive(state.get("pid"), marker):
            recovered = recover_pid(marker)
            if recovered:
                state["pid"] = recovered
                write_json(state_path, state)
            else:
                state.update(status="interrupted", error="Worker restarted after an interrupted trial; retained evidence, no automatic paid retry.", completed_at=now())
                write_json(state_path, state)
                return
    elif (job_dir / "cancel").exists():
        state.update(status="cancelled", completed_at=now())
        write_json(state_path, state)
        return
    else:
        args = command(job_dir, run_id, state["kind"])
        env = os.environ.copy()
        env["OPENROUTER_API_BASE"] = read_json(job_dir / "job.json")["api_base"]
        env["PATH"] = str(ROOT / ".venv" / "bin") + os.pathsep + env.get("PATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        state.update(status="running", started_at=now(), command=args)
        write_json(state_path, state)
        log = (run_dir / "console.log").open("a")
        proc = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        state["pid"] = proc.pid
        write_json(state_path, state)
    cancel_at = None
    while not previous_result and (proc.poll() is None if proc else process_alive(state.get("pid"), marker)):
        if (job_dir / "cancel").exists():
            if cancel_at is None:
                cancel_at = time.monotonic()
                try:
                    os.killpg(state["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
            elif time.monotonic() - cancel_at > 30:
                try:
                    os.killpg(state["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        time.sleep(1)
    if log:
        log.close()
    trial = artifacts.trial_directory(run_dir)
    result = read_json(trial / "result.json") if trial else None
    state["completed_at"] = now()
    if state.get("started_at"):
        state["duration_sec"] = round((datetime.fromisoformat(state["completed_at"]) - datetime.fromisoformat(state["started_at"])).total_seconds(), 2)
    state["turns"] = len(artifacts.episodes(run_dir))
    if result:
        state["tests"] = artifacts.test_results(trial)
        state.update(artifacts.classify(result, state["tests"], state["kind"]))
        if state["kind"] == "evaluation":
            oracle = read_json(job_dir / "runs" / "oracle" / "state.json", {})
            if oracle.get("status") == "passed" and sorted(t["name"] for t in oracle["tests"]) != sorted(t["name"] for t in state["tests"]):
                state.update(status="error", passed=None, error="Evaluation collected different tests from the successful oracle; excluded from solve rate.")
        state["usage"] = result.get("agent_result") or {}
        state["trial_directory"] = str(trial)
    else:
        state.update(status="error", error="Harbor exited without a trial result. Inspect the run log.")
    if cancel_at is not None:
        state.update(status="cancelled", passed=None, error="Cancelled by user; excluded from the learnability calculation.")
    write_json(state_path, state)


def skip_pending(job_dir, status="skipped"):
    for path in (job_dir / "runs").glob("*/state.json"):
        state = read_json(path)
        if state["status"] == "queued":
            state.update(status=status, completed_at=now())
            write_json(path, state)


def run_group(job_dir, ids):
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(execute, job_dir, rid): rid for rid in ids}
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                path = job_dir / "runs" / futures[future] / "state.json"
                state = read_json(path)
                state.update(status="error", error=redact(str(exc)), completed_at=now())
                write_json(path, state)


def process_job(job_dir):
    path = job_dir / "job.json"
    record = read_json(path)
    if (job_dir / "cancel").exists():
        active = [rid for rid in record["run_ids"]
                  if read_json(job_dir / "runs" / rid / "state.json")["status"] == "running"]
        run_group(job_dir, active)
        skip_pending(job_dir, "cancelled")
        record.update(status="cancelled", phase="Cancelled", updated_at=now())
        write_json(path, record)
        return
    record.update(status="running", phase="Checking task structure", updated_at=now())
    record["validation"] = validate(job_dir / "task")
    write_json(path, record)
    if not record["validation"]["passed"]:
        record.update(status="validation_failed", phase="Structural validation failed")
        skip_pending(job_dir)
    else:
        record.update(phase="Running oracle and nop in fresh containers")
        write_json(path, record)
        run_group(job_dir, ["oracle", "nop"])
        controls = [read_json(job_dir / "runs" / rid / "state.json") for rid in ["oracle", "nop"]]
        same_tests = sorted(t["name"] for t in controls[0]["tests"]) == sorted(t["name"] for t in controls[1]["tests"])
        record["controls_passed"] = all(c["status"] == "passed" for c in controls) and same_tests
        if (job_dir / "cancel").exists():
            record.update(status="cancelled", phase="Cancelled")
            skip_pending(job_dir, "cancelled")
        elif not record["controls_passed"]:
            record.update(status="validation_failed", phase="Oracle/nop validation failed", error="Controls must both pass with the same nonempty test set before evaluation.")
            skip_pending(job_dir)
        elif record["mode"] == "controls":
            record.update(status="completed", phase="Oracle and nop checks passed")
        elif not os.environ.get("OPENROUTER_API_KEY"):
            record.update(status="error", phase="Model key missing", error="Set OPENROUTER_API_KEY in the worker environment.")
            skip_pending(job_dir)
        else:
            record.update(phase="Running five independent GLM attempts")
            write_json(path, record)
            run_group(job_dir, [f"eval-{n}" for n in range(1, 6)])
            states = [read_json(job_dir / "runs" / f"eval-{n}" / "state.json")["status"] for n in range(1, 6)]
            if (job_dir / "cancel").exists():
                record.update(status="cancelled", phase="Cancelled")
            elif all(s in {"passed", "failed"} for s in states):
                record.update(status="completed", phase="Five runs completed")
            else:
                record.update(status="error", phase="Evaluation contains incomplete or invalid runs", error="Errors/timeouts are excluded from model failures; inspect individual runs.")
    record["updated_at"] = now()
    record["completed_at"] = now()
    write_json(path, record)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Drain the current queue, then exit")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "worker.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A Task Lab worker is already running for this data directory.")
        lock.write(str(os.getpid()))
        lock.flush()
        while True:
            pending = [p for p in (DATA / "jobs").glob("*/job.json") if read_json(p, {}).get("status") not in JOB_TERMINAL]
            pending.sort(key=lambda p: read_json(p)["created_at"])
            if not pending and args.once:
                return
            if not pending:
                time.sleep(1)
                continue
            # Queue submissions safely while Docker Desktop is starting/offline.
            # Cancellation must still work without a Docker daemon.
            if not (pending[0].parent / "cancel").exists():
                try:
                    docker = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                            capture_output=True, text=True, timeout=3)
                    ready = docker.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    ready = False
                if not ready:
                    record = read_json(pending[0])
                    if record.get("phase") != "Waiting for Docker Desktop":
                        record.update(phase="Waiting for Docker Desktop", updated_at=now())
                        write_json(pending[0], record)
                    time.sleep(3)
                    continue
            try:
                process_job(pending[0].parent)
            except Exception as exc:
                record = read_json(pending[0])
                record.update(status="error", phase="Worker error", error=redact(str(exc)), updated_at=now())
                write_json(pending[0], record)
                print(redact(str(exc)), flush=True)


if __name__ == "__main__":
    main()
