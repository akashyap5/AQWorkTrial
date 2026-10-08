"""Submit a local example/generated task to the same runner used by the UI."""
import argparse
import json
import time
from pathlib import Path
from runner.store import JOB_TERMINAL, create_job, job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--controls-only", action="store_true")
    parser.add_argument("--wait", action="store_true", help="Wait for the running Task Lab worker")
    parser.add_argument("--profile", choices=("calibration", "demo", "search"), default="calibration")
    args = parser.parse_args()
    if not (args.task / "task.toml").is_file():
        parser.error("task must be a Harbor task directory")
    result = create_job(args.task.name, "controls" if args.controls_only else "full", args.task, profile=args.profile)
    print(f"Job {result['id']}: http://127.0.0.1:8000/api/jobs/{result['id']}", flush=True)
    if args.wait:
        while result["status"] not in JOB_TERMINAL:
            time.sleep(2)
            result = job(result["id"])
        print(json.dumps(result, indent=2))
        if result["status"] != "completed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
