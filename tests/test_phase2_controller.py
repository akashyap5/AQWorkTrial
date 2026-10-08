"""Offline integration checks for bounded batches and evidence-based prompt edits."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from generator import generate
from phase2 import controller, llm, prompts
from runner import store


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setenv("TASKLAB_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(controller, "DATA", tmp_path / "phase2")
    monkeypatch.setattr(store, "DATA", tmp_path / "runner")


def finished_job(evaluations=None, *, control_statuses=("passed", "passed"), status="completed"):
    evaluations = evaluations or ["passed", "failed", "failed", "failed", "failed"]
    runs = [{"id": kind, "kind": kind, "status": state} for kind, state in zip(("oracle", "nop"), control_statuses)]
    runs += [{"id": f"eval-{i}", "kind": "evaluation", "status": state}
             for i, state in enumerate(evaluations, 1)]
    return {"id": "a" * 16, "runs": runs, "status": status,
            "summary": store.summarize(runs, "demo")}


def generation_result(topic, output_dir, **kwargs):
    path = Path(output_dir)
    path.mkdir(parents=True)
    (path / "instruction.md").write_text("Repair the original application according to the public contract.")
    return {"status": "structurally_validated", "task_dir": str(path), "cost_usd": 0.04,
            "prompt_version": kwargs["prompt_version"]}


def review(verdict="pass", cost=0.01):
    return {"verdict": verdict, "summary": "Static contract audit", "issues": [],
            "call": {"status": "completed", "cost_usd": cost}}


def install_task_mocks(monkeypatch, *, reviews=None, job=None):
    author = Mock(side_effect=generation_result)
    reviewer = Mock(side_effect=reviews or [review()])
    job = job or finished_job()
    queue = Mock(return_value=job)
    monkeypatch.setattr(generate, "generate_task", author)
    monkeypatch.setattr(controller, "semantic_review", reviewer)
    monkeypatch.setattr(store, "create_job", queue)
    monkeypatch.setattr(store, "job", lambda job_id: job)
    monkeypatch.setattr(controller, "_wait_job", lambda *args: job)
    monkeypatch.setattr(controller, "_job_feedback", lambda job: {"summary": controller.summarize_job(job)})
    return author, reviewer, queue


def test_start_freezes_source_prompt_and_next_batch_uses_latest_active():
    base = prompts.current_prompt()
    first = controller.start_batch()
    candidate = prompts.create_version("A candidate with fresh authoring guidance.", base["version"])
    assert first["prompt_version"] == base["version"]
    assert first["source_prompt"]["sha256"] == base["sha256"]
    assert first["source_prompt"]["text"] == base["text"]
    first["status"] = "checkpoint"
    controller._save(first)
    second = controller.start_batch()
    assert second["prompt_version"] == candidate["version"]
    assert second["source_prompt"]["status"] == "candidate"


def test_concurrent_batch_starts_allow_only_one():
    def start():
        try:
            return controller.start_batch()["id"]
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: start(), range(4)))
    assert sum(value is not None for value in outcomes) == 1
    assert len(controller._records()) == 1


def test_nonofficial_api_base_is_rejected_before_a_batch_is_created(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://example.invalid/v1")
    with pytest.raises(ValueError, match="OpenRouter|openrouter"):
        controller.start_batch()
    assert controller._records() == []


def test_task_generation_stays_on_batch_source_even_if_pointer_changes(monkeypatch):
    record = controller.start_batch()
    original = record["prompt_version"]
    prompts.create_version("An unrelated newly activated candidate.", original)
    author, reviewer, queue = install_task_mocks(monkeypatch)
    controller.process_task(record, record["tasks"][0])
    assert author.call_args.kwargs["prompt_version"] == original
    assert author.call_args.kwargs["max_repairs"] == 0
    assert queue.call_args.kwargs["metadata"]["prompt_version"] == original
    assert queue.call_args.kwargs["profile"] == "demo"
    assert record["tasks"][0]["status"] == "completed"
    assert reviewer.call_count == 1


def test_rejected_semantic_review_never_submits_solver_job(monkeypatch):
    record = controller.start_batch()
    author, reviewer, queue = install_task_mocks(monkeypatch, reviews=[review("reject")])
    controller.process_task(record, record["tasks"][0])
    assert record["tasks"][0]["status"] == "invalid"
    assert author.call_count == reviewer.call_count == 1
    queue.assert_not_called()


def test_review_budget_stop_keeps_budget_outcome_and_never_submits_job(monkeypatch):
    record = controller.start_batch()
    stopped = {**review("error"), "stop_reason": "budget_exhausted"}
    _, _, queue = install_task_mocks(monkeypatch, reviews=[stopped])
    controller.process_task(record, record["tasks"][0])
    assert record["tasks"][0]["status"] == "budget_exhausted"
    assert record["tasks"][0]["attempts"][0]["terminal_status"] == "budget_exhausted"
    queue.assert_not_called()


def test_semantic_repair_retains_original_files_and_subtracts_spend(monkeypatch):
    record = controller.start_batch()
    author, reviewer, queue = install_task_mocks(monkeypatch, reviews=[review("repair"), review()])
    controller.process_task(record, record["tasks"][0])
    assert author.call_count == reviewer.call_count == 2
    assert queue.call_count == 1
    repair_call = author.call_args_list[1]
    assert repair_call.kwargs["repair_feedback"]["previous_task"]["instruction.md"]
    assert repair_call.kwargs["repair_feedback"]["previous_feedback"]["verdict"] == "repair"
    assert repair_call.kwargs["budget_usd"] == pytest.approx(0.46)
    assert reviewer.call_args_list[1].args[2] == pytest.approx(
        record["config"]["authoring"]["semantic_review_cost_usd"] - 0.01)


def test_pass_with_blocking_issues_is_repaired(monkeypatch, tmp_path):
    (tmp_path / "instruction.md").write_text("original contract")
    monkeypatch.setattr(llm, "budgeted_json_call", Mock(return_value={
        "status": "completed", "cost_usd": 0.01,
        "data": {"verdict": "pass", "summary": "Contradictory", "issues": [{"evidence": "missing contract"}]}}))
    result = controller.semantic_review(tmp_path, tmp_path / "evidence", 0.1)
    assert result["verdict"] == "repair"


@pytest.mark.parametrize("evaluations,outcome", [
    (["failed"] * 5, "too_hard_in_demo"),
    (["passed"] * 5, "too_easy_in_demo"),
    (["passed", "failed", "failed", "failed", "failed"], "observed_demo_band"),
    (["passed", "budget_exhausted", "budget_exhausted", "budget_exhausted", "budget_exhausted"], "budget_limited"),
    (["passed", "error", "failed", "failed", "failed"], "inconclusive"),
])
def test_demo_outcomes_keep_budget_exhaustion_distinct_and_never_claim_learnability(evaluations, outcome):
    result = controller.summarize_job(finished_job(evaluations))
    assert result["outcome"] == outcome
    assert result["learnable"] is None
    if "budget_exhausted" in evaluations:
        assert result["valid_runs"] == 1
        assert result["failures"] == 0


def test_failed_controls_cannot_become_difficulty_feedback():
    result = controller.summarize_job(finished_job(control_statuses=("failed", "passed"), status="validation_failed"))
    assert result["outcome"] == "invalid_task"
    assert result["learnable"] is None


def test_checkpoint_creates_one_candidate_with_batch_provenance_and_does_not_continue(monkeypatch):
    record = controller.start_batch()
    source = copy.deepcopy(record["source_prompt"])
    task_calls = []
    def evaluate_batch_task(record, task):
        task_calls.append(task["id"])
        task.update(status="completed", summary=controller.summarize_job(finished_job(["passed"] * 5)),
                    feedback={"reason": "All five valid attempts passed."})
    monkeypatch.setattr(controller, "process_task", evaluate_batch_task)
    candidate_text = source["text"] + "\nRequire one additional meaningful interaction supported by the public contract.\n"
    updater = Mock(return_value={"status": "completed", "cost_usd": 0.02, "data": {
        "action": "revise", "prompt": candidate_text, "rationale": "Three valid tasks were too easy.",
        "evidence": ["event-ledger:5/5"], "expected_effect": "More mixed outcomes", "uncertainty": "Small sample"}})
    monkeypatch.setattr(llm, "budgeted_json_call", updater)
    controller.process_batch(record)
    assert record["status"] == "checkpoint"
    assert len(task_calls) == len(controller.BRIEFS)
    assert len(controller._records()) == 1
    assert updater.call_count == 1
    assert record["decision"] == "candidate_ready_for_validation"
    candidate = prompts.current_prompt()
    assert candidate["version"] != source["version"]
    assert candidate["status"] == "candidate"
    assert candidate["parent_version"] == source["version"]
    assert candidate["evidence"]["batch_id"] == record["id"]
    assert candidate["evidence"]["metrics"]["observed_demo_band_tasks"] == 0
    assert source["text"] == prompts.get_prompt(source["version"])["text"]
    assert "additional meaningful interaction" in record["prompt_diff"]
    assert "Stopped after this batch" in record["checkpoint_message"]


def test_keep_decision_does_not_create_or_activate_version(monkeypatch):
    record = controller.start_batch()
    monkeypatch.setattr(llm, "budgeted_json_call", Mock(return_value={"status": "completed", "cost_usd": 0.01,
        "data": {"action": "keep", "prompt": record["source_prompt"]["text"], "rationale": "Insufficient evidence"}}))
    controller.propose_update(record)
    assert record["decision"] == "no_change"
    assert len(prompts.list_prompts()) == 1
    assert prompts.current_prompt()["version"] == record["prompt_version"]


def test_updater_receives_repair_defects_even_after_final_review_passes(monkeypatch):
    record = controller.start_batch()
    issue = {"file": "tests/test_outputs.py",
             "evidence": "Verifier requires sorted keys, but the contract only requires semantic JSON equality.",
             "suggested_fix": "Compare parsed JSON values without imposing key order."}
    repaired_review = {"verdict": "repair", "summary": "An assertion exceeds the public contract.", "issues": [issue]}
    passed_review = {"verdict": "pass", "summary": "The repaired verifier matches the public contract.", "issues": []}
    task = record["tasks"][0]
    task.update(status="completed", semantic_review=passed_review,
                feedback={"summary": {"outcome": "observed_demo_band"}}, attempts=[
                    {"number": 0, "generation": {"status": "structurally_validated", "errors": []},
                     "semantic_review": repaired_review, "finished": True},
                    {"number": 1, "generation": {"status": "structurally_validated", "errors": []},
                     "semantic_review": passed_review, "finished": True},
                ])
    updater = Mock(return_value={"status": "completed", "cost_usd": 0.01,
        "data": {"action": "keep", "prompt": record["source_prompt"]["text"], "rationale": "Record the repaired defect."}})
    monkeypatch.setattr(llm, "budgeted_json_call", updater)
    controller.propose_update(record)
    payload = json.loads(updater.call_args.args[0][1]["content"])
    submitted = payload["tasks"][0]
    assert submitted["semantic_review"]["verdict"] == "pass"
    assert len(submitted["attempt_history"]) == 2
    assert submitted["attempt_history"][0]["semantic_review"]["issues"] == [issue]
    assert submitted["attempt_history"][1]["semantic_review"]["verdict"] == "pass"
    assert submitted["feedback"]["summary"]["outcome"] == "observed_demo_band"


def test_prompt_correction_preserves_prior_attempt_and_shares_update_budget(monkeypatch):
    record = controller.start_batch()
    source = record["source_prompt"]["text"]
    first_text = source + "\nAdd one measured task-scope change after interpreting the evidence.\n"
    corrected_text = source + "\nTreat budget-limited attempts as inconclusive and preserve prior repair lessons.\n"
    updater = Mock(side_effect=[
        {"status": "completed", "cost_usd": 0.07, "data": {
            "action": "revise", "prompt": first_text, "rationale": "Initial proposal"}},
        {"status": "completed", "cost_usd": 0.04, "data": {
            "action": "revise", "prompt": corrected_text, "rationale": "Corrected interpretation"}},
    ])
    monkeypatch.setattr(llm, "budgeted_json_call", updater)
    controller.propose_update(record)
    first_attempt = copy.deepcopy(record["update_attempts"][0])
    first_candidate = record["candidate_prompt"]["version"]
    prompts.mark_status(first_candidate, "rejected")
    prompts.activate_version(record["prompt_version"])
    correction = {
        "reason": "Budget exhaustion does not establish that the ledger task is too hard.",
        "runtime_observation": "Host idle sleep interrupted this evaluation.",
        "include": "Earlier semantic repair findings remain relevant.",
    }
    controller.propose_update(record, correction_feedback=correction)
    first_call, second_call = updater.call_args_list
    allowance = record["config"]["adaptation"]["prompt_update_cost_usd"]
    assert first_call.kwargs["budget_usd"] == allowance
    assert second_call.kwargs["budget_usd"] == pytest.approx(allowance - 0.07)
    assert record["update_cost_usd"] == pytest.approx(0.11)
    assert first_call.kwargs["request_name"] == "prompt-update"
    assert second_call.kwargs["request_name"] == "prompt-update-correction-1"
    assert first_call.kwargs["evidence_dir"] == second_call.kwargs["evidence_dir"]
    payload = json.loads(second_call.args[0][1]["content"])
    assert payload["correction_feedback"] == correction
    assert len(record["update_attempts"]) == 2
    assert record["update_attempts"][0] == first_attempt
    assert record["update_attempts"][1]["correction_feedback"] == correction
    assert prompts.get_prompt(first_candidate)["status"] == "rejected"
    assert prompts.current_prompt()["text"] == corrected_text


def test_interrupted_prompt_update_is_not_automatically_retried(monkeypatch):
    record = controller.start_batch()
    record["update_started_at"] = store.now()
    caller = Mock()
    monkeypatch.setattr(llm, "budgeted_json_call", caller)
    with pytest.raises(RuntimeError, match="no automatic paid retry"):
        controller.process_batch(record)
    caller.assert_not_called()


def test_interrupted_authoring_does_not_create_paid_retry(monkeypatch):
    record = controller.start_batch()
    task = record["tasks"][0]
    task["status"] = "generating"
    task["attempts"] = [{"number": 0, "finished": False}]
    author = Mock()
    monkeypatch.setattr(generate, "generate_task", author)
    with pytest.raises(RuntimeError, match="no automatic paid retry"):
        controller.process_task(record, task)
    author.assert_not_called()


def test_recovered_finished_evaluation_does_not_generate_another_attempt(monkeypatch):
    record = controller.start_batch()
    task = record["tasks"][0]
    task["status"] = "evaluating"
    task["attempts"] = [{"number": 0, "finished": True, "terminal_status": "completed"}]
    author = Mock()
    monkeypatch.setattr(generate, "generate_task", author)
    controller.process_task(record, task)
    assert task["status"] == "completed"
    author.assert_not_called()


def test_control_infrastructure_error_is_not_repaired_as_a_task_defect(monkeypatch):
    record = controller.start_batch()
    job = finished_job(control_statuses=("error", "passed"), status="validation_failed")
    author, _, queue = install_task_mocks(monkeypatch, job=job)
    controller.process_task(record, record["tasks"][0])
    assert record["tasks"][0]["status"] == "error"
    assert record["tasks"][0]["summary"]["outcome"] == "execution_error"
    assert author.call_count == queue.call_count == 1
