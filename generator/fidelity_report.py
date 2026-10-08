"""Repeat-measurement evidence: tasks whose identical content was measured more than once, ranked by consistency.

Probe jobs are grouped by a hash of what the solver and verifier see (instruction, environment, tests, solution);
task.toml labels, verdicts, and READMEs are excluded, so a shipping probe and later unchanged re-runs of the shipped
copy count as one task. Only completed jobs with passing controls and a decided result count.

    python3 generator/fidelity_report.py           # print the ranking
    python3 generator/fidelity_report.py --write   # also write output/fidelity/latest.json for the Task Lab page
"""
import argparse
import collections
import datetime
import glob
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {"task.toml", "verdict.json", ".author.json", "README.md", "gate_decision.json", "withdrawal.json"}


def content_key(snapshot):
    digest = hashlib.sha256()
    for path in sorted(snapshot.rglob("*")):
        rel = path.relative_to(snapshot).as_posix()
        if path.is_file() and rel not in IGNORED:
            digest.update(rel.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def ranking(top=10):
    groups, pending = collections.defaultdict(list), collections.Counter()
    for path in glob.glob(str(ROOT / ".tasklab" / "jobs" / "*" / "job.json")):
        job = json.loads(Path(path).read_text())
        folder = Path(path).parent
        if not (folder / "task").is_dir():
            continue
        slug = job["task_name"].removeprefix("gen-")
        spec_path = ROOT / "output" / "candidates" / slug / ".author.json"
        spec = json.loads(spec_path.read_text()) if spec_path.is_file() else {}
        if not spec or spec.get("family") == "exploration-probe" or spec.get("prompt_version") == "claude-exploration-probe":
            continue  # only GLM-5.1 pipeline tasks
        key = (slug, content_key(folder / "task"))
        if job["status"] in ("queued", "running"):
            pending[key] += 1
            continue
        if job["status"] != "completed" or not job.get("controls_passed"):
            continue
        states = [json.loads(Path(p).read_text())["status"] for p in sorted(glob.glob(str(folder / "runs" / "eval-*" / "state.json")))]
        passes, valid = states.count("passed"), states.count("passed") + states.count("failed")
        if valid < 5 and passes < 4:
            continue  # incomplete and undecided (timeouts or errors)
        groups[key].append({"id": job["id"], "created_at": job["created_at"], "status": job["status"], "passes": passes, "valid": valid,
                            "source": (job.get("metadata") or {}).get("source") or "ui"})
    rows = []
    for (slug, key), runs in groups.items():
        if len(runs) < 2 or not any(1 <= r["passes"] <= 3 and r["valid"] == 5 for r in runs):
            continue  # needs repeat measurements and at least one in-band result
        runs.sort(key=lambda r: r["created_at"])
        band = sum(1 <= r["passes"] <= 3 and r["valid"] == 5 for r in runs)
        spec = json.loads((ROOT / "output" / "candidates" / slug / ".author.json").read_text())
        rows.append({"slug": slug, "content": key, "in_band": band, "measured": len(runs), "pending": pending.get((slug, key), 0),
                     "golden": (ROOT / "golden" / slug).is_dir(), "summary": spec.get("summary", ""),
                     "prompt_version": spec.get("prompt_version"), "runs": runs})
    rows.sort(key=lambda r: (-r["in_band"], -r["in_band"] / r["measured"], -r["measured"]))
    return rows[:top]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    rows = ranking(args.top)
    for r in rows:
        seq = ", ".join(f"{m['passes']}/{m['valid']}" for m in r["runs"])
        print(f"{r['slug'][:42]:42s} {'golden' if r['golden'] else 'new   '} in band {r['in_band']}/{r['measured']} | {seq} | pending {r['pending']}")
    if args.write:
        out = ROOT / "output" / "fidelity"
        out.mkdir(parents=True, exist_ok=True)
        (out / "latest.json").write_text(json.dumps({"written": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "tasks": rows}, indent=1))
