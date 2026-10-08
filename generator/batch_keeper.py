"""Keep GLM-5.1 generation running until a target number of gated learnable tasks exists (or a deadline passes).

Every two minutes: count running batch generators; start another t006-style checklist batch (8 authors, one per task-shape variant) whenever
fewer than --batches are running. Each batch is an ordinary resumable fast_author batch (output/batches/...).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


LAUNCHED = []  # batch processes started by this keeper (other generators running on the machine are not counted)


def running_batches():
    return sum(1 for proc in LAUNCHED if proc.poll() is None)


def shipped():
    return sum(1 for toml in (ROOT / "output" / "learnable").glob("*/task.toml") if "glm" in toml.read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=12)
    parser.add_argument("--batches", type=int, default=4)
    parser.add_argument("--until", default="19:00")
    parser.add_argument("--start", type=int, default=24)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--adopt", nargs="*", default=[], help="batch ids to resume under this keeper (counted toward --batches)")
    args = parser.parse_args()
    start = args.start
    for batch_id in args.adopt:
        # Resumed batches re-attach to their existing probe jobs (no re-authoring, no re-paid runs) and count as running.
        log = ROOT / "output" / ".probe-logs" / f"keeper-resume-{batch_id}.log"
        LAUNCHED.append(subprocess.Popen([sys.executable, "-u", str(ROOT / "generator" / "fast_author.py"), "--resume", batch_id, "--max-edits", "1"],
                                         cwd=ROOT, stdout=open(log, "a"), stderr=subprocess.STDOUT, start_new_session=True, env=os.environ.copy()))
        print(f"[{time.strftime('%H:%M')}] resumed {batch_id} -> {log.name}", flush=True)
    while True:
        now = time.strftime("%H:%M")
        if shipped() >= args.target or now >= args.until:
            print(f"[{now}] stopping: shipped={shipped()} target={args.target}", flush=True)
            break
        if running_batches() < args.batches:
            # Prompt governor: regress to the best measured prompt before authoring more (rule in prompt_governor.py).
            gov = subprocess.run([sys.executable, str(ROOT / "generator" / "prompt_governor.py"), "--apply"],
                                 cwd=ROOT, capture_output=True, text=True)
            regression = [l for l in gov.stdout.splitlines() if l.startswith("Prompt regression")]
            if regression:
                print(f"[{now}] {regression[0]}", flush=True)
            log = ROOT / "output" / ".probe-logs" / f"keeper-{time.strftime('%H%M%S')}.log"
            LAUNCHED.append(subprocess.Popen([sys.executable, "-u", str(ROOT / "generator" / "fast_author.py"), "--families", "everyday-trap",
                              "--count", str(args.count), "--parallel", str(args.count), "--start", str(args.start), "--max-edits", "1"],
                             cwd=ROOT, stdout=open(log, "w"), stderr=subprocess.STDOUT, start_new_session=True, env=os.environ.copy()))
            print(f"[{now}] started a checklist batch (shipped={shipped()}, running={running_batches()}) -> {log.name}", flush=True)
            time.sleep(20)
            continue
        time.sleep(120)
