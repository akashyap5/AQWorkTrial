"""Champion/challenger prompt iteration for the checklist family.

The champion is prompts/trap_author_prompt.txt (family everyday-trap); the challenger is
prompts/trap_author_prompt_x.txt (family everyday-trap-x). Both arms author from the same briefs in paired batch
slots. GLM-5.1 writes each challenger from measured evidence: first-probe results per arm, the fairness gate's
clarifications, the lever library, the solver profile, and the rule-count dial's outcomes.

    python3 generator/prompt_reviser.py --new        # write a challenger now
    python3 generator/prompt_reviser.py --decide     # apply the rule below (run by the batch keeper before each batch)
    python3 generator/prompt_reviser.py --report     # per-arm results since the A/B window started

Rule: once each arm has 8 completed first probes in the window (the challenger counts only since it was written),
promote the challenger if its in-band rate is at least 15 points higher, or replace it with a new GLM-5.1 revision
if it is at least 10 points lower. The FAIRNESS and OUTPUT FORMAT sections are always kept verbatim.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

CHAMPION = ROOT / "prompts" / "trap_author_prompt.txt"
CHALLENGER = ROOT / "prompts" / "trap_author_prompt_x.txt"
STATE = ROOT / "prompts" / "ab_state.json"
ARMS = {"champion": "everyday-trap", "challenger": "everyday-trap-x"}
MIN_N, PROMOTE, RETIRE = 8, 0.15, 0.10


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"window_start": now(), "challenger_since": None, "history": []}


def save_state(state):
    fa.robust.write_json_atomic(STATE, state)


def first_probes(family, since):
    """Outcome of the first probe of every task of `family` whose first probe started after `since`."""
    first = {}
    for path in glob.glob(str(fa.store.DATA / "jobs" / "*" / "job.json")):
        job = json.loads(Path(path).read_text())
        if (job.get("metadata") or {}).get("source") != "fast_author":
            continue
        name = job["task_name"]
        if name not in first or job["created_at"] < first[name]["created_at"]:
            first[name] = job
    rows = {}
    cutoff = datetime.datetime.fromisoformat(since)
    for name, job in first.items():
        spec = fa.CANDIDATES / name / ".author.json"
        if not spec.is_file() or json.loads(spec.read_text()).get("family") != family:
            continue
        if datetime.datetime.fromisoformat(job["created_at"]) < cutoff:
            continue
        states = [json.loads(Path(p).read_text())["status"] for p in sorted(glob.glob(str(Path(path).parent.parent / job["id"] / "runs" / "eval-*" / "state.json")))]
        passes, valid = states.count("passed"), states.count("passed") + states.count("failed")
        if job["status"] not in fa.store.JOB_TERMINAL:
            outcome = "pending"
        elif passes >= 4:
            outcome = "too_easy"
        elif valid < 5:
            outcome = "inconclusive"
        else:
            outcome = "too_hard" if passes == 0 else "in_band"
        rows[name] = outcome
    return rows


def summary(rows):
    done = [o for o in rows.values() if o in ("in_band", "too_easy", "too_hard")]
    band = sum(o == "in_band" for o in done)
    return {"measured": len(done), "in_band": band, "rate": band / len(done) if done else None,
            "too_easy": sum(o == "too_easy" for o in done), "too_hard": sum(o == "too_hard" for o in done),
            "pending": sum(o == "pending" for o in rows.values())}


def evidence(state):
    """Measured evidence for the reviser (all from pipeline records)."""
    lines = []
    for arm, family in ARMS.items():
        since = state["window_start"] if arm == "champion" else (state.get("challenger_since") or state["window_start"])
        lines.append(f"{arm} ({family}) since {since}: {summary(first_probes(family, since))}")
    holds = [json.loads(l) for l in (ROOT / "output" / "gate_decisions.jsonl").read_text().splitlines()]
    fixes = [h["fix"] for h in holds if h.get("decision") == "held" and h.get("fix")][-20:]
    lib = json.loads((ROOT / "prompts" / "strategies.json").read_text())
    levers = sorted(lib.get("strategies", []), key=lambda s: -(s.get("stats") or {}).get("tasks", 0))[:10]
    lever_text = "\n".join(f"- {s['id']} {s['name']} [{(s.get('stats') or {}).get('passes', 0)}/{(s.get('stats') or {}).get('runs', 0)} solver runs passed]: {s['how'][:500]}" for s in levers)
    profiles = sorted(glob.glob(str(ROOT / "prompts" / "solver_profiles" / "*")))
    profile = Path(profiles[-1]).read_text()[:4000] if profiles else ""
    dial = [l.strip() for l in (fa.RUNS / "log.txt").read_text().splitlines() if "rule-count dial:" in l or (": probe 1:" in l)][-12:]
    return (f"FIRST-PROBE RESULTS IN THE CURRENT A/B WINDOW:\n" + "\n".join(lines) +
            "\n\nHISTORY: across 15 champion versions, first probes landed in 1-3/5 about 36-44% of the time; "
            "43% were too easy (4-5/5) and 21% were 0/5, mostly because a tested output format was never stated "
            "(every run missed the same assertion). The fairness gate accepts about 88% of in-band tasks from this prompt.\n\n"
            "BEHAVIOURS THE GATE HAD TO ADD BECAUSE TESTS CHECKED THEM WITHOUT THE INSTRUCTION STATING THEM (recent):\n- " + "\n- ".join(fixes) +
            "\n\nLEVER LIBRARY (most used; pass counts are solver runs of tasks that used the lever):\n" + lever_text +
            "\n\nSOLVER PROFILE (latest, written by GLM-5.1 from recorded runs):\n" + profile +
            "\n\nRULE-COUNT DIAL LOG (adding stated rules to too-easy tasks):\n" + "\n".join(dial))


SYSTEM = """You improve the system prompt that GLM-5.1 uses to author coding tasks for a benchmark. A task is learnable
when 1 to 3 of 5 independent runs of the solver (GLM-5.3-flash, high reasoning, terminal agent) pass all hidden tests.
Too easy (4-5/5) and too hard (0/5) tasks are wasted, and a failure only counts if it traces to a requirement the
instruction states plainly. Revise the prompt to raise the share of tasks that land in 1-3/5 while keeping every task
fair, easy for a careful human, and short. Use only the measured evidence you are given."""


def write_challenger(note=""):
    state = load_state()
    base = CHAMPION.read_text()
    fairness = base[base.index("FAIRNESS (strict):"):base.index("DESIGN:")]
    output = base[base.index("OUTPUT FORMAT."):]
    user = (f"CURRENT PROMPT (the champion):\n<<<PROMPT\n{base}\nPROMPT>>>\n\n{evidence(state)}\n\n"
            "TASK: write a revised prompt. Keep the FAIRNESS section and everything from 'OUTPUT FORMAT.' to the end "
            "exactly as they are. Change only the guidance on what tasks to design and how to place difficulty. Return "
            "the complete revised prompt between <<<PROMPT and PROMPT>>>, then one short paragraph explaining the change.")
    fa.CURRENT.slug = "prompt-reviser"
    text = fa.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], tag="prompt-reviser", raw_text=True)
    match = re.search(r"<<<PROMPT\s*(.*?)\s*PROMPT>>>", text, re.S)
    if not match:
        raise SystemExit("reviser returned no prompt block")
    revised = match.group(1).strip() + "\n"
    rationale = text[match.end():].strip()[:1200]
    # The pipeline parses the output blocks and the gate relies on the fairness rules: keep both verbatim.
    if "OUTPUT FORMAT." in revised:
        revised = revised[:revised.index("OUTPUT FORMAT.")]
    if fairness not in revised:
        head, _, tail = revised.partition("DESIGN:")
        head = re.sub(r"FAIRNESS \(strict\):.*", "", head, flags=re.S)
        revised = head.rstrip() + "\n\n" + fairness + ("DESIGN:" + tail if tail else "")
    revised = revised.rstrip() + "\n\n" + output
    CHALLENGER.write_text(revised)
    state["challenger_since"] = now()
    state["history"].append({"at": state["challenger_since"], "event": "new challenger", "note": note, "rationale": rationale,
                             "chars": len(revised)})
    save_state(state)
    fa.robust.ledger("prompt_challenger", note=note, rationale=rationale, chars=len(revised))
    with (ROOT / "prompts" / "fast_author_changelog.md").open("a") as f:
        f.write(f"| {datetime.datetime.now().strftime('%H:%M')} | New GLM-5.1 challenger prompt ({note or 'from measured evidence'}): "
                f"{rationale[:300].replace('|', '/').replace(chr(10), ' ')} |\n")
    print(f"challenger written ({len(revised)} chars): {rationale[:400]}")


def decide():
    state = load_state()
    if not CHALLENGER.is_file():
        write_challenger("first challenger")
        return
    champ = summary(first_probes(ARMS["champion"], state["window_start"]))
    chall = summary(first_probes(ARMS["challenger"], state.get("challenger_since") or state["window_start"]))
    print(f"champion {champ} | challenger {chall}")
    if champ["measured"] < MIN_N or chall["measured"] < MIN_N:
        return
    if chall["rate"] >= champ["rate"] + PROMOTE:
        CHAMPION.write_text(CHALLENGER.read_text())
        state["window_start"] = now()
        state["history"].append({"at": state["window_start"], "event": "challenger promoted", "champion": champ, "challenger": chall})
        save_state(state)
        fa.robust.ledger("prompt_promoted", champion=champ, challenger=chall)
        write_challenger(f"after promotion ({chall['rate']:.0%} vs {champ['rate']:.0%})")
    elif chall["rate"] <= champ["rate"] - RETIRE:
        state["history"].append({"at": now(), "event": "challenger retired", "champion": champ, "challenger": chall})
        save_state(state)
        write_challenger(f"previous challenger trailed ({chall['rate']:.0%} vs {champ['rate']:.0%})")


def report():
    state = load_state()
    for arm, family in ARMS.items():
        since = state["window_start"] if arm == "champion" else (state.get("challenger_since") or state["window_start"])
        print(f"{arm:10s} {family:16s} since {since[11:16]}: {summary(first_probes(family, since))}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--new", action="store_true")
    parser.add_argument("--decide", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
    if args.new:
        state = load_state()
        save_state(state)
        write_challenger("first challenger")
    elif args.decide:
        decide()
    else:
        report()
