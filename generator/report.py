"""Part 3 pipeline metrics, computed only from records (batch state, ledger, Task Lab job records, Harbor results).

    python3 generator/report.py <batch-id> [<batch-id> ...]     # specific batches
    python3 generator/report.py --since 13:40                     # every batch started today at or after a time

For each task slot: authored, structural validation (the repository validator), functional correctness (reference
passes and the stubbed starter fails in Docker; oracle passes and no-op fails on the Task Lab worker), the measured
pass distribution, the fairness-gate decision, GLM-5.1 spend (ledger llm_call events), solver spend (Harbor
agent_result.cost_usd), and wall time. Writes reports/pipeline_metrics.md and reports/pipeline_metrics.json.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runner import store  # noqa: E402
from validator.validate import validate_task  # noqa: E402

BATCHES = ROOT / "output" / "batches"
CANDIDATES = ROOT / "output" / "candidates"
LEARNABLE = ROOT / "output" / "learnable"
LEDGER = ROOT / "output" / "ledger.jsonl"


def ledger_events():
    events = []
    if LEDGER.is_file():
        for line in LEDGER.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    return events


def jobs_by_task():
    jobs = {}
    for path in store.DATA.glob("jobs/*/job.json"):
        try:
            job = json.loads(path.read_text())
        except ValueError:
            continue
        jobs.setdefault(job["task_name"], []).append(job)
    for name in jobs:
        jobs[name].sort(key=lambda j: j["created_at"])
    return jobs


def run_outcomes(job):
    statuses, cost = [], 0.0
    for n in range(1, 6):
        run_dir = store.DATA / "jobs" / job["id"] / "runs" / f"eval-{n}"
        try:
            statuses.append(json.loads((run_dir / "state.json").read_text())["status"])
        except (OSError, ValueError):
            statuses.append("missing")
        for result in run_dir.glob("harbor/*/*/result.json"):
            try:
                cost += float((json.loads(result.read_text()).get("agent_result") or {}).get("cost_usd") or 0)
            except ValueError:
                pass
    return statuses, cost


def classify(statuses):
    passed, failed = statuses.count("passed"), statuses.count("failed")
    if passed + failed < 5:
        return "inconclusive"
    return "too_hard" if passed == 0 else "learnable_range" if passed <= 3 else "too_easy"


def task_row(slot, jobs, events, created):
    slug = slot.get("slug")
    row = {"slot": slot["index"], "variant": slot.get("variant"), "slug": slug, "authored": bool(slug),
           "outcome": slot.get("outcome"), "status": slot.get("status")}
    if not slug:
        return row
    cand = CANDIDATES / slug
    if (cand / "task.toml").is_file():
        row["structural_valid"] = bool(validate_task(str(cand)).get("passed")) and not validate_task(str(cand)).get("errors")
    row["docker_valid"] = any(e.get("slug") == slug and str(e.get("message", "")).startswith("VALID") for e in events)
    task_jobs = [j for j in jobs.get(slug, []) if j["created_at"] >= created]
    row["probes"] = []
    solver_cost = 0.0
    for job in task_jobs:
        statuses, cost = run_outcomes(job)
        solver_cost += cost
        row["probes"].append({"job": job["id"], "status": job.get("status"), "controls_ok": job.get("controls_passed"),
                              "passes": statuses.count("passed"), "completed": statuses.count("passed") + statuses.count("failed"),
                              "class": classify(statuses)})
    done = [p for p in row["probes"] if p["status"] == "completed" or p["completed"] == 5]
    row["functionally_correct"] = any(p["controls_ok"] for p in row["probes"])
    row["first_probe_class"] = done[0]["class"] if done else None
    row["final_probe_class"] = done[-1]["class"] if done else None
    row["ever_in_range"] = any(p["class"] == "learnable_range" and p["controls_ok"] for p in done)
    verdict = cand / "verdict.json"
    row["gate"] = json.loads(verdict.read_text()).get("decision") if verdict.is_file() else None
    row["shipped"] = (LEARNABLE / slug).is_dir()
    row["glm_cost_usd"] = round(sum(float(e.get("cost_usd") or 0) for e in events if e.get("event") == "llm_call" and e.get("slug") == slug), 4)
    row["solver_cost_usd"] = round(solver_cost, 4)
    stamps = [e["ts"] for e in events if e.get("slug") == slug]
    if stamps:
        t0 = datetime.strptime(min(stamps)[:19], "%Y-%m-%dT%H:%M:%S")
        t1 = datetime.strptime(max(stamps)[:19], "%Y-%m-%dT%H:%M:%S")
        row["minutes"] = round((t1 - t0).total_seconds() / 60, 1)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("batches", nargs="*")
    parser.add_argument("--since", default="")
    args = parser.parse_args()
    today = datetime.now().strftime("%Y%m%d")
    ids = list(args.batches)
    if args.since:
        hhmm = args.since.replace(":", "")
        ids += sorted(p.name for p in BATCHES.glob(f"batch-{today}-*") if p.name.split("-")[2][:4] >= hhmm)
    events, jobs = ledger_events(), jobs_by_task()
    report = {"generated": datetime.now().isoformat(timespec="seconds"), "batches": []}
    for batch_id in ids:
        state = json.loads((BATCHES / batch_id / "state.json").read_text())
        created = datetime.strptime(batch_id.split("-")[1] + batch_id.split("-")[2], "%Y%m%d%H%M%S").astimezone().isoformat()
        created_utc = datetime.fromisoformat(created).astimezone().astimezone(tz=None)
        rows = [task_row(slot, jobs, events, store_time(created_utc)) for slot in state["slots"]]
        report["batches"].append({"batch": batch_id, "args": {k: state["args"].get(k) for k in ("families", "count", "start", "max_edits")}, "tasks": rows})
    all_rows = [r for b in report["batches"] for r in b["tasks"]]
    summary = {
        "slots": len(all_rows),
        "authored": sum(r["authored"] for r in all_rows),
        "structural_valid": sum(bool(r.get("structural_valid")) for r in all_rows),
        "docker_valid": sum(bool(r.get("docker_valid")) for r in all_rows),
        "functionally_correct": sum(bool(r.get("functionally_correct")) for r in all_rows),
        "first_probe": dict(Counter(r.get("first_probe_class") for r in all_rows if r.get("first_probe_class"))),
        "ever_in_learnable_range": sum(bool(r.get("ever_in_range")) for r in all_rows),
        "shipped_after_gate": sum(bool(r.get("shipped")) for r in all_rows),
        "glm_cost_usd": round(sum(r.get("glm_cost_usd") or 0 for r in all_rows), 2),
        "solver_cost_usd": round(sum(r.get("solver_cost_usd") or 0 for r in all_rows), 2),
        "median_minutes": sorted(r["minutes"] for r in all_rows if r.get("minutes") is not None)[len([r for r in all_rows if r.get("minutes") is not None]) // 2]
        if any(r.get("minutes") is not None for r in all_rows) else None,
    }
    if ids:  # GLM-5.1 spend in the batch window from the raw call records (covers calls made before per-task logging)
        import ast, os, re
        start = min(datetime.strptime(i.split("-")[1] + i.split("-")[2], "%Y%m%d%H%M%S").timestamp() for i in ids)
        window = 0.0
        for raw in (ROOT / "output" / ".fast-author" / "raw").glob("*.txt"):
            if os.path.getmtime(raw) < start:
                continue
            match = re.search(r"usage=(\{.*\})", raw.open().readline())
            try:
                window += float(ast.literal_eval(match.group(1)).get("cost") or 0) if match else 0
            except (ValueError, SyntaxError):
                pass
        summary["glm_cost_usd_window"] = round(window, 2)
    report["summary"] = summary
    out = ROOT / "reports"
    (out / "pipeline_metrics.json").write_text(json.dumps(report, indent=1, default=str))
    lines = ["# Pipeline metrics", "", f"Generated {report['generated']} from batch state, the ledger, Task Lab job records, and Harbor results.", "",
             "| Metric | Value |", "|---|---|"]
    for key, label in (("slots", "Task slots"), ("authored", "Authored by GLM-5.1"), ("structural_valid", "Pass structural validation"),
                       ("docker_valid", "Functionally correct in Docker (reference passes, starter fails)"),
                       ("functionally_correct", "Oracle passes and no-op fails on the Task Lab worker"),
                       ("first_probe", "First 5-run probe (too_easy / learnable_range / too_hard / inconclusive)"),
                       ("ever_in_learnable_range", "Landed at 1-3/5 in some probe"), ("shipped_after_gate", "Shipped after the GLM-5.1 fairness gate"),
                       ("glm_cost_usd", "GLM-5.1 spend attributed per task (ledger) USD"),
                       ("glm_cost_usd_window", "GLM-5.1 spend in the batch window, all calls (raw records) USD"), ("solver_cost_usd", "GLM-5.3-flash solver spend USD"),
                       ("median_minutes", "Median minutes per task (first to last event)")):
        lines.append(f"| {label} | {summary.get(key)} |")
    for b in report["batches"]:
        lines += ["", f"## {b['batch']}", "", "| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in b["tasks"]:
            probes = ", ".join(f"{p['passes']}/{p['completed']}" for p in r.get("probes", [])) or "-"
            lines.append(f"| {r['slot']} | {r.get('slug') or '(not authored)'} | {r.get('structural_valid', '-')} | {r.get('functionally_correct', '-')} | {probes} | {r.get('gate') or '-'} | {r.get('shipped', False)} | {r.get('glm_cost_usd', '-')} | {r.get('solver_cost_usd', '-')} | {r.get('minutes', '-')} |")
    (out / "pipeline_metrics.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, indent=1))


def store_time(dt):
    """Task Lab job timestamps are UTC ISO strings; compare in the same format."""
    from datetime import timezone
    return dt.astimezone(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
