"""Offline search accounting, checkpoint, and export acceptance checks."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from phase2 import controller, llm, prompts, search
from runner import store


@pytest.fixture(autouse=True)
def isolated_search(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-search-secret")
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TASKLAB_PROMPTS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(search, "DATA", tmp_path / "search")
    monkeypatch.setattr(controller, "DATA", tmp_path / "phase2")
    monkeypatch.setattr(store, "DATA", tmp_path / "runner")
    monkeypatch.setattr(search, "ROOT", tmp_path)


def round_for(record, states=("invalid", "duplicate", "budget_exhausted"), hits=0):
    batch = controller.start_batch(profile="search", metadata={"search_id": record["id"]}, defer_update=True)
    batch["status"] = "checkpoint"
    batch["decision"] = "update_deferred"
    for index, (task, status) in enumerate(zip(batch["tasks"], states)):
        task.update(status=status, accepted=index < hits)
    controller._save(batch)
    return batch


def test_orphaned_published_round_is_recovered_without_creating_another():
    record = search.start_search()
    batch = round_for(record)
    assert record["rounds"] == []  # Simulates a crash before append/save.
    search._save(record)
    assert record["rounds"] == [batch["id"]]
    search._save(record)
    assert record["rounds"] == [batch["id"]]


def test_stagnation_is_derived_idempotently_across_two_file_crash_window():
    record = search.start_search()
    first = round_for(record)
    search._save(record)
    assert record["stagnant_rounds"] == 1
    # A stale legacy marker cannot cause another increment after a restart.
    first["search_accounted"] = False
    controller._save(first)
    for _ in range(3):
        search._save(record)
    assert record["stagnant_rounds"] == 1
    round_for(record, states=("completed",) * 3, hits=1)
    search._save(record)
    assert record["stagnant_rounds"] == 0
    round_for(record)
    search._save(record)
    assert record["stagnant_rounds"] == 1


def test_three_inconclusive_rounds_reach_brainstorm_checkpoint_without_counting_failures():
    record = search.start_search()
    record["config"]["stagnant_rounds"] = 3
    for _ in range(3):
        round_for(record, states=("budget_exhausted",) * 3)
    search._save(record)
    assert record["stagnant_rounds"] == 3
    assert record["accepted_count"] == 0
    assert "brainstorm" in search.stopping_reason(record)


def test_queued_stop_never_runs_paid_bootstrap(monkeypatch):
    record = search.start_search()
    search.stop_search(record["id"])
    bootstrap = Mock()
    finish = Mock()
    monkeypatch.setattr(search, "bootstrap", bootstrap)
    monkeypatch.setattr(search, "finish", finish)
    search.process_search(record)
    bootstrap.assert_not_called()
    assert finish.call_args.args[2] == "cancelled"


def test_interrupted_bootstrap_is_never_replayed(monkeypatch):
    record = search.start_search()
    record["bootstrap_started_at"] = store.now()
    search._save(record)
    call = Mock()
    monkeypatch.setattr(llm, "budgeted_json_call", call)
    with pytest.raises(RuntimeError, match="Interrupted search bootstrap"):
        search.bootstrap(record)
    call.assert_not_called()


def test_completed_bootstrap_version_is_recovered_and_activated_without_model(monkeypatch):
    record = search.start_search()
    first = prompts.current_prompt()
    candidate = prompts.create_version("Expanded scope prompt", first["version"], activate=False,
                                       evidence={"search_bootstrap": record["id"]})
    call = Mock()
    monkeypatch.setattr(llm, "budgeted_json_call", call)
    search.bootstrap(record)
    assert record["bootstrap_prompt"] == candidate["version"]
    assert prompts.current_prompt()["version"] == candidate["version"]
    call.assert_not_called()


def test_interrupted_bootstrap_preserves_reserved_cost_then_settles_actual():
    record = search.start_search()
    evidence = search.DATA / record["id"] / "bootstrap"
    store.write_json(evidence / "search-bootstrap-reservation.json", {"cost_usd": 0.20})
    search._save(record)
    assert record["accounted_cost_usd"] == 0.20
    store.write_json(evidence / "search-bootstrap-result.json", {"cost_usd": 0.03})
    search._save(record)
    assert record["accounted_cost_usd"] == 0.03


def test_report_denominator_retains_invalid_duplicate_and_inconclusive_slots():
    record = search.start_search()
    round_for(record)
    search.write_report(record)
    report = json.loads((search.ROOT / record["report_path"]).read_text())
    assert report["yield"]["candidate_slots"] == 3
    assert report["yield"]["rate"] == 0
    assert {row["status"] for row in report["candidates"]} == {"invalid", "duplicate", "budget_exhausted"}
    assert report["yield"]["first_ten_slots"] == 3


def test_score_accounting_does_not_double_count_a_round():
    record = search.start_search()
    batch = round_for(record, states=("completed", "invalid", "budget_exhausted"), hits=1)
    search.update_scores(record, batch)
    search.update_scores(record, batch)
    score = record["prompt_scores"][batch["prompt_version"]]
    assert score == {"slots": 3, "accepted": 1, "rounds": [batch["id"]], "yield": 1 / 3}


def prepared_export(record, tmp_path):
    batch = round_for(record, states=("completed",) * 3)
    task = batch["tasks"][0]
    source = tmp_path / "original-task"
    source.mkdir()
    (source / "README.md").write_text("# Journal Recovery\n\nRecover committed records.")
    (source / "instruction.md").write_text("Repair journal recovery.")
    job = store.create_job("journal-recovery", task_path=source, profile="search", metadata={
        "accepted": True, "failure_audit": {"accepted": True, "verdict": "valid_model_failures"}})
    path = store.directory(job["id"])
    stored = store.read_json(path / "job.json")
    stored["status"] = "completed"
    store.write_json(path / "job.json", stored)
    for index, run_id in enumerate(stored["run_ids"]):
        state_path = path / "runs" / run_id / "state.json"
        state = store.read_json(state_path)
        state["status"] = "passed" if index < 4 else "failed"
        store.write_json(state_path, state)
    task.update(accepted=True, job_id=job["id"], attempts=[{"job_id": job["id"], "generation": {
        "name": "journal-recovery", "display_name": "Journal Recovery"}}])
    controller._save(batch)
    return batch, path


def test_export_requires_audit_and_is_idempotent_after_copy_before_record_save(tmp_path):
    record = search.start_search()
    batch, _ = prepared_export(record, tmp_path)
    before = copy.deepcopy(record)
    search.collect(record, batch)
    assert len(record["accepted"]) == 1
    row = record["accepted"][0]
    assert (search.ROOT / row["readme"]).is_file()
    assert search._digest(search.ROOT / row["path"]) == row["task_sha256"]
    search.collect(before, batch)  # Snapshot exported but append was not persisted.
    assert len(before["accepted"]) == 1
    search.collect(before, batch)
    assert len(before["accepted"]) == 1


@pytest.mark.parametrize("defect", ["missing_audit", "failed_control", "incomplete_job", "changed_snapshot"])
def test_export_refuses_unverified_or_changed_task(tmp_path, defect):
    record = search.start_search()
    batch, path = prepared_export(record, tmp_path)
    raw = store.read_json(path / "job.json")
    if defect == "missing_audit":
        raw["metadata"].pop("failure_audit")
    elif defect == "incomplete_job":
        raw["status"] = "error"
    elif defect == "failed_control":
        state_path = path / "runs/oracle/state.json"
        state = store.read_json(state_path)
        state["status"] = "failed"
        store.write_json(state_path, state)
    else:
        (path / "task/instruction.md").write_text("Changed contract after evaluation.")
    store.write_json(path / "job.json", raw)
    with pytest.raises(ValueError, match="Refusing to export|snapshot changed"):
        search.collect(record, batch)
    assert record["accepted"] == []


def test_search_cannot_start_another_round_without_full_round_budget():
    record = search.start_search()
    record["config"]["max_cost_usd"] = record["config"]["round_allowance_usd"] - 0.01
    assert "budget" in search.stopping_reason(record)


def test_report_counts_stages_repairs_costs_and_elapsed_time(tmp_path):
    record = search.start_search()
    record.update(created_at="2026-10-06T20:00:00+00:00", completed_at="2026-10-06T20:02:03+00:00",
                  bootstrap_cost_usd=0.04)
    batch, _ = prepared_export(record, tmp_path)
    first, invalid, duplicate = batch["tasks"]
    first["attempts"][0]["generation"]["status"] = "structurally_validated"
    first["attempts"].insert(0, {"generation": {"status": "generation_failed"}})
    first.update(generation_cost_usd=0.10, review_cost_usd=0.02, failure_audit_cost_usd=0.03)
    invalid.update(status="invalid", attempts=[{"generation": {"status": "generation_failed"}}],
                   generation_cost_usd=0.07)
    duplicate.update(status="duplicate", attempts=[{"generation": {"status": "structurally_validated"}}],
                     generation_cost_usd=0.09)
    batch["update_cost_usd"] = 0.05
    controller._save(batch)
    search.collect(record, batch)
    search.write_report(record)
    report = json.loads((search.ROOT / record["report_path"]).read_text())
    assert report["counts"] == {
        "candidate_slots": 3, "attempted_slots": 3, "structurally_validated_task_slots": 2,
        "oracle_nop_valid_task_slots": 1, "five_valid_outcome_task_slots": 1,
        "audited_learnable_task_slots": 1, "collected_task_slots": 1,
        "invalid_task_slots": 1, "duplicate_task_slots": 1,
        "inconclusive_task_slots": 0, "generation_attempts": 4}
    assert report["elapsed_wall_sec"] == 123
    assert report["costs"]["task_generation_usd"] == pytest.approx(0.26)
    assert report["costs"]["accounted_total_usd"] == pytest.approx(0.40)
    assert report["costs"]["prompt_updates_usd"] == pytest.approx(0.05)
    assert report["candidates"][0]["stages"]["five_valid_outcomes"] is True


def test_report_uses_actual_control_and_trial_states_not_optimistic_summary(tmp_path):
    record = search.start_search()
    batch, path = prepared_export(record, tmp_path)
    task = batch["tasks"][0]
    task.update(accepted=False, status="budget_exhausted", summary={"outcome": "budget_limited"})
    for run_id, status in (("nop", "failed"), ("eval-5", "budget_exhausted")):
        state_path = path / "runs" / run_id / "state.json"
        state = store.read_json(state_path)
        state["status"] = status
        store.write_json(state_path, state)
    controller._save(batch)
    search.write_report(record)
    report = json.loads((search.ROOT / record["report_path"]).read_text())
    assert report["counts"]["oracle_nop_valid_task_slots"] == 0
    assert report["counts"]["five_valid_outcome_task_slots"] == 0
    assert report["counts"]["inconclusive_task_slots"] == 1
    assert report["counts"]["audited_learnable_task_slots"] == 0


def test_report_before_any_round_has_explicit_zero_counts():
    record = search.start_search()
    search.write_report(record)
    report = json.loads((search.ROOT / record["report_path"]).read_text())
    assert report["counts"]["candidate_slots"] == 0
    assert report["counts"]["structurally_validated_task_slots"] == 0
    assert report["counts"]["five_valid_outcome_task_slots"] == 0
    assert report["yield"]["audited_learnable_rate"] is None


def test_disabled_fixed_stagnation_limit_allows_three_no_hit_rounds():
    record = search.start_search()
    record["config"]["stagnant_rounds"] = 0
    for _ in range(3):
        round_for(record)
    search._save(record)
    assert record["stagnant_rounds"] == 3
    assert search.stopping_reason(record) is None
    assert len(record["progress_assessments"]) == 1
    assert record["progress_assessments"][0]["five_valid_outcome_slots"] == 0


def six_measured_rounds(record, *, improves=False, inconclusive=False):
    record["config"]["stagnant_rounds"] = 0
    for index in range(6):
        if index in (2, 4):
            current = prompts.current_prompt()
            prompts.create_version(f"Generation revision after round {index}", current["version"])
        batch = round_for(record, states=("completed",) * 3)
        for task in batch["tasks"]:
            task["summary"] = {"valid_runs": 5, "passes": 4 if improves and index >= 3 else 5,
                               "outcome": "too_easy"}
        if inconclusive and index == 5:
            batch["tasks"][0]["summary"] = {"valid_runs": 4, "passes": 2, "outcome": "inconclusive"}
        controller._save(batch)
    search._save(record)


def test_severe_stall_requires_multiple_revisions_and_six_measured_rounds():
    record = search.start_search()
    six_measured_rounds(record)
    assert len(record["progress_assessments"]) == 2
    assert record["severe_bottleneck"]["earlier_three_rounds"]["mean_distance_to_band"] == 2
    assert "Six completed rounds" in search.stopping_reason(record)


@pytest.mark.parametrize("options", [{"improves": True}, {"inconclusive": True}])
def test_improving_or_inconclusive_evidence_does_not_trigger_difficulty_stall(options):
    record = search.start_search()
    six_measured_rounds(record, **options)
    assert record["severe_bottleneck"] is None
    assert search.stopping_reason(record) is None


def test_resume_preserves_generation_evidence_and_reopens_score_accounting():
    record = search.start_search()
    batch = round_for(record)
    search.update_scores(record, batch)
    record.update(status="checkpoint", error="Truncated review response", stop_reason="Review incomplete",
                  completed_at=store.now(), processed_rounds=[batch["id"]])
    search._save(record)
    batch["status"] = "queued"
    task = batch["tasks"][0]
    task.update(status="queued", attempts=[{"finished": False,
        "generation": {"status": "structurally_validated", "task_dir": "/preserved/original/task"}}])
    controller._save(batch)
    (search.DATA / record["id"] / "stop").touch()
    before = (controller.DATA / "batches" / batch["id"] / "batch.json").read_bytes()
    resumed = search.resume_search(record["id"])
    assert resumed["status"] == "queued"
    assert resumed["error"] is None and resumed["stop_reason"] is None
    assert "completed_at" not in resumed
    assert resumed["processed_rounds"] == []
    assert resumed["prompt_scores"] == {}
    assert resumed["resume_events"][0]["previous_error"] == "Truncated review response"
    assert resumed["resume_events"][0]["reopened_rounds"] == [batch["id"]]
    assert not (search.DATA / record["id"] / "stop").exists()
    assert (controller.DATA / "batches" / batch["id"] / "batch.json").read_bytes() == before


@pytest.mark.parametrize("stage", ["authoring", "prompt-update"])
def test_resume_refuses_unresolved_ambiguous_paid_calls(stage):
    record = search.start_search()
    batch = round_for(record)
    record["status"] = "checkpoint"
    search._save(record)
    batch["status"] = "queued"
    if stage == "authoring":
        batch["tasks"][0].update(status="queued", attempts=[{"finished": False}])
    else:
        batch.update(update_started_at=store.now(), decision="update_deferred")
    controller._save(batch)
    with pytest.raises(ValueError, match="evidence must be resolved"):
        search.resume_search(record["id"])
    assert store.read_json(search.DATA / record["id"] / "search.json")["status"] == "checkpoint"
