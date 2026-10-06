"""
Structural validator for Harbor (Terminal-Bench 2) tasks.

Checks that a generated task directory matches the layout and ``task.toml``
schema that Harbor enforces. The schema mirrored here tracks Harbor's own
``TaskConfig`` / ``TaskPaths`` (vendored for reference under
``harbor/src/harbor/models/task/``). If the real ``harbor`` package is
importable, we additionally run its native validation as a cross-check.

Exit code is 0 only when there are no errors. Warnings never fail the run but
flag things you almost certainly want to fix.
"""

import re
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python <3.11: `pip install tomli`
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        sys.exit(
            "This validator needs TOML support. Use Python 3.11+ (recommended) "
            "or `pip install tomli` on older versions."
        )

# Mirrors harbor.constants.ORG_NAME_PATTERN
ORG_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*/[a-zA-Z0-9][a-zA-Z0-9._-]*$")

# Mirrors harbor.models.difficulty.Difficulty
VALID_DIFFICULTIES = {"easy", "medium", "hard", "unknown"}

# Agents must get at least an hour, so a timeout is not the reason a run fails.
MIN_AGENT_TIMEOUT_SEC = 3600.0

# Files/dirs Harbor's TaskPaths looks for.
REQUIRED_FILES = ["task.toml", "instruction.md"]
REQUIRED_DIRS = ["environment", "solution", "tests"]


def _check_task_toml(task_path: Path, errors: list[str], warnings: list[str]) -> dict:
    """Parse and schema-check task.toml. Returns the parsed dict (or {})."""
    toml_path = task_path / "task.toml"
    if not toml_path.exists():
        return {}

    try:
        with open(toml_path, "rb") as f:
            cfg = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        errors.append(f"task.toml parse error: {e}")
        return {}

    # schema_version (Harbor accepts `version` as a legacy alias).
    if "schema_version" not in cfg and "version" not in cfg:
        warnings.append("task.toml has no `schema_version` (Harbor defaults it, but set it explicitly).")

    # [task] package info — required for a publishable dataset task.
    task = cfg.get("task")
    if not isinstance(task, dict):
        errors.append("task.toml missing [task] section.")
    else:
        name = task.get("name", "")
        if not name:
            errors.append("task.toml [task].name is missing.")
        elif not ORG_NAME_PATTERN.match(name) or ".." in name:
            errors.append(f"task.toml [task].name {name!r} is not in 'org/name' format.")
        if not task.get("description"):
            warnings.append("task.toml [task].description is empty.")

    # [metadata].difficulty — drives calibration; require a valid value.
    meta = cfg.get("metadata", {})
    if not isinstance(meta, dict):
        errors.append("task.toml [metadata] is not a table.")
    else:
        difficulty = meta.get("difficulty")
        if difficulty is None:
            errors.append("task.toml [metadata].difficulty is missing.")
        elif difficulty not in VALID_DIFFICULTIES:
            errors.append(
                f"task.toml [metadata].difficulty {difficulty!r} not in {sorted(VALID_DIFFICULTIES)}."
            )

    # Timeouts must be positive numbers where present.
    for section in ("verifier", "agent"):
        block = cfg.get(section, {})
        if isinstance(block, dict) and "timeout_sec" in block:
            t = block["timeout_sec"]
            if not isinstance(t, (int, float)) or t <= 0:
                errors.append(f"task.toml [{section}].timeout_sec must be a positive number.")

    # The agent must get at least an hour, so timeouts aren't a failure mode.
    agent_block = cfg.get("agent", {})
    if isinstance(agent_block, dict):
        agent_timeout = agent_block.get("timeout_sec")
        if agent_timeout is None:
            errors.append(
                f"task.toml [agent].timeout_sec must be set to at least "
                f"{int(MIN_AGENT_TIMEOUT_SEC)} (1 hour)."
            )
        elif isinstance(agent_timeout, (int, float)) and agent_timeout < MIN_AGENT_TIMEOUT_SEC:
            errors.append(
                f"task.toml [agent].timeout_sec is {agent_timeout}, but must be at least "
                f"{int(MIN_AGENT_TIMEOUT_SEC)} (1 hour) so timeouts aren't a failure mode."
            )

    env = cfg.get("environment", {})
    if isinstance(env, dict):
        bt = env.get("build_timeout_sec")
        if bt is not None and (not isinstance(bt, (int, float)) or bt <= 0):
            errors.append("task.toml [environment].build_timeout_sec must be a positive number.")

    return cfg


def _check_environment(task_path: Path, cfg: dict, errors: list[str]) -> None:
    """environment/ must define how to build: Dockerfile, compose, or docker_image."""
    env_dir = task_path / "environment"
    if not env_dir.is_dir():
        return  # missing-dir already reported by the required-dirs check

    dockerfile = env_dir / "Dockerfile"
    compose = (env_dir / "docker-compose.yaml").exists() or (
        env_dir / "docker-compose.yml"
    ).exists()
    docker_image = bool(cfg.get("environment", {}).get("docker_image"))

    if not dockerfile.exists() and not compose and not docker_image:
        errors.append(
            "environment/ has no Dockerfile or docker-compose.yaml and "
            "task.toml sets no [environment].docker_image."
        )
    elif dockerfile.exists():
        if "FROM" not in dockerfile.read_text():
            errors.append("environment/Dockerfile is missing a FROM statement.")


def _check_scripts(task_path: Path, errors: list[str], warnings: list[str]) -> None:
    """solution/solve.sh, tests/test.sh, and the reward contract."""
    # Harbor discovers solve.{sh,bat} / test.{sh,bat}.
    if (task_path / "solution").is_dir():
        if not any((task_path / "solution" / f"solve{ext}").exists() for ext in (".sh", ".bat")):
            errors.append("solution/ has no solve.sh (or solve.bat).")

    tests_dir = task_path / "tests"
    if tests_dir.is_dir():
        test_script = next(
            (tests_dir / f"test{ext}" for ext in (".sh", ".bat") if (tests_dir / f"test{ext}").exists()),
            None,
        )
        if test_script is None:
            errors.append("tests/ has no test.sh (or test.bat).")
        else:
            # The reward contract: test.sh must write /logs/verifier/reward.{txt,json}.
            body = test_script.read_text()
            if "reward.txt" not in body and "reward.json" not in body and "rewards.json" not in body:
                errors.append(
                    "tests/test.sh never writes /logs/verifier/reward.txt "
                    "(or reward.json) — Harbor reads the reward from there."
                )
        if not list(tests_dir.glob("*.py")):
            warnings.append("tests/ has no .py files (fine for non-pytest tasks, unusual otherwise).")


def _check_canary(task_path: Path, warnings: list[str]) -> None:
    """Real Terminal-Bench tasks carry a canary marker in the non-instruction
    files (solve.sh / Dockerfile / tests). Harbor strips canary lines from
    instruction.md before showing it to the agent, so it's conventionally left
    out of instruction.md. Warn if no canary appears anywhere in the task."""
    candidates = [
        task_path / "solution" / "solve.sh",
        task_path / "environment" / "Dockerfile",
        *(task_path / "tests").glob("*.py"),
    ]
    has_canary = any(
        p.exists() and "canary" in p.read_text().lower() for p in candidates
    )
    if not has_canary:
        warnings.append("No canary marker found in solve.sh / Dockerfile / tests (recommended for benchmark data).")


def _harbor_cross_check(task_path: Path, errors: list[str], warnings: list[str]) -> None:
    """If the real harbor package is installed, run its native validation too."""
    try:
        from harbor.models.task.config import TaskConfig  # type: ignore
        from harbor.models.task.task import Task  # type: ignore
    except Exception:
        warnings.append("harbor not importable — skipped native schema cross-check (`pip install harbor`).")
        return

    toml_path = task_path / "task.toml"
    if toml_path.exists():
        try:
            TaskConfig.model_validate_toml(toml_path.read_text())
        except Exception as e:  # pydantic ValidationError, etc.
            errors.append(f"harbor TaskConfig rejected task.toml: {e}")

    try:
        if not Task.is_valid_dir(task_path):
            errors.append("harbor Task.is_valid_dir() returned False for this directory.")
    except Exception as e:
        errors.append(f"harbor Task.is_valid_dir() raised: {e}")


def validate_task(task_dir: str) -> dict:
    """Validate a task directory. Returns {passed, errors, warnings}."""
    task_path = Path(task_dir)
    if not task_path.is_dir():
        return {"passed": False, "errors": [f"Directory does not exist: {task_dir}"], "warnings": []}

    errors: list[str] = []
    warnings: list[str] = []

    for filename in REQUIRED_FILES:
        if not (task_path / filename).exists():
            errors.append(f"Missing required file: {filename}")
    for dirname in REQUIRED_DIRS:
        if not (task_path / dirname).is_dir():
            errors.append(f"Missing required directory: {dirname}/")

    if (task_path / "instruction.md").exists() and not (task_path / "instruction.md").read_text().strip():
        errors.append("instruction.md is empty.")

    cfg = _check_task_toml(task_path, errors, warnings)
    _check_environment(task_path, cfg, errors)
    _check_scripts(task_path, errors, warnings)
    _check_canary(task_path, warnings)
    _harbor_cross_check(task_path, errors, warnings)

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate.py <task_dir>")
        sys.exit(1)

    result = validate_task(sys.argv[1])

    for w in result["warnings"]:
        print(f"  WARN: {w}")

    if result["passed"]:
        print("PASSED: All structural checks passed.")
    else:
        print("FAILED:")
        for e in result["errors"]:
            print(f"  - {e}")

    sys.exit(0 if result["passed"] else 1)
