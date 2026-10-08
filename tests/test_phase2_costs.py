"""Cross-component outcome and aggregate accounting contracts; no inference."""
import copy

import pytest

from phase2 import controller
from runner import store


def job(evaluations=None, *, status="completed", controls=("passed", "passed"),
        controls_passed=True, validation_passed=True):
    runs = [{"id": name, "kind": name, "status": outcome, "tests": []}
            for name, outcome in zip(("oracle", "nop"), controls)]
    runs += [{"id": f"eval-{i}", "kind": "evaluation", "status": outcome, "tests": []}
             for i, outcome in enumerate(evaluations or ["skipped"] * 5, start=1)]
    return {"id": "fixture-job", "profile": "demo", "status": status, "runs": runs,
            "controls_passed": controls_passed,
            "validation": {"passed": validation_passed, "errors": []},
            "summary": store.summarize(runs, "demo")}


@pytest.mark.parametrize("outcomes,label", [
    (["passed", "passed", "failed", "failed", "failed"], "observed_demo_band"),
    (["failed"] * 5, "too_hard_in_demo"),
    (["passed"] * 5, "too_easy_in_demo"),
    (["passed"] * 4 + ["failed"], "too_easy_in_demo"),
    (["passed", "failed", "failed", "failed", "budget_exhausted"], "budget_limited"),
    (["passed", "failed", "failed", "failed", "error"], "inconclusive"),
])
def test_runner_outcomes_have_only_demo_difficulty_labels(outcomes, label):
    summary = controller.summarize_job(job(outcomes))
    assert summary["outcome"] == label
    assert summary["learnable"] is None
    assert summary["valid_runs"] == sum(s in {"passed", "failed"} for s in outcomes)


@pytest.mark.parametrize("control_outcomes,label", [
    (("failed", "passed"), "invalid_task"),
    (("passed", "failed"), "invalid_task"),
    (("error", "passed"), "execution_error"),
    (("timeout", "passed"), "execution_error"),
])
def test_control_failure_and_infrastructure_failure_are_distinct(control_outcomes, label):
    summary = controller.summarize_job(job(status="validation_failed", controls=control_outcomes, controls_passed=False))
    assert summary["outcome"] == label
    assert summary["learnable"] is None


def test_structural_rejection_is_invalid_task_before_controls_run():
    summary = controller.summarize_job(job(status="validation_failed", controls=("skipped", "skipped"),
                                         controls_passed=False, validation_passed=False))
    assert summary["outcome"] == "invalid_task"


def test_controls_collecting_different_tests_are_invalid_even_when_both_rewards_pass():
    summary = controller.summarize_job(job(status="validation_failed", controls_passed=False))
    assert summary["outcome"] == "invalid_task"


def test_running_job_does_not_publish_partial_difficulty():
    summary = controller.summarize_job(job(["passed"] * 3 + ["running", "queued"], status="running"))
    assert summary["outcome"] == "in_progress"
    assert summary["observed_demo_band"] is None


def test_costs_include_pending_provider_reservations_and_count_each_job_once(monkeypatch):
    calls = []
    fake_jobs = {
        "original": {"runs": [{"kind": "oracle"}, {"kind": "nop"},
                              {"budget": {"spent_usd": 0.004, "reserved_usd": 0.006}}]},
        "repaired": {"runs": [{"budget": {"spent_usd": 0.002, "reserved_usd": 0.003}}]},
    }
    def fetch(identifier):
        calls.append(identifier)
        return copy.deepcopy(fake_jobs[identifier])
    monkeypatch.setattr(store, "job", fetch)
    record = {"update_cost_usd": 0.02, "tasks": [
        {"generation_cost_usd": 0.10, "review_cost_usd": 0.04,
         "attempts": [{"job_id": "original"}, {"job_id": "original"}, {"job_id": "repaired"}, {}]},
        {"generation_cost_usd": 0.03, "review_cost_usd": 0.01, "attempts": []},
    ]}
    controller._costs(record)
    assert sorted(calls) == ["original", "repaired"]
    assert record["tasks"][0]["solver_accounted_cost_usd"] == pytest.approx(0.015)
    assert record["tasks"][0]["accounted_cost_usd"] == pytest.approx(0.155)
    assert record["accounted_cost_usd"] == pytest.approx(0.215)


def test_live_accounting_reconciles_reservation_after_timeout_without_double_count(monkeypatch):
    ledger = {"spent_usd": 0.005, "reserved_usd": 0.025}
    monkeypatch.setattr(store, "job", lambda _: {"runs": [{"status": "budget_exhausted", "budget": ledger}]})
    record = {"tasks": [{"attempts": [{"job_id": "timed-out"}], "generation_cost_usd": 0.05,
                          "review_cost_usd": 0.02}], "update_cost_usd": 0.01}
    controller._costs(record)
    assert record["accounted_cost_usd"] == pytest.approx(0.11)
    ledger.update(spent_usd=0.007, reserved_usd=0)
    controller._costs(record)
    assert record["accounted_cost_usd"] == pytest.approx(0.087)
    controller._costs(record)
    assert record["accounted_cost_usd"] == pytest.approx(0.087)


def test_stage_budget_allocation_fits_each_task_and_the_batch():
    cfg = controller.settings()
    demo, author, adaptation = cfg["demo"], cfg["authoring"], cfg["adaptation"]
    count = len(controller.BRIEFS)
    per_task = author["generation_cost_usd"] + author["semantic_review_cost_usd"] + 5 * store.DEMO_BUDGET["cost_usd"]
    assert per_task + adaptation["prompt_update_cost_usd"] / count <= demo["task_cost_usd"]
    assert count * per_task + adaptation["prompt_update_cost_usd"] <= demo["batch_cost_usd"]
    assert demo["solver_timeout_sec"] == store.DEMO_BUDGET["agent_timeout_sec"]
