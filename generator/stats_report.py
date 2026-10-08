"""Headline results, recomputed from the records into output/stats/latest.json (shown on the Task Lab Results tab).

    python3 generator/stats_report.py [--spend 69.49]   # --spend: total API spend in USD, read from the key's usage endpoint
"""
import argparse
import datetime
import glob
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator import window_report as wr  # noqa: E402
from generator import fidelity_report as fr  # noqa: E402


def champion_lineage():
    """Prompt versions that share the t005 instructions (the champion prompt)."""
    base = json.loads((ROOT / "prompts" / "trap_author_versions" / "t005.json").read_text())["base_prompt"]
    return {json.loads(Path(p).read_text())["version"] for p in glob.glob(str(ROOT / "prompts" / "trap_author_versions" / "t*.json"))
            if json.loads(Path(p).read_text()).get("base_prompt") == base}


def pipeline_spend(shipped):
    """Spend since the ledger began (12:30, the final pipeline): GLM-5.1 calls from the ledger, solver attempts from Harbor results."""
    lines = [json.loads(line) for line in (ROOT / "output" / "ledger.jsonl").read_text().splitlines()]
    since = min(e["ts"] for e in lines)
    since_utc = datetime.datetime.strptime(since, "%Y-%m-%dT%H:%M:%S%z").astimezone(datetime.timezone.utc).isoformat()
    glm = sum(float(e.get("cost_usd") or 0) for e in lines if e.get("event") == "llm_call")
    solver = 0.0
    for job_path in glob.glob(str(ROOT / ".tasklab" / "jobs" / "*" / "job.json")):
        if json.loads(Path(job_path).read_text())["created_at"] < since_utc:
            continue
        for result in glob.glob(str(Path(job_path).parent / "runs" / "eval-*" / "harbor" / "*" / "*" / "result.json")):
            try:
                solver += float((json.loads(Path(result).read_text()).get("agent_result") or {}).get("cost_usd") or 0)
            except (OSError, ValueError):
                pass
    return {"pipeline_since": since, "pipeline_glm_usd": round(glm, 2), "pipeline_solver_usd": round(solver, 2),
            "cost_per_shipped_pipeline_usd": round((glm + solver) / shipped, 2) if shipped else None}


def best(tasks, size):
    wr.SIZE = size
    window = wr.best_window(tasks)
    return {"size": size, "shipped": window["shipped"], "in_band_first": window["in_band_first"],
            "start": window["start"], "end": window["end"]} if window else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spend", type=float, default=None, help="total API spend in USD (OpenRouter key usage)")
    args = parser.parse_args()
    tasks = wr.sequence()
    champion = [t for t in tasks if t["prompt_version"] in champion_lineage()]
    measured = [t for t in champion if t["first_outcome"] in ("in_band", "too_easy", "too_hard")]
    shipped = sorted(p.name for p in (ROOT / "output" / "learnable").iterdir() if (p / "task.toml").is_file())
    valid = sum(subprocess.run([sys.executable, str(ROOT / "validator" / "validate.py"), str(ROOT / "output" / "learnable" / s)],
                               capture_output=True).returncode == 0 for s in shipped)
    repeats = fr.ranking(top=50)
    stats = {
        "written": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "best_window_10": best(tasks, 10),
        "best_window_20_champion": best(champion, 20),
        "champion_first_probes": {"measured": len(measured), "in_band": sum(t["first_outcome"] == "in_band" for t in measured)},
        "shipped": len(shipped), "shipped_pass_validator": valid,
        "repeat_verified": sum(r["in_band"] == r["measured"] and r["measured"] >= 3 for r in repeats),
        "repeat_measured": len(repeats),
        "spend_usd": args.spend,
        **pipeline_spend(len(shipped)),
        "cost_per_shipped_usd": round(args.spend / len(shipped), 2) if args.spend and shipped else None,
        "domains": len(shipped),
    }
    out = ROOT / "output" / "stats"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))
