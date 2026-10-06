"""Author an original Harbor task and validate its structure locally.

Oracle, nop, and difficulty checks must run separately in fresh containers.
No model-produced command is executed on the host.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

if __package__:
    from . import config
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from generator import config

from validator.validate import validate_task

AUTHOR_MODEL = "z-ai/glm-5.1"
BASE_IMAGE = "python:3.12-slim-bookworm"
MAX_RESPONSE_BYTES = 2_000_000
REQUIRED_AUTHORED_FILES = {
    "instruction.md", "environment/Dockerfile", "solution/solve.sh", "tests/test_outputs.py",
}
SYSTEM_PROMPT = f"""You author original Harbor terminal tasks from a topic.
Do not reuse or adapt any public benchmark task. Invent a concrete, useful
scenario with an unambiguous, testable contract. Target a moderately difficult
coding/system task; actual learnability will be measured later, not assumed.

Return one JSON object with exactly these fields:
{{"name": "short-kebab-slug", "description": "one sentence",
 "files": {{"instruction.md": "...", "environment/Dockerfile": "...",
 "solution/solve.sh": "...", "tests/test_outputs.py": "..."}}}}
Include additional text files under environment/, solution/, or tests/ when
needed. All file paths must be relative POSIX paths. No markdown code fences.
Do not provide task.toml or tests/test.sh: the framework supplies those.

Requirements:
- instruction.md describes every tested behavior, paths and edge cases. Do not
  disclose the reference solution or mention hidden test implementation details.
- Dockerfile must have a single FROM {BASE_IMAGE}, install all task dependencies
  at build time, and create a genuinely unsolved initial state in /app. Use
  WORKDIR /app. Install bash if needed. Do not set ENTRYPOINT or USER. Do not
  require external services, API keys, internet access during solving, or
  machine-specific files. COPY sources are relative to environment/ only.
- The framework appends a build-time install of pytest and pytest-json-ctrf into
  /opt/task-verifier. Do not change that path. Verifier code should need only
  pytest and the standard library. Other runtime tools belong in the image.
  The framework collects all test modules under /tests. When a test invokes a
  Python application, use the system 'python3' executable in subprocess.run,
  not sys.executable: pytest's isolated interpreter does not include application
  dependencies installed into the system Python environment.
- solve.sh must begin with #!/bin/bash and set -euo pipefail. It is a complete,
  deterministic reference solution, run only in the container, and must solve
  the described behavior rather than bypass tests. Never copy the solution or
  private tests into environment/. Do not inspect /tests from the solution.
- test_outputs.py runs from /tests against the final container state in /app.
  Use behavioral assertions, multiple independent deterministic cases and
  meaningful boundary cases. Verify requirements, not exact implementation text.
  The correct reference solution must pass; the untouched environment must fail.
  Tests must not modify the application to make it pass or invoke the oracle.
- Make all fixture data self-contained. Do not rely on current dates, randomness
  without fixed seeds, remote downloads during verification, or wall-clock races.
- Do not include benchmark canaries; the framework adds them consistently.
"""
VERIFIER_SCRIPT = """#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\\n' > /logs/verifier/reward.txt
/opt/task-verifier/bin/python -m pytest /tests \\
  --ctrf /logs/verifier/ctrf.json -rA
test_status=$?
if [ "$test_status" -eq 0 ]; then
  printf '1\\n' > /logs/verifier/reward.txt
elif [ "$test_status" -ne 1 ]; then
  # Collection errors and interrupted/crashed verifiers are not ordinary failures.
  # Harbor reads the reward file even when the verifier exits nonzero.
  rm -f /logs/verifier/reward.txt
  exit "$test_status"
fi
"""
DOCKER_VERIFIER_SETUP = """
# Install the isolated verifier at build time, before evaluation begins.
USER root
RUN python3 -m venv /opt/task-verifier \\
    && /opt/task-verifier/bin/pip install --no-cache-dir \\
       pytest==8.4.1 pytest-json-ctrf==0.3.5
"""


class TaskBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=60)
    description: str = Field(min_length=1, max_length=1000)
    files: dict[str, str]


def _slugify(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60].rstrip("-") or "task"


def _redact(value: Any, api_key: str) -> Any:
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]") if api_key else value
    if isinstance(value, dict):
        return {_redact(key, api_key): _redact(item, api_key) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, api_key) for item in value]
    return value


def _write_json(path: Path, payload: Any, api_key: str = "") -> None:
    path.write_text(json.dumps(_redact(payload, api_key), indent=2, ensure_ascii=False) + "\n")


def _parse_bundle(content: str) -> TaskBundle:
    if len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("Response exceeds the 2 MB task bundle limit.")
    bundle = TaskBundle.model_validate_json(content)
    if not 4 <= len(bundle.files) <= 100:
        raise ValueError("A task must contain between 4 and 100 authored files.")
    missing = REQUIRED_AUTHORED_FILES - bundle.files.keys()
    if missing:
        raise ValueError(f"Missing required authored files: {', '.join(sorted(missing))}")
    for name, body in bundle.files.items():
        path = PurePosixPath(name)
        if (
            path.is_absolute() or "\\" in name or "\x00" in name
            or ".." in path.parts or str(path) != name
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in path.parts)
            or (name != "instruction.md" and (
                len(path.parts) < 2 or path.parts[0] not in {"environment", "solution", "tests"}
            ))
        ):
            raise ValueError(f"Unsafe or unsupported task file path: {name!r}")
        if name == "tests/test.sh":
            raise ValueError("tests/test.sh is supplied by the framework; omit it.")
        if not body.strip() or "\x00" in body:
            raise ValueError(f"Task file {name!r} must contain nonempty text without NUL bytes.")
    for name in bundle.files:
        if any(str(parent) in bundle.files for parent in PurePosixPath(name).parents):
            raise ValueError(f"Task file conflicts with a parent file: {name!r}")
    dockerfile = bundle.files["environment/Dockerfile"]
    from_lines = re.findall(r"^\s*FROM\s+(.+)$", dockerfile, flags=re.MULTILINE | re.IGNORECASE)
    if from_lines != [BASE_IMAGE]:
        raise ValueError(f"Dockerfile must use exactly one FROM {BASE_IMAGE}.")
    if re.search(r"^\s*(ENTRYPOINT|USER)\s", dockerfile, flags=re.MULTILINE | re.IGNORECASE):
        raise ValueError("Dockerfile must not set ENTRYPOINT or USER.")
    return bundle


def _canary(body: str) -> str:
    comment = f"# {config.CANARY_LINE}\n"
    if body.startswith("#!"):
        first, separator, rest = body.partition("\n")
        return first + "\n" + comment + (rest if separator else "")
    return comment + body


def _materialize(bundle: TaskBundle, destination: Path) -> None:
    destination.mkdir()
    for name, body in bundle.files.items():
        if name == "environment/Dockerfile":
            body = body.rstrip() + "\n" + DOCKER_VERIFIER_SETUP
        if name in {"environment/Dockerfile", "solution/solve.sh"} or name.startswith("tests/"):
            if name.endswith((".sh", ".py")) or name == "environment/Dockerfile":
                body = _canary(body)
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.rstrip() + "\n")
        if path.suffix == ".sh":
            path.chmod(0o755)
    (destination / "tests/test.sh").write_text(_canary(VERIFIER_SCRIPT))
    (destination / "tests/test.sh").chmod(0o755)
    description = json.dumps(bundle.description, ensure_ascii=False)
    (destination / "task.toml").write_text(
        'schema_version = "1.3"\n\n'
        f'[task]\nname = "generated/{bundle.name}"\ndescription = {description}\n\n'
        '[metadata]\ndifficulty = "unknown"\nauthoring_model = "z-ai/glm-5.1"\n\n'
        '[verifier]\ntimeout_sec = 900.0\n\n'
        '[agent]\ntimeout_sec = 3600.0\n\n'
        '[environment]\nbuild_timeout_sec = 1200.0\n'
        'cpus = 2\nmemory_mb = 2048\nstorage_mb = 10240\n'
    )


def _validate_candidate(path: Path) -> dict:
    result = validate_task(str(path))
    # Application fixtures can intentionally be broken; parse only the tests.
    for test in (path / "tests").rglob("*.py"):
        try:
            ast.parse(test.read_text(), filename=str(test.relative_to(path)))
        except SyntaxError as exc:
            result["errors"].append(f"{test.relative_to(path)}:{exc.lineno}: {exc.msg}")
    for directory in ("solution", "tests"):
        for script in (path / directory).rglob("*.sh"):
            checked = subprocess.run(
                ["bash", "-n", str(script)], capture_output=True, text=True, timeout=10,
            )
            if checked.returncode:
                result["errors"].append(f"{script.relative_to(path)}: {checked.stderr.strip()}")
    result["passed"] = not result["errors"]
    return result


def generate_task(topic: str, output_dir: str | None = None, *, max_repairs: int = 2) -> dict:
    """Generate and structurally validate a task, without running generated code.

    Success status is ``structurally_validated``; oracle/nop/band remain
    ``not_run``. Existing destinations are never overwritten. Evidence is kept
    outside the task directory and its Docker build context.
    """
    if not topic.strip():
        raise ValueError("A nonempty task topic is required.")
    if not isinstance(max_repairs, int) or not 0 <= max_repairs <= 3:
        raise ValueError("max_repairs must be an integer between 0 and 3.")
    api_key = os.environ.get("OPENROUTER_API_KEY", config.OPENROUTER_API_KEY)
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is required for task generation.")
    model = os.environ.get("GENERATOR_MODEL", config.MODEL)
    if model != AUTHOR_MODEL:
        raise ValueError(f"Task authoring is restricted to {AUTHOR_MODEL}.")
    base_url = os.environ.get("OPENROUTER_API_BASE", config.OPENROUTER_API_BASE)
    destination = Path(output_dir or Path(config.OUTPUT_DIR) / _slugify(_redact(topic, api_key))).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the name before inference: duplicates cannot both pay for requests.
    destination.mkdir()
    run_id = uuid.uuid4().hex
    evidence = destination.parent / ".generation-runs" / run_id
    evidence.mkdir(parents=True)
    started = time.monotonic()
    result: dict[str, Any] = {
        "task_dir": str(destination), "status": "generation_failed",
        "evidence_dir": str(evidence), "model": model, "attempts": 0,
        "structural_validation": None,
        "functional_validation": {"oracle": "not_run", "nop": "not_run"},
        "band_evaluation": "not_run", "errors": [],
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Create one original task about:\n{_redact(topic.strip(), api_key)}"},
    ]
    _write_json(evidence / "generation.json", {
        "run_id": run_id, "topic": topic, "model": model,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "max_repairs": max_repairs, "task_dir": str(destination),
    }, api_key)
    client = None
    try:
        # Disable hidden SDK retries so records correspond to actual requests.
        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=180.0)
        for attempt in range(max_repairs + 1):
            result["attempts"] = attempt + 1
            request = {
                "model": model, "messages": messages,
                "response_format": {"type": "json_object"},
                "max_tokens": 32768, "temperature": 0.7,
            }
            _write_json(evidence / f"attempt-{attempt + 1}-request.json", request, api_key)
            try:
                response = client.chat.completions.create(**request)
            except Exception as exc:
                # Transport exceptions can contain headers: retain safe metadata.
                error = {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
                _write_json(evidence / f"attempt-{attempt + 1}-api-error.json", error)
                result["status"] = "api_error"
                result["errors"] = [f"Authoring request failed: {error['type']} (see API error metadata)."]
                break
            _write_json(evidence / f"attempt-{attempt + 1}-response.json", response.model_dump(mode="json"), api_key)
            content = response.choices[0].message.content if response.choices else ""
            content = _redact(content or "", api_key)
            try:
                bundle = _parse_bundle(content)
                candidate = evidence / f"attempt-{attempt + 1}-task"
                _materialize(bundle, candidate)
                validation = _validate_candidate(candidate)
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                validation = {"passed": False, "errors": [str(exc)], "warnings": []}
            validation = _redact(validation, api_key)
            result["structural_validation"] = validation
            _write_json(evidence / f"attempt-{attempt + 1}-validation.json", validation, api_key)
            if validation["passed"]:
                for path in candidate.iterdir():
                    shutil.move(str(path), str(destination / path.name))
                result["status"] = "structurally_validated"
                result["errors"] = []
                result["files_sha256"] = {
                    str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(destination.rglob("*")) if path.is_file()
                }
                break
            result["errors"] = validation["errors"]
            messages.extend([
                {"role": "assistant", "content": content[:MAX_RESPONSE_BYTES]},
                {"role": "user", "content": "Repair the complete JSON task bundle to resolve these validation errors. "
                 "Preserve the original scenario and intended behavior. Return all authored files again, "
                 "not a patch. Do not weaken requirements or tests.\n" + json.dumps(validation["errors"])},
            ])
    finally:
        if client is not None:
            client.close()
        result["elapsed_sec"] = round(time.monotonic() - started, 3)
        _write_json(evidence / "result.json", result, api_key)
        if result["status"] != "structurally_validated":
            try:
                destination.rmdir()  # Remove only our own empty reservation.
            except OSError:
                pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", help="Original task topic")
    parser.add_argument("--output-dir", help="New task directory (must not already exist)")
    parser.add_argument("--max-repairs", type=int, default=2, choices=range(4))
    args = parser.parse_args()
    try:
        result = generate_task(args.topic, args.output_dir, max_repairs=args.max_repairs)
    except (ValueError, FileExistsError) as exc:
        parser.exit(2, f"Generation could not start: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "structurally_validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
