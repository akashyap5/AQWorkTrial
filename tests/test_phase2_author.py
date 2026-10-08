"""No-network checks for prompt provenance and authoring spend boundaries."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from generator import generate
from phase2 import llm, prompts


@pytest.fixture(autouse=True)
def isolated_author(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKLAB_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-author-secret")
    monkeypatch.delenv("GENERATOR_MODEL", raising=False)


def response(data=None, *, content=None, usage=None, finish_reason="stop"):
    raw = {"choices": [{"finish_reason": finish_reason, "message": {
        "content": content if content is not None else json.dumps(data or {"ok": True})}}],
        "usage": usage or {"cost": 0.005, "prompt_tokens": 100, "completion_tokens": 100}}
    return SimpleNamespace(model_dump=lambda **kw: raw)


def client_for(result):
    client = Mock()
    client.chat.completions.create.return_value = result
    return client


def test_initial_registry_preserves_starter_and_versions(tmp_path):
    starter = (prompts.ROOT / "prompts/starter_prompt.txt").read_text()
    first = prompts.current_prompt()
    assert first["version"] == "v0001"
    assert first["text"] == starter
    second = prompts.create_version("a useful revision", first["version"], {"tasks": ["a", "b"]})
    assert second["status"] == "candidate"
    assert prompts.current_prompt()["version"] == "v0002"
    assert prompts.get_prompt("v0001")["text"] == starter
    assert second["sha256"] != first["sha256"]
    assert (prompts.ROOT / "prompts/starter_prompt.txt").read_text() == starter


def test_version_allocation_is_serialized_and_does_not_overwrite():
    first = prompts.current_prompt()
    with ThreadPoolExecutor(max_workers=5) as pool:
        versions = list(pool.map(lambda n: prompts.create_version(
            f"revision {n}", first["version"], activate=False), range(10)))
    assert len({p["version"] for p in versions}) == 10
    assert prompts.current_prompt()["version"] == first["version"]
    assert len(prompts.list_prompts()) == 11


def test_status_changes_do_not_mutate_immutable_version_record():
    base = prompts.current_prompt()
    revision = prompts.create_version("candidate", base["version"])
    path = prompts.registry_path() / "versions" / f"{revision['version']}.json"
    before = path.read_bytes()
    prompts.mark_status(revision["version"], "rejected")
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="rejected"):
        prompts.current_prompt()
    prompts.activate_version(base["version"])
    assert prompts.current_prompt()["version"] == base["version"]
    with pytest.raises(ValueError, match="rejected"):
        prompts.activate_version(revision["version"])


def test_compare_and_activate_does_not_overwrite_a_newer_current_prompt():
    base = prompts.current_prompt()
    newer = prompts.create_version("new current prompt", base["version"])
    with pytest.raises(ValueError, match="Current prompt changed"):
        prompts.create_version("stale batch update", base["version"], require_current_parent=True)
    assert prompts.current_prompt()["version"] == newer["version"]
    assert len(prompts.list_prompts()) == 2
    newest = prompts.create_version("fresh batch update", newer["version"], require_current_parent=True)
    assert prompts.current_prompt()["version"] == newest["version"]


def test_registry_detects_tampering_and_rejects_path_traversal():
    first = prompts.current_prompt()
    path = prompts.registry_path() / "versions" / f"{first['version']}.json"
    value = json.loads(path.read_text())
    value["text"] += "changed"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="immutable hash"):
        prompts.current_prompt()
    with pytest.raises(ValueError, match="Invalid prompt"):
        prompts.get_prompt("../../outside")


def test_pre_call_budget_guard_makes_no_paid_request(tmp_path):
    client = client_for(response())
    result = llm.budgeted_json_call([{"role": "user", "content": "x" * 1000}],
                                  tmp_path, 0.0001, client=client)
    assert result["status"] == "budget_exhausted"
    assert result["cost_usd"] == 0
    client.chat.completions.create.assert_not_called()


def test_provider_price_guard_and_max_tokens_bound_reservation(tmp_path):
    client = client_for(response())
    messages = [{"role": "user", "content": "return JSON"}]
    result = llm.budgeted_json_call(messages, tmp_path, 0.02, max_tokens=32768, client=client)
    request = client.chat.completions.create.call_args.kwargs
    assert request["model"] == "z-ai/glm-5.1"
    assert 1024 <= request["max_tokens"] < 32768
    assert request["extra_body"]["provider"]["max_price"] == {
        "prompt": 1.5, "completion": 4.5, "request": 0}
    assert request["extra_body"]["provider"]["sort"] == "throughput"
    assert result["reserved_usd"] <= 0.02
    assert result["data"] == {"ok": True}
    assert result["cost_usd"] == 0.005


def test_generation_can_disable_reasoning_without_changing_reviewer_defaults(tmp_path):
    client = client_for(response())
    llm.budgeted_json_call([{"role": "user", "content": "write a task"}],
                           tmp_path / "generation", 0.1, client=client, reasoning_enabled=False)
    assert client.chat.completions.create.call_args.kwargs["extra_body"]["reasoning"] == {"enabled": False}
    llm.budgeted_json_call([{"role": "user", "content": "audit a task"}],
                           tmp_path / "review", 0.1, client=client)
    assert "reasoning" not in client.chat.completions.create.call_args.kwargs["extra_body"]


def test_byok_cost_is_not_lost_when_account_cost_is_zero(tmp_path):
    client = client_for(response(usage={"cost": 0, "is_byok": True,
                                        "cost_details": {"upstream_inference_cost": 0.031}}))
    result = llm.budgeted_json_call([{"role": "user", "content": "x"}],
                                  tmp_path, 0.1, client=client)
    assert result["cost_usd"] == 0.031
    assert result["accounting"]["source"] == "provider_usage"


def test_byok_account_fee_is_added_to_upstream_cost():
    accounting = llm.usage_accounting({"usage": {"cost": 0.001, "is_byok": True,
        "cost_details": {"upstream_inference_cost": 0.02}}}, 0.1)
    assert accounting["cost_usd"] == pytest.approx(0.021)


def test_non_byok_cost_is_not_double_counted():
    accounting = llm.usage_accounting({"usage": {"cost": 0.02, "is_byok": False,
        "cost_details": {"upstream_inference_cost": 0.02}}}, 0.1)
    assert accounting["cost_usd"] == 0.02


def test_missing_byok_flag_with_both_cost_components_counts_both():
    accounting = llm.usage_accounting({"usage": {"cost": 0.001,
        "cost_details": {"upstream_inference_cost": 0.02}}}, 0.1)
    assert accounting["cost_usd"] == pytest.approx(0.021)


def test_unknown_usage_retains_full_reservation(tmp_path):
    client = client_for(response(usage={"total_tokens": 20}))
    result = llm.budgeted_json_call([{"role": "user", "content": "x"}],
                                  tmp_path, 0.1, client=client)
    assert result["cost_usd"] == result["reserved_usd"]
    assert result["accounting"]["source"] == "reserved_unknown_usage"


def test_ambiguous_api_error_never_retries_and_reserves_cost(tmp_path):
    client = Mock()
    client.chat.completions.create.side_effect = RuntimeError("test-author-secret in headers")
    result = llm.budgeted_json_call([{"role": "user", "content": "x"}],
                                  tmp_path, 0.1, client=client)
    assert result["status"] == "api_error"
    assert result["cost_usd"] == result["reserved_usd"] > 0
    assert client.chat.completions.create.call_count == 1
    assert "test-author-secret" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


@pytest.mark.parametrize("content,finish_reason", [("{}", "length"), ("[]", "stop"), ("bad", "stop"), ("{}", "error")])
def test_truncated_or_nonobject_response_is_rejected_but_billed(tmp_path, content, finish_reason):
    client = client_for(response(content=content, finish_reason=finish_reason))
    result = llm.budgeted_json_call([{"role": "user", "content": "x"}],
                                  tmp_path, 0.1, client=client)
    assert result["status"] == "invalid_response"
    assert result["cost_usd"] == 0.005


def test_input_bound_uses_utf8_bytes_and_rejects_unbounded_media():
    assert llm.input_token_bound([{"role": "user", "content": "😀" * 100}]) > 400
    with pytest.raises(ValueError, match="text messages"):
        llm.input_token_bound([{"role": "user", "content": [{"image_url": "anything"}]}])


def test_generation_automatically_uses_latest_pointer_and_can_pin_baseline(tmp_path, monkeypatch):
    base = prompts.current_prompt()
    next_prompt = prompts.create_version("Unique candidate guidance", base["version"])
    task = {"name": "counter", "description": "Repair a counter", "files": {
        "instruction.md": "Make /app/counter.py print 2.",
        "environment/Dockerfile": "FROM python:3.12-slim-bookworm\nWORKDIR /app\nRUN echo 'print(1)' > counter.py\n",
        "solution/solve.sh": "#!/bin/bash\nset -euo pipefail\necho 'print(2)' > /app/counter.py\n",
        "tests/test_outputs.py": "import subprocess\ndef test_counter():\n    assert subprocess.check_output(['python3','/app/counter.py']).strip() == b'2'\n",
    }}
    client = client_for(response(task))
    monkeypatch.setattr(generate, "OpenAI", Mock(return_value=client))
    result = generate.generate_task("counter", str(tmp_path / "task"))
    assert result["status"] == "structurally_validated"
    assert result["prompt_version"] == next_prompt["version"]
    request = client.chat.completions.create.call_args.kwargs
    assert request["messages"][0]["content"] == generate.SYSTEM_PROMPT
    assert "Unique candidate guidance" in request["messages"][1]["content"]
    evidence = json.loads((Path(result["evidence_dir"]) / "prompt.json").read_text())
    assert evidence["sha256"] == next_prompt["sha256"]
    assert evidence["text"] == next_prompt["text"]
    pinned = generate.generate_task("counter", str(tmp_path / "task-pinned"), prompt_version=base["version"])
    assert pinned["prompt_version"] == base["version"]


def test_authoring_budget_is_shared_across_structural_repairs(tmp_path, monkeypatch):
    client = client_for(response(content="invalid", usage={"cost": 0.025}))
    monkeypatch.setattr(generate, "OpenAI", Mock(return_value=client))
    result = generate.generate_task("counter", str(tmp_path / "task"), budget_usd=0.026)
    assert result["status"] == "budget_exhausted"
    assert client.chat.completions.create.call_count == 1
    assert result["cost_usd"] == 0.025
    assert not Path(result["task_dir"]).exists()
