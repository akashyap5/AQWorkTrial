"""Fairness-gate sweeper: every learnable GLM task must carry a GLM-5.1 fairness audit.

Tasks accepted by generator processes started before the gate existed are audited here (majority of 3 audits on
their recorded probe job); unfair ones are withdrawn from output/learnable and get the auditor's clarification for a
re-probe. Runs until interrupted; safe to run alongside generators (file locks).
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402


def needs_gate(task_dir):
    toml = (task_dir / "task.toml").read_text()
    if "glm" not in toml or 'family = "exploration-probe"' in toml:
        return None
    match = re.search(r'probe_job = "([0-9a-f]+)"', toml)
    if not match or fa.gate_decision(task_dir.name, match.group(1)):
        return None  # decided under the current policy (output/gate_decisions.jsonl); decisions are final
    verdict = task_dir / "verdict.json"
    if verdict.is_file() and json.loads(verdict.read_text()).get("gate_policy") == fa.GATE_POLICY:
        return None
    return match.group(1)


def unaccepted_in_band():
    """Newest completed GLM probe per task that landed at 1-3/5 but never reached output/learnable or a gate decision."""
    latest = {}
    for job_path in fa.store.DATA.glob("jobs/*/job.json"):
        job = json.loads(job_path.read_text())
        if (job.get("metadata") or {}).get("source") != "fast_author":
            continue
        name = job["task_name"]
        if name not in latest or job["created_at"] > latest[name]["created_at"]:
            latest[name] = job
    for name, job in sorted(latest.items()):
        cand = fa.CANDIDATES / name
        if job.get("status") != "completed" or not job.get("controls_passed") or not (cand / ".author.json").is_file():
            continue
        if (fa.LEARNABLE / name).exists() or "glm" not in (cand / "task.toml").read_text():
            continue
        spec = json.loads((cand / ".author.json").read_text())
        if spec.get("family") == "exploration-probe" or spec.get("prompt_version") == "claude-exploration-probe":
            continue  # Claude-authored exploration probes are never shipped
        if fa.gate_decision(name, job["id"]):
            continue  # already decided; the first decision on a probe job is final
        verdict = cand / "verdict.json"
        if verdict.is_file():
            v = json.loads(verdict.read_text())
            if v.get("probe_job") == job["id"] and v.get("gate_policy") == fa.GATE_POLICY:
                continue  # already gated for this job under the final policy
        states = [json.loads((job_path_dir / "state.json").read_text())["status"]
                  for job_path_dir in sorted((fa.store.DATA / "jobs" / job["id"] / "runs").glob("eval-*"))]
        if states.count("passed") + states.count("failed") == 5 and 1 <= states.count("passed") <= 3:
            yield name, job["id"]


def reconcile_learnable():
    """A shipped task needs an 'accepted' first gate decision on its recorded probe job; anything else is withdrawn."""
    for toml in sorted(fa.LEARNABLE.glob("*/task.toml")):
        match = re.search(r'probe_job = "([0-9a-f]+)"', toml.read_text())
        decision = fa.gate_decision(toml.parent.name, match.group(1)) if match else None
        if decision and decision["decision"] != "accepted":
            fa.withdraw(toml.parent.name, f"the gate's first decision on probe job {match.group(1)} was '{decision['decision']}' "
                                          f"({decision.get('decided_at') or decision.get('written')}); a later re-audit of the same job does not count")


CONFIRMATIONS = ROOT / "output" / "confirmations.jsonl"


def confirm_new_shipments():
    """Queue three unchanged re-runs of every newly shipped task outside the frozen golden set (results stay out of the UI)."""
    golden = {p.name for p in (ROOT / "golden").iterdir()} if (ROOT / "golden").is_dir() else set()
    queued = {json.loads(line)["slug"] for line in CONFIRMATIONS.read_text().splitlines()} if CONFIRMATIONS.is_file() else set()
    for toml in sorted(fa.LEARNABLE.glob("*/task.toml")):
        slug = toml.parent.name
        if slug in golden or slug in queued:
            continue
        jobs = []
        for n in (1, 2, 3):
            job = fa.store.create_job(slug, "full", toml.parent, profile="calibration",
                                      metadata={"display_name": f"{slug} (confirmation {n})", "source": "confirmation"})
            jobs.append(job["id"] if isinstance(job, dict) else job)
        with fa.robust.file_lock(CONFIRMATIONS.with_name(".confirmations.lock")):
            with CONFIRMATIONS.open("a") as f:
                f.write(json.dumps({"slug": slug, "jobs": jobs, "queued": fa.robust.now()}) + "\n")
        fa.log(slug, f"confirmation: queued 3 unchanged re-runs {[j[:8] for j in jobs]}")


def held_awaiting_reprobe():
    """Tasks whose newest probe was held by the gate: the clarified instruction has not been measured yet."""
    path = fa._gate_store()
    held = {}
    for line in (path.read_text().splitlines() if path.is_file() else []):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("decision") == "held" and entry.get("fix") and entry.get("gate_policy") == fa.GATE_POLICY:
            held.setdefault(entry["slug"], set()).add(entry["probe_job"])
    for slug, jobs in sorted(held.items()):
        if not (fa.LEARNABLE / slug).exists() and fa.latest_probe_job(slug) in jobs:
            yield slug


def reprobe_running(slug):
    return subprocess.run(["pgrep", "-f", f"reprobe {slug} --max-edits"], capture_output=True).returncode == 0


if __name__ == "__main__":
    import os
    import subprocess
    once = "--once" in sys.argv
    launched = {}
    loops = 0
    while True:
        loops += 1
        if loops % 5 == 1:
            # Best stretch of 20 consecutive probed tasks; frozen once it reaches 40% (generator/window_report.py).
            subprocess.run([sys.executable, str(ROOT / "generator" / "fidelity_report.py"), "--write"], cwd=ROOT, capture_output=True)
            for size in ("10", "20"):
                subprocess.run([sys.executable, str(ROOT / "generator" / "window_report.py"), "--freeze", "--size", size], cwd=ROOT, capture_output=True)
        # Docker has ~31 address pools; networks leaked by cancelled trials make new runs error out ("collected different
        # tests"). Remove unused networks older than 5 minutes so a trial that is just starting keeps its network.
        subprocess.run(["docker", "network", "prune", "-f", "--filter", "until=5m"], capture_output=True)
        reconcile_learnable()
        for task_dir in sorted(fa.LEARNABLE.glob("*/task.toml")):
            job = needs_gate(task_dir.parent)
            if job:
                print(json.dumps(fa.gate(task_dir.parent.name, job), default=str), flush=True)
        for name, job in unaccepted_in_band():
            print(json.dumps(fa.gate(name, job), default=str), flush=True)
        confirm_new_shipments()
        for name in held_awaiting_reprobe():
            # Whoever made the hold decision (a batch slot, a re-probe or this sweeper), measure the clarified task once.
            if time.time() - launched.get(name, 0) < 1800 or reprobe_running(name):
                continue
            launched[name] = time.time()
            subprocess.Popen([sys.executable, "-u", str(ROOT / "generator" / "fast_author.py"), "--reprobe", name, "--max-edits", "1"],
                             cwd=ROOT, stdout=open(ROOT / "output" / ".probe-logs" / f"reprobe-{name}.log", "a"),
                             stderr=subprocess.STDOUT, start_new_session=True, env=os.environ.copy())
            fa.log(name, "re-probe launched for the clarified task")
        if once:
            break
        time.sleep(60)
