"""Best stretch of 20 consecutive probed tasks, by share shipped through the fairness gate.

Tasks are GLM-5.1 candidates ordered by when their first probe started; a window is 20 consecutive tasks (it may span
batches and prompt versions). A task counts as shipped when the gate accepted it (output/gate_decisions.jsonl), on its
first probe or after the pipeline's own edits. Tasks still in flight count as not shipped yet, so a frozen window can
only be an underestimate. Tasks that failed validation before any probe are not in the sequence.

    python3 generator/window_report.py            # print the best window and the overall rate
    python3 generator/window_report.py --freeze   # also write output/window/latest.json and, when the best window
                                                  # reaches 40% and beats the frozen one, output/window/best.json
"""
import argparse
import datetime
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runner import store  # noqa: E402

SIZE, FREEZE_AT = 20, 0.40
OUT = ROOT / "output" / "window"


def jobs_by_task():
    by = {}
    for path in glob.glob(str(store.DATA / "jobs" / "*" / "job.json")):
        job = json.loads(Path(path).read_text())
        states = [json.loads(Path(p).read_text())["status"] for p in sorted(glob.glob(str(Path(path).parent / "runs" / "eval-*" / "state.json")))]
        job["_passes"], job["_valid"] = states.count("passed"), states.count("passed") + states.count("failed")
        by.setdefault(job["task_name"], []).append(job)
    return by


def sequence():
    """Every probed GLM-5.1 candidate with its first probe, final state, and whether the gate shipped it."""
    accepted = {}
    for line in (ROOT / "output" / "gate_decisions.jsonl").read_text().splitlines():
        entry = json.loads(line)
        if entry.get("decision") == "accepted":
            accepted.setdefault(entry["slug"], entry)
    tasks = []
    for name, jobs in jobs_by_task().items():
        probes = sorted((j for j in jobs if (j.get("metadata") or {}).get("source") == "fast_author"), key=lambda j: j["created_at"])
        spec_path = ROOT / "output" / "candidates" / name / ".author.json"
        if not probes or not spec_path.is_file():
            continue
        spec = json.loads(spec_path.read_text())
        if spec.get("family") == "exploration-probe" or spec.get("prompt_version") == "claude-exploration-probe":
            continue
        first = probes[0]
        if first["status"] not in store.JOB_TERMINAL:
            first_outcome = "pending"
        elif not first.get("controls_passed"):
            first_outcome = "controls_failed"
        elif first["_passes"] >= 4:
            first_outcome = "too_easy"
        elif first["_valid"] < 5:
            first_outcome = "inconclusive"
        else:
            first_outcome = "too_hard" if first["_passes"] == 0 else "in_band"
        in_flight = any(j["status"] not in store.JOB_TERMINAL for j in probes)
        tasks.append({"slug": name, "first_probe": first["id"], "first_at": first["created_at"], "first_outcome": first_outcome,
                      "first_passes": first["_passes"], "family": spec.get("family"), "prompt_version": spec.get("prompt_version"),
                      "summary": spec.get("summary", ""), "shipped": name in accepted,
                      "shipped_job": accepted.get(name, {}).get("probe_job"), "in_flight": in_flight,
                      "probes": [{"id": j["id"], "created_at": j["created_at"], "status": j["status"], "passes": j["_passes"],
                                  "valid": j["_valid"]} for j in probes]})
    return sorted(tasks, key=lambda t: t["first_at"])


def best_window(tasks, since=None):
    pool = [t for t in tasks if not since or t["first_at"] >= since]
    best = None
    for i in range(0, max(0, len(pool) - SIZE + 1)):
        window = pool[i:i + SIZE]
        shipped = sum(t["shipped"] for t in window)
        band = sum(t["first_outcome"] == "in_band" for t in window)
        key = (shipped, band, window[-1]["first_at"])
        if best is None or key > best["key"]:
            best = {"key": key, "shipped": shipped, "in_band_first": band, "start": window[0]["first_at"],
                    "end": window[-1]["first_at"], "tasks": window}
    return best


def overall(tasks, since=None):
    pool = [t for t in tasks if not since or t["first_at"] >= since]
    return {"tasks": len(pool), "shipped": sum(t["shipped"] for t in pool),
            "in_band_first": sum(t["first_outcome"] == "in_band" for t in pool),
            "in_flight": sum(t["in_flight"] for t in pool)}


def snapshot(window, tasks, label):
    return {"label": label, "written": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "size": SIZE, "shipped": window["shipped"], "rate": window["shipped"] / SIZE,
            "in_band_first": window["in_band_first"], "start": window["start"], "end": window["end"],
            "overall": overall(tasks), "tasks": window["tasks"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--since", default="", help="only windows whose tasks started probing after this ISO time")
    parser.add_argument("--size", type=int, default=SIZE, help="tasks per window (10 matches the README's batch of 10)")
    args = parser.parse_args()
    SIZE = args.size
    tasks = sequence()
    window = best_window(tasks, args.since or None)
    print(f"probed tasks: {overall(tasks, args.since or None)}")
    if not window:
        print(f"fewer than {SIZE} probed tasks")
        sys.exit(0)
    print(f"best {SIZE}-task window: {window['shipped']}/{SIZE} shipped ({window['shipped'] / SIZE:.0%}), "
          f"{window['in_band_first']}/{SIZE} in band on the first probe, {window['start'][11:16]}–{window['end'][11:16]} UTC")
    if args.freeze:
        OUT.mkdir(parents=True, exist_ok=True)
        latest = snapshot(window, tasks, "best window so far (not frozen)")
        (OUT / f"latest-{SIZE}.json").write_text(json.dumps(latest, indent=1))
        best_path = OUT / f"best-{SIZE}.json"
        frozen = json.loads(best_path.read_text()) if best_path.is_file() else None
        if latest["rate"] >= FREEZE_AT and (not frozen or latest["shipped"] > frozen["shipped"]):
            latest["label"] = f"frozen best window ({latest['shipped']}/{SIZE} shipped)"
            best_path.write_text(json.dumps(latest, indent=1))
            print(f"FROZE new best window: {latest['shipped']}/{SIZE}")
