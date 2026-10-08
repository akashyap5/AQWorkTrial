"""Offline checks for broad task authoring and cheap duplicate detection."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from generator import generate
from phase2 import diversity, llm


def task_at(path, instruction, code, tests="def test_result():\n    assert True\n"):
    path.mkdir()
    (path / "environment").mkdir()
    (path / "tests").mkdir()
    (path / "instruction.md").write_text(instruction)
    (path / "environment/app.py").write_text(code)
    (path / "tests/test_outputs.py").write_text(tests)
    return path


def test_rotation_covers_distinct_families_before_repeating():
    briefs = diversity.choose_briefs(0, 16, [])
    assert len({row["family"] for row in briefs[:8]}) == 8
    assert len({row["id"] for row in briefs}) == 16
    assert all(row["name"] and row["topic"] for row in briefs)
    assert briefs[0]["solution_pattern"] != briefs[8]["solution_pattern"]


def test_rotation_prioritizes_underexplored_families_even_after_failed_tasks():
    first = diversity.choose_briefs(0, 1)[0]
    archive = [{**first, "status": "invalid"}] * 3
    next_briefs = diversity.choose_briefs(0, 7, archive)
    assert first["family"] not in {row["family"] for row in next_briefs}
    assert len({row["family"] for row in next_briefs}) == 7


def test_compact_archive_keeps_distinct_solution_patterns_without_source():
    archive = [{"id": str(i), "family": f"family-{i % 4}", "name": "Useful task",
                "solution_pattern": f"pattern-{i}", "description": "Details",
                "fingerprint": {"large": "hashes"}, "solution": "secret code"}
               for i in range(20)]
    compact = diversity.compact_archive(archive, 4)
    assert len(compact) == 4
    assert len({row["family"] for row in compact}) == 4
    assert all("solution_pattern" in row and "solution" not in row and "fingerprint" not in row
               for row in compact)
    assert diversity.compact_archive(archive, 0) == []


def test_exact_instruction_duplicate_ignores_formatting_and_framework_files(tmp_path):
    first = task_at(tmp_path / "one", "Repair THE streaming decoder.\nHandle empty frames.", "pass")
    second = task_at(tmp_path / "two", "repair the streaming decoder; handle empty frames!", "pass")
    (second / "tests/test.sh").write_text("lots of different framework boilerplate")
    (second / "README.md").write_text("different human summary")
    result = diversity.duplicate_check(diversity.fingerprint_task(second), [
        {"id": "original", "fingerprint": diversity.fingerprint_task(first)}])
    assert result["duplicate"]
    assert result["reason"] == "identical_normalized_instruction"
    assert result["match_id"] == "original"


def test_common_empty_starter_and_pytest_boilerplate_do_not_imply_duplicate(tmp_path):
    code = "import json\nfrom pathlib import Path\ndef solve(value):\n    raise NotImplementedError\n"
    tests = "import subprocess\nfrom pathlib import Path\ndef test_output():\n    assert True\n"
    first = task_at(tmp_path / "one", "Recover an interrupted journal while preserving committed transactions.", code, tests)
    second = task_at(tmp_path / "two", "Decode framed messages split across arbitrary byte chunks.", code, tests)
    result = diversity.duplicate_check(diversity.fingerprint_task(second), [
        {"id": "original", "fingerprint": diversity.fingerprint_task(first)}])
    assert not result["duplicate"]


def test_reskinned_nontrivial_code_and_tests_are_detected(tmp_path):
    code = """import json
def rebuild(records, limit):
    outputs = []
    seen = set()
    for record in records:
        key = record['name']
        if key in seen:
            continue
        seen.add(key)
        if record['value'] > limit:
            outputs.append((key, record['value']))
    return sorted(outputs)
def serialize(records):
    grouped = {}
    for key, value in records:
        grouped.setdefault(key, []).append(value)
    return json.dumps(grouped, sort_keys=True)
def load(path):
    with open(path) as stream:
        records = json.load(stream)
    return rebuild(records, 5)
"""
    tests = """import json
import pytest
from app import rebuild, serialize, load
def test_rebuild():
    records = [{'name': 'a', 'value': 7}, {'name': 'a', 'value': 8}]
    assert rebuild(records, 5) == [('a', 7)]
    assert rebuild(records, 9) == []
def test_serialize():
    result = json.loads(serialize([('a', 4), ('a', 5), ('b', 6)]))
    assert result['a'] == [4, 5]
    assert result['b'] == [6]
    assert serialize([]) == '{}'
def test_load(tmp_path):
    path = tmp_path / 'one.json'
    path.write_text(json.dumps([{'name': 'a', 'value': 7}]))
    assert load(path) == [('a', 7)]
"""
    first = task_at(tmp_path / "one", "Prepare a inventory report from warehouse records.", code, tests)
    second = task_at(tmp_path / "two", "Summarize sensor detections for a wildlife station.",
                     code.replace("records", "detections").replace("'name'", "'sensor'").replace("limit", "cutoff"),
                     tests.replace("records", "detections").replace("'name'", "'sensor'").replace("7", "17"))
    result = diversity.duplicate_check(diversity.fingerprint_task(second), [
        {"id": "original", "fingerprint": diversity.fingerprint_task(first)}])
    assert result["duplicate"], result
    assert result["reason"] == "same_initial_code_and_test_structure"


@pytest.fixture
def author_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setenv("TASKLAB_PROMPTS_DIR", str(tmp_path / "prompt-registry"))
    monkeypatch.delenv("GENERATOR_MODEL", raising=False)
    monkeypatch.delenv("TASKLAB_AUTHOR_TIMEOUT_SEC", raising=False)


def test_generated_task_has_readable_metadata_and_high_level_readme(tmp_path, monkeypatch, author_env):
    bundle = {"name": "recover-sqlite-journal", "description": "Recover committed records after an interrupted journal write.", "files": {
        "instruction.md": "Repair /app/app.py to print 2.",
        "environment/Dockerfile": "FROM python:3.12-slim-bookworm\nWORKDIR /app\nRUN echo 'print(1)' > app.py\n",
        "solution/solve.sh": "#!/bin/bash\nset -euo pipefail\necho 'print(2)' > /app/app.py\n",
        "tests/test_outputs.py": "import subprocess\ndef test_app():\n    assert subprocess.check_output(['python3', '/app/app.py']).strip() == b'2'\n"}}
    fake_call = Mock(return_value={"status": "completed", "data": bundle, "content": json.dumps(bundle),
                                  "cost_usd": 0.01})
    monkeypatch.setattr(generate, "budgeted_json_call", fake_call)
    monkeypatch.setattr(generate, "OpenAI", Mock())
    archive = [{"id": "old", "solution_pattern": "incremental decoding"}]
    result = generate.generate_task("recover storage", str(tmp_path / "task"), diversity_context=archive)
    assert result["status"] == "structurally_validated", result
    assert result["name"] == "recover-sqlite-journal"
    assert result["display_name"] == "Recover SQLite Journal"
    readme = (Path(result["task_dir"]) / "README.md").read_text()
    assert readme.startswith("# Recover SQLite Journal")
    assert bundle["description"] in readme
    assert "instruction.md" in readme
    assert bundle["files"]["instruction.md"] not in readme
    assert "incremental decoding" in fake_call.call_args.args[0][1]["content"]
    assert "README.md" in result["files_sha256"]


def test_author_timeout_is_configurable_for_direct_json_calls(tmp_path, monkeypatch, author_env):
    monkeypatch.setenv("TASKLAB_AUTHOR_TIMEOUT_SEC", "600")
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(model_dump=lambda **kw: {
        "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        "usage": {"cost": 0.01}})
    constructor = Mock(return_value=client)
    monkeypatch.setattr(llm, "OpenAI", constructor)
    result = llm.budgeted_json_call([{"role": "user", "content": "return JSON"}], tmp_path, 0.1)
    assert result["status"] == "completed"
    assert result["request_timeout_sec"] == 600
    assert constructor.call_args.kwargs["timeout"] == 600
    assert constructor.call_args.kwargs["max_retries"] == 0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad", "3601"])
def test_invalid_author_timeout_rejected_before_request(monkeypatch, value):
    monkeypatch.setenv("TASKLAB_AUTHOR_TIMEOUT_SEC", value)
    with pytest.raises(ValueError, match="TASKLAB_AUTHOR_TIMEOUT_SEC"):
        llm.author_timeout_sec()
