"""Repair prompts retain task evidence without copying framework-only files."""
import json
from unittest.mock import Mock

import pytest

from generator import generate
from phase2 import controller
from runner import store


def bundle():
    return {"name": "recover-records", "description": "Repair record recovery.", "files": {
        "instruction.md": "Recover committed records from the supplied journal.",
        "environment/Dockerfile": "FROM python:3.12-slim-bookworm\nWORKDIR /app\nCOPY app.py /app/app.py\n",
        "environment/app.py": "def recover(records):\n    return []\n",
        "solution/solve.sh": "#!/bin/bash\nset -euo pipefail\nprintf 'def recover(records): return records\\n' > /app/app.py\n",
        "tests/test_outputs.py": "def test_recovery():\n    assert True\n"}}


def test_authored_file_roundtrip_excludes_framework_only_files_and_user_suffix(tmp_path):
    authored = bundle()
    path = tmp_path / "task"
    generate._materialize(generate._parse_bundle(json.dumps(authored)), path)
    assert "USER root" in (path / "environment/Dockerfile").read_text()
    restored = generate.authored_files(path)
    assert restored == authored["files"] | {"instruction.md": authored["files"]["instruction.md"] + "\n"}
    assert "tests/test.sh" not in restored and "README.md" not in restored and "task.toml" not in restored
    assert generate.config.CANARY_LINE not in "".join(restored.values())
    assert generate._parse_bundle(json.dumps({**authored, "files": restored})).name == authored["name"]


def test_repair_sanitizer_does_not_hide_arbitrary_docker_user_directive(tmp_path):
    authored = bundle()
    path = tmp_path / "task"
    generate._materialize(generate._parse_bundle(json.dumps(authored)), path)
    dockerfile = path / "environment/Dockerfile"
    dockerfile.write_text(dockerfile.read_text().replace("WORKDIR /app", "USER nobody\nWORKDIR /app"))
    restored = generate.authored_files(path)
    assert "USER nobody" in restored["environment/Dockerfile"]
    with pytest.raises(ValueError, match="must not set ENTRYPOINT or USER"):
        generate._parse_bundle(json.dumps({**authored, "files": restored}))


def test_repair_after_structural_failure_keeps_valid_source_and_oracle_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TASKLAB_PROMPTS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(controller, "DATA", tmp_path / "phase2")
    monkeypatch.setattr(store, "DATA", tmp_path / "runner")
    record = controller.start_batch()
    task = record["tasks"][0]
    source = tmp_path / "original-task"
    generate._materialize(generate._parse_bundle(json.dumps(bundle())), source)
    oracle_feedback = {"repair_instruction": "Correct journal replay without weakening tests.",
                       "runs": [{"id": "oracle", "failed_tests": [{"name": "test_recovery", "message": "Expected committed record 7, got none."}]}]}
    task.update(precheck_feedback=oracle_feedback,
                feedback={"outcome": "generation_failed", "errors": ["Dockerfile must not set ENTRYPOINT or USER."]},
                attempts=[
                    {"number": 0, "finished": True, "generation": {"status": "structurally_validated", "task_dir": str(source)},
                     "semantic_review": {"verdict": "pass", "summary": "Public contract consistent", "issues": []},
                     "execution_feedback": oracle_feedback},
                    {"number": 1, "finished": True, "generation": {
                        "status": "generation_failed", "task_dir": str(tmp_path / "nonexistent-invalid-task"),
                        "errors": ["Dockerfile must not set ENTRYPOINT or USER."],
                        "content": "very large provider reasoning that must not enter feedback"}}])
    author = Mock(return_value={"status": "api_error", "cost_usd": 0, "errors": ["fixture stops here"]})
    monkeypatch.setattr(generate, "generate_task", author)
    controller.process_task(record, task)
    context = author.call_args.kwargs["repair_feedback"]
    assert context["previous_task"]["instruction.md"].startswith("Recover committed records")
    assert "USER root" not in context["previous_task"]["environment/Dockerfile"]
    assert "tests/test.sh" not in context["previous_task"]
    assert context["precheck_feedback"] == oracle_feedback
    assert context["attempt_history"][0]["execution_feedback"] == oracle_feedback
    assert "ENTRYPOINT or USER" in context["attempt_history"][1]["generation_errors"][0]
    assert context["previous_feedback"]["outcome"] == "generation_failed"
    assert "large provider reasoning" not in json.dumps(context)
