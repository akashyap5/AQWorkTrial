"""Offline tests for task authoring boundaries and evidence preservation."""
import copy
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from generator import generate


def bundle():
    return {
        "name": "repair-counter",
        "description": "Repair the counter implementation.",
        "files": {
            "instruction.md": "Repair /app/counter.py so next_value returns its argument plus one.",
            "environment/Dockerfile": "FROM python:3.12-slim-bookworm\nWORKDIR /app\nCOPY counter.py /app/counter.py\n",
            "environment/counter.py": "def next_value(value):\n    return value\n",
            "solution/solve.sh": "#!/bin/bash\nset -euo pipefail\nprintf 'def next_value(value):\\n    return value + 1\\n' > /app/counter.py\n",
            "tests/test_outputs.py": "import sys\nsys.path.insert(0, '/app')\nfrom counter import next_value\ndef test_increment():\n    assert next_value(4) == 5\n",
        },
    }


def completion(content):
    if isinstance(content, dict):
        content = json.dumps(content)
    data = {
        "id": "fake-completion", "model": generate.AUTHOR_MODEL,
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 600, "total_tokens": 800},
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model_dump=lambda **kwargs: data,
    )


@pytest.fixture
def inference(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret-never-log")
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://example.invalid/v1")
    monkeypatch.delenv("GENERATOR_MODEL", raising=False)
    monkeypatch.setattr(generate.config, "MODEL", generate.AUTHOR_MODEL)
    client = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(generate, "OpenAI", constructor)
    client.chat.completions.create.return_value = completion(bundle())
    return client, constructor


def test_valid_generation_preserves_evidence_and_does_not_claim_execution(tmp_path, inference):
    client, constructor = inference
    result = generate.generate_task("Repair a counter", str(tmp_path / "task"))
    assert result["status"] == "structurally_validated", result
    assert result["attempts"] == 1
    assert result["functional_validation"] == {"oracle": "not_run", "nop": "not_run"}
    assert result["band_evaluation"] == "not_run"
    task = Path(result["task_dir"])
    assert generate.validate_task(str(task))["passed"]
    config = tomllib.loads((task / "task.toml").read_text())
    assert config["agent"]["timeout_sec"] >= 3600
    assert config["task"]["name"] == "generated/repair-counter"
    for name in ["environment/Dockerfile", "solution/solve.sh", "tests/test_outputs.py", "tests/test.sh"]:
        assert generate.config.CANARY_LINE in (task / name).read_text()
    assert "canary" not in (task / "instruction.md").read_text()
    assert (task / "solution/solve.sh").read_text().startswith("#!/bin/bash\n")
    assert (task / "tests/test.sh").stat().st_mode & 0o111
    assert "pytest==8.4.1" in (task / "environment/Dockerfile").read_text()
    assert "/logs/verifier/reward.txt" in (task / "tests/test.sh").read_text()
    evidence = Path(result["evidence_dir"])
    response = json.loads((evidence / "attempt-1-response.json").read_text())
    assert response["usage"]["total_tokens"] == 800
    assert "test-secret-never-log" not in "".join(p.read_text() for p in evidence.rglob("*.json"))
    assert not evidence.is_relative_to(task)
    assert set(result["files_sha256"]) == {str(p.relative_to(task)) for p in task.rglob("*") if p.is_file()}
    constructor.assert_called_once_with(
        api_key="test-secret-never-log", base_url="https://example.invalid/v1", max_retries=0, timeout=180.0,
    )
    assert client.chat.completions.create.call_args.kwargs["model"] == "z-ai/glm-5.1"
    assert client.chat.completions.create.call_args.kwargs["max_tokens"] == 32768


@pytest.mark.parametrize("invalid", ["not-json", json.dumps({"name": "task"})])
def test_malformed_response_receives_bounded_repair(tmp_path, inference, invalid):
    client, _ = inference
    requests = []
    responses = iter([completion(invalid), completion(bundle())])

    def create(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        return next(responses)

    client.chat.completions.create.side_effect = create
    result = generate.generate_task("counter", str(tmp_path / "task"), max_repairs=1)
    assert result["status"] == "structurally_validated"
    assert result["attempts"] == 2
    assert len(requests[0]["messages"]) == 2
    assert "validation errors" in requests[1]["messages"][-1]["content"]
    assert json.loads((Path(result["evidence_dir"]) / "attempt-1-validation.json").read_text())["passed"] is False


def test_invalid_python_tests_are_repaired_but_broken_application_is_allowed(tmp_path, inference):
    client, _ = inference
    bad = bundle()
    bad["files"]["tests/test_outputs.py"] = "def invalid(:\n"
    good = bundle()
    good["files"]["environment/counter.py"] = "def intentionally_broken(:\n"
    client.chat.completions.create.side_effect = [completion(bad), completion(good)]
    result = generate.generate_task("counter", str(tmp_path / "task"), max_repairs=1)
    assert result["status"] == "structurally_validated"
    first = json.loads((Path(result["evidence_dir"]) / "attempt-1-validation.json").read_text())
    assert any("tests/test_outputs.py" in error for error in first["errors"])


def test_repairs_stop_and_invalid_task_is_not_published(tmp_path, inference):
    client, _ = inference
    client.chat.completions.create.return_value = completion("not json")
    destination = tmp_path / "task"
    result = generate.generate_task("counter", str(destination), max_repairs=2)
    assert result["status"] == "generation_failed"
    assert result["attempts"] == client.chat.completions.create.call_count == 3
    assert not destination.exists()
    assert (Path(result["evidence_dir"]) / "result.json").exists()


@pytest.mark.parametrize("unsafe", [
    "../escaped", "/tmp/escaped", "environment/../../escaped", "environment/./escaped",
    "environment//escaped", "environment\\escaped", "task.toml", "tests/test.sh",
])
def test_model_cannot_write_outside_bundle_or_override_framework(tmp_path, inference, unsafe):
    client, _ = inference
    unsafe_bundle = bundle()
    unsafe_bundle["files"][unsafe] = "do not write this"
    client.chat.completions.create.return_value = completion(unsafe_bundle)
    result = generate.generate_task("counter", str(tmp_path / "task"), max_repairs=0)
    assert result["status"] == "generation_failed"
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "task").exists()


def test_shell_script_is_checked_without_execution(tmp_path, inference):
    client, _ = inference
    marker = tmp_path / "must-not-exist"
    authored = bundle()
    authored["files"]["solution/solve.sh"] = f"#!/bin/bash\ntouch '{marker}'\n"
    client.chat.completions.create.return_value = completion(authored)
    result = generate.generate_task("counter", str(tmp_path / "task"))
    assert result["status"] == "structurally_validated"
    assert not marker.exists()


def test_existing_destination_is_never_overwritten_or_billed(tmp_path, inference):
    client, _ = inference
    destination = tmp_path / "task"
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_text("keep this")
    with pytest.raises(FileExistsError):
        generate.generate_task("counter", str(destination))
    assert sentinel.read_text() == "keep this"
    client.chat.completions.create.assert_not_called()


def test_api_error_is_not_a_task_defect_and_credentials_are_not_logged(tmp_path, inference):
    client, _ = inference
    client.chat.completions.create.side_effect = RuntimeError("request header: test-secret-never-log")
    result = generate.generate_task("counter", str(tmp_path / "task"))
    assert result["status"] == "api_error"
    assert result["attempts"] == 1
    assert result["structural_validation"] is None
    evidence = Path(result["evidence_dir"])
    assert "test-secret-never-log" not in "".join(p.read_text() for p in evidence.rglob("*.json"))


def test_missing_key_or_wrong_model_fails_before_request(tmp_path, inference, monkeypatch):
    client, _ = inference
    monkeypatch.setenv("GENERATOR_MODEL", "another-model")
    with pytest.raises(ValueError, match="restricted"):
        generate.generate_task("counter", str(tmp_path / "task"))
    monkeypatch.delenv("GENERATOR_MODEL")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        generate.generate_task("counter", str(tmp_path / "task"))
    client.chat.completions.create.assert_not_called()


def test_cli_works_from_script_and_module():
    root = Path(__file__).resolve().parents[1]
    for args, cwd in [(["-m", "generator.generate", "--help"], root), (["generate.py", "--help"], root / "generator")]:
        result = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "--max-repairs" in result.stdout


@pytest.mark.parametrize("pytest_exit,reward", [(0, "1\n"), (1, "0\n"), (2, None), (5, None), (127, None)])
def test_framework_verifier_distinguishes_failures_from_broken_verification(tmp_path, pytest_exit, reward):
    # Run only our fixed wrapper with a fake pytest process in isolated paths.
    # No model-produced script or application code is executed on the host.
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(f"#!/bin/bash\nexit {pytest_exit}\n")
    fake_python.chmod(0o755)
    verifier_logs = tmp_path / "verifier-logs"
    script = generate.VERIFIER_SCRIPT.replace("/logs/verifier", str(verifier_logs))
    script = script.replace("/opt/task-verifier/bin/python", str(fake_python))
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    reward_path = verifier_logs / "reward.txt"
    if reward is None:
        assert not reward_path.exists()
        assert result.returncode == pytest_exit
    else:
        assert result.returncode == 0
        assert reward_path.read_text() == reward


def test_framework_collects_additional_authored_test_modules(tmp_path):
    test_dir = tmp_path / "private-tests"
    test_dir.mkdir()
    (test_dir / "test_outputs.py").write_text("def test_primary():\n    assert True\n")
    (test_dir / "test_extra.py").write_text("def test_additional_requirement():\n    assert False\n")
    verifier_logs = tmp_path / "verifier-logs"
    # Exercise the fixed framework script against harmless local fixtures.
    # CTRF is installed in generated images, not required in this unit-test env.
    script = generate.VERIFIER_SCRIPT.replace("--ctrf /logs/verifier/ctrf.json", "")
    script = script.replace("/logs/verifier", str(verifier_logs))
    script = script.replace("/opt/task-verifier/bin/python", sys.executable)
    script = script.replace("/tests", str(test_dir))
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (verifier_logs / "reward.txt").read_text() == "0\n"
    assert "test_additional_requirement" in result.stdout
    assert "1 failed, 1 passed" in result.stdout
