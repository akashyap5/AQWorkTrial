"""Read Harbor's actual trial results, CTRF tests, and ATIF trajectories."""
import re
from pathlib import Path
from runner.store import read_json, redact


def trial_directory(run_dir):
    for path in sorted(Path(run_dir).glob("harbor/*/*/config.json")):
        return path.parent
    return None


def text_file(path, limit=250_000):
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            text = stream.read().decode("utf-8", errors="replace")
        return redact(("[Earlier output omitted]\n" if size > limit else "") + text)
    except FileNotFoundError:
        return ""


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def episodes(run_dir):
    trial = trial_directory(run_dir)
    if trial is None:
        return []
    rows = []
    paths = [p for p in (trial / "agent").glob("trajectory*.json")
             if re.fullmatch(r"trajectory(?:\.cont-\d+)?\.json", p.name)]
    paths.sort(key=lambda p: 0 if p.name == "trajectory.json" else int(re.search(r"cont-(\d+)", p.name).group(1)) + 1 if re.search(r"cont-(\d+)", p.name) else 999)
    for path in paths:
        data = read_json(path, {})
        for step in data.get("steps", []):
            if step.get("source") != "agent" or step.get("is_copied_context"):
                continue
            commands = []
            for call in step.get("tool_calls") or []:
                args = call.get("arguments") or {}
                if not any(key in args for key in ("commands", "keystrokes", "command")):
                    continue
                items = args.get("commands", [args])
                if not isinstance(items, list):
                    items = [items]
                for item in items:
                    if isinstance(item, str):
                        commands.append({"command": redact(item), "duration": None})
                    elif isinstance(item, dict):
                        commands.append({"command": redact(item.get("keystrokes", item.get("command", str(item)))), "duration": item.get("duration_sec", item.get("duration"))})
            output = "\n".join(content_text(r.get("content")) for r in (step.get("observation") or {}).get("results", []))
            rows.append({"index": len(rows) + 1, "analysis": redact(content_text(step.get("message"))),
                         "commands": commands, "output": redact(output), "timestamp": step.get("timestamp")})
    return rows


def test_results(trial):
    reports = sorted((trial / "verifier").glob("*ctrf.json"))
    tests = []
    for path in reports:
        report = read_json(path, {})
        report_tests = (report.get("results") or {}).get("tests", [])
        if not report_tests:
            tests.append({"name": f"{path.stem}: verifier report", "status": "error",
                          "message": "Empty or malformed verifier report (possible collection error)."})
        for test in report_tests:
            test = dict(test)
            if test.get("raw_status") in {"setup_failed", "teardown_failed"}:
                test["status"] = "error"
            if len(reports) > 1:
                test["name"] = f"{path.stem}: {test.get('name', 'unnamed')}"
            tests.append(test)
    if tests:
        return [{"name": t.get("name", "unnamed"), "status": t.get("status", "unknown"),
                 "raw_status": t.get("raw_status"),
                 "message": redact(t.get("message") or t.get("trace") or "")} for t in tests]
    output = text_file(trial / "verifier" / "test-stdout.txt")
    pairs = re.findall(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)", output, re.MULTILINE)
    pairs += [(name, status) for status, name in re.findall(r"^(PASSED|FAILED|ERROR|SKIPPED)\s+(\S+::\S+)", output, re.MULTILINE)]
    return [{"name": name, "status": status.lower(), "message": ""} for name, status in dict(pairs).items()]


def classify(result, tests, kind):
    exception = result.get("exception_info")
    if exception:
        detail = f"{exception.get('exception_type', 'Error')}: {exception.get('exception_message', '')}"
        return {"status": "timeout" if "timeout" in exception.get("exception_type", "").lower() else "error", "error": redact(detail), "passed": None, "reward": None}
    reward = (result.get("verifier_result") or {}).get("rewards", {}).get("reward")
    if reward not in (0, 1) or not tests or any(t["status"] not in {"passed", "failed"} for t in tests):
        return {"status": "error", "error": "Verifier did not produce a binary reward and a nonempty, error-free test report.", "passed": None, "reward": reward}
    all_passed = all(t["status"] == "passed" for t in tests)
    if bool(reward) != all_passed:
        return {"status": "error", "error": "Reward disagrees with individual test results.", "passed": None, "reward": reward}
    passed = reward == (0 if kind == "nop" else 1)
    return {"status": "passed" if passed else "failed", "error": None, "passed": passed, "reward": reward}


def details(run_dir):
    run_dir = Path(run_dir)
    state = read_json(run_dir / "state.json", {})
    trial = trial_directory(run_dir)
    state.update(episodes=episodes(run_dir), log=text_file(run_dir / "console.log"), verifier_output="")
    if trial:
        state["verifier_output"] = text_file(trial / "verifier" / "test-stdout.txt")
        state["log"] += "\n" + text_file(trial / "trial.log")
        if state["kind"] == "oracle":
            state["log"] += "\n" + text_file(trial / "agent" / "oracle.txt")
    return state
