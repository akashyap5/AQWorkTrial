"""Prompt governor: measure every trap-prompt version and regress to the best one when a new prompt underperforms.

Versions that differ only by strategy-library feedback share a base prompt; outcomes are grouped by base prompt.
Signal: the first completed 5-run probe of each GLM-5.1 task authored with that base prompt (in band = 1-3/5).
Rule (fixed): when the current base prompt has at least MIN_N measured tasks and its in-band rate trails the best
measured base prompt (also MIN_N or more) by MARGIN or more, the best base prompt is restored as the prompt file.
Every decision is appended to prompts/fast_author_changelog.md and output/ledger.jsonl.

    python3 generator/prompt_governor.py            # print the per-prompt table
    python3 generator/prompt_governor.py --apply    # also regress if the rule says so
"""
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator import robust  # noqa: E402
from runner import store  # noqa: E402

PROMPT_FILE = ROOT / "prompts" / "trap_author_prompt.txt"
VERSIONS = ROOT / "prompts" / "trap_author_versions"
CANDIDATES = ROOT / "output" / "candidates"
LEARNABLE = ROOT / "output" / "learnable"
MIN_N, MARGIN = 8, 0.15
EARLY_N, EASY_SHARE = 5, 0.6  # fast trigger: a new prompt whose first measured tasks are mostly too easy


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def version_bases():
    bases = {}
    for path in VERSIONS.glob("t*.json"):
        record = json.loads(path.read_text())
        bases[record["version"]] = (digest(record["base_prompt"]), record["base_prompt"])
    return bases


def first_probes():
    """task name -> (prompt version, first completed probe class)"""
    first = {}
    for job_path in store.DATA.glob("jobs/*/job.json"):
        job = json.loads(job_path.read_text())
        if (job.get("metadata") or {}).get("source") != "fast_author":
            continue
        author = CANDIDATES / job["task_name"] / ".author.json"
        if not author.is_file():
            continue
        statuses = []
        for n in range(1, 6):
            state = job_path.parent / "runs" / f"eval-{n}" / "state.json"
            statuses.append(json.loads(state.read_text())["status"] if state.is_file() else "-")
        passed, failed = statuses.count("passed"), statuses.count("failed")
        if passed >= 4:
            cls = "too_easy"  # includes probes stopped early once 4 runs passed
        elif passed + failed == 5:
            cls = "in_band" if passed else "too_hard"
        else:
            continue
        name = job["task_name"]
        if name not in first or job["created_at"] < first[name][0]:
            first[name] = (job["created_at"], json.loads(author.read_text()).get("prompt_version"), cls)
    return {name: (v, cls) for name, (_, v, cls) in first.items()}


def table():
    bases = version_bases()
    groups = defaultdict(lambda: {"versions": set(), "n": 0, "in_band": 0, "too_easy": 0, "too_hard": 0, "shipped": 0, "text": ""})
    for name, (version, cls) in first_probes().items():
        if version not in bases:
            continue
        base, text = bases[version]
        g = groups[base]
        g["versions"].add(version)
        g["text"] = text
        g["n"] += 1
        g[cls] += 1
        g["shipped"] += (LEARNABLE / name).is_dir()
    for g in groups.values():
        g["rate"] = g["in_band"] / g["n"] if g["n"] else 0.0
    return groups


def decide(groups):
    """Return (base prompt to restore, current base prompt). The best measured prompt wins when the current one
    (a) trails it by MARGIN or more after MIN_N tasks, or (b) has a too-easy share of EASY_SHARE or more after
    EARLY_N tasks while the best prompt's in-band rate is higher."""
    current = digest(PROMPT_FILE.read_text())
    measured = {b: g for b, g in groups.items() if g["n"] >= MIN_N}
    if not measured:
        return None, current
    best = max(measured, key=lambda b: (measured[b]["rate"], measured[b]["shipped"]))
    if best == current:
        return None, current
    cur = groups.get(current)
    if cur and cur["n"] >= MIN_N and cur["rate"] <= measured[best]["rate"] - MARGIN:
        return best, current
    if cur and cur["n"] >= EARLY_N and cur["too_easy"] / cur["n"] >= EASY_SHARE and cur["rate"] < measured[best]["rate"]:
        return best, current
    return None, current


def main():
    groups = table()
    for base, g in sorted(groups.items(), key=lambda kv: min(kv[1]["versions"])):
        print(f"{base} {','.join(sorted(g['versions'])):24} n={g['n']:3} in_band={g['in_band']:2} ({g['rate']:.0%}) "
              f"too_easy={g['too_easy']:2} too_hard={g['too_hard']:2} shipped={g['shipped']}")
    best, current = decide(groups)
    print("current base prompt:", current, "| regression:", "to " + best if best else "none")
    if best and "--apply" in sys.argv:
        g, c = groups[best], groups[current]
        PROMPT_FILE.write_text(g["text"])
        note = (f"Prompt regression (automatic): restored the base prompt of {','.join(sorted(g['versions']))} "
                f"({g['in_band']}/{g['n']} in band, {g['rate']:.0%}) over {','.join(sorted(c['versions']))} "
                f"({c['in_band']}/{c['n']}, {c['rate']:.0%}, too easy {c['too_easy']}/{c['n']}); rules: n>={MIN_N} and a gap of {MARGIN:.0%} or more, "
                f"or n>={EARLY_N} with a too-easy share of {EASY_SHARE:.0%} or more.")
        with (ROOT / "prompts" / "fast_author_changelog.md").open("a") as f:
            f.write(f"| {time.strftime('%H:%M')} | {note} | Measured first-probe outcomes per base prompt. |\n")
        robust.ledger("prompt_regression", restored=sorted(g["versions"]), replaced=sorted(c["versions"]),
                      restored_rate=g["rate"], replaced_rate=c["rate"])
        print(note)


if __name__ == "__main__":
    main()
