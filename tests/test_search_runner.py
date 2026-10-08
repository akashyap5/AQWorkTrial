"""Search protocol and truthful classifications without paid inference."""
import copy

import pytest

from runner import service, store
from runner.budget_proxy import Ledger


@pytest.fixture
def search_job(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-secret")
    source = tmp_path / "task"
    source.mkdir()
    (source / "instruction.md").write_text("Repair the offline build dependency graph.")
    (source / "README.md").write_text("# Build Dependency Repair\n\nFix dependency ordering and cache invalidation.")
    return store.create_job("build-dependency-repair", task_path=source, profile="search",
                            metadata={"prompt_version": "v0004", "family": "build-tooling"})


def test_search_profile_freezes_protocol_and_budget_without_affecting_demo(search_job):
    assert search_job["profile"] == "search"
    assert search_job["budget"] == store.SEARCH_BUDGET
    assert search_job["budget"]["agent_timeout_sec"] == 600
    assert search_job["budget"]["cost_usd"] == .20
    assert search_job["budget"]["max_completion_tokens"] == 131072
    assert search_job["model"] == "openrouter/z-ai/glm-5.3-flash"
    assert search_job["reasoning_effort"] == "high"
    assert search_job["max_concurrency"] == 5
    assert len([r for r in search_job["runs"] if r["kind"] == "evaluation"]) == 5
    assert store.DEMO_BUDGET["agent_timeout_sec"] == 300
    assert store.DEMO_BUDGET["max_completion_tokens"] == 16384
    search_job["budget"]["max_price_per_million"]["prompt"] = 99
    assert store.SEARCH_BUDGET["max_price_per_million"]["prompt"] == .3


def test_search_harbor_config_bounds_only_agent_execution_and_has_no_turn_cap(search_job):
    directory = store.directory(search_job["id"])
    command = service.command(directory, "eval-1", "evaluation", "http://127.0.0.1:123/v1")
    config = store.read_json(directory / "runs/eval-1/search-config.json")
    assert config == {"agents": [{"name": "terminus-2", "model_name": store.MODEL,
        "override_timeout_sec": 600,
        "kwargs": {"reasoning_effort": "high", "api_base": "http://127.0.0.1:123/v1"}}]}
    assert "--timeout-multiplier" not in command and "--ak" not in command
    assert "max_turns" not in str(config)
    assert command[command.index("--max-retries") + 1] == "0"
    assert "--config" not in service.command(directory, "oracle", "oracle")
    with pytest.raises(ValueError, match="metered"):
        service.command(directory, "eval-1", "evaluation")


def runs(passes=2):
    return [{"kind": "oracle", "status": "passed"}, {"kind": "nop", "status": "passed"}] + [
        {"kind": "evaluation", "status": "passed" if n < passes else "failed"}
        for n in range(5)]


@pytest.mark.parametrize("passes,expected", [(0, False), (1, True), (2, True), (3, True), (4, False), (5, False)])
def test_search_canonical_band_requires_five_valid_attempts(passes, expected):
    summary = store.summarize(runs(passes), "search")
    assert summary["learnable"] is expected
    assert summary["valid_runs"] == 5
    assert summary["passes"] == passes
    assert summary["inconclusive_runs"] == 0


@pytest.mark.parametrize("invalid", ["timeout", "budget_exhausted", "error", "interrupted", "cancelled", "skipped"])
def test_inconclusive_search_runs_never_count_as_failures(invalid):
    results = runs()
    results[-1]["status"] = invalid
    summary = store.summarize(results, "search")
    assert summary["learnable"] is None
    assert summary["passes"] == 2 and summary["failures"] == 2
    assert summary["valid_runs"] == 4 and summary["inconclusive_runs"] == 1


def test_search_cannot_be_learnable_without_passing_controls():
    results = runs()
    results[0]["status"] = "failed"
    assert store.summarize(results, "search")["learnable"] is None
    assert store.summarize(results[2:], "search")["learnable"] is None


@pytest.mark.parametrize("reason", ["cost", "time"])
def test_search_budget_overrides_apparent_verifier_failure_as_inconclusive(reason):
    state = {"kind": "evaluation", "status": "failed", "passed": False, "reward": 0}
    result = {"exception_info": {"exception_type": "AgentTimeoutError"}} if reason == "time" else {}
    ledger = {"spent_usd": .02, "reserved_usd": .07, "stop_reason": "cost" if reason == "cost" else None}
    service.apply_budget_outcome(state, result, {"profile": "search"}, ledger)
    assert state["status"] == "budget_exhausted"
    assert state["failure_reason"] == f"budget_{reason}"
    assert state["budget_reason"] == reason
    assert state["passed"] is None and state["reward"] is None
    assert state["cost_usd"] == .02 and state["reserved_cost_usd"] == .07


def test_search_preserves_verifier_timeouts_and_billing_errors_as_infrastructure_errors():
    state = {"kind": "evaluation", "status": "timeout", "passed": None}
    service.apply_budget_outcome(state, {"exception_info": {"exception_type": "VerifierTimeoutError"}}, {"profile": "search"}, {})
    assert state["status"] == "timeout"
    service.apply_budget_outcome(state, {}, {"profile": "search"}, {"accounting_error": "Missing provider usage"})
    assert state["status"] == "error" and state["failure_reason"] == "cost_accounting_error"


def request():
    return {"model": "z-ai/glm-5.3-flash", "messages": [{"role": "user", "content": "Repair the task."}]}


def test_search_gateway_preserves_native_output_allowance_when_affordable(tmp_path):
    ledger = Ledger(tmp_path / "budget.json", copy.deepcopy(store.SEARCH_BUDGET))
    payload, index = ledger.prepare(request())
    assert payload["max_tokens"] == 131072
    assert not ledger.data["requests"][index]["cost_limited_completion"]
    assert ledger.data["reserved_usd"] < .20


def test_cost_clamped_completion_truncation_is_a_budget_stop_not_solver_failure(tmp_path):
    ledger = Ledger(tmp_path / "budget.json", copy.deepcopy(store.SEARCH_BUDGET))
    ledger.data["spent_usd"] = .15
    payload, index = ledger.prepare(request())
    assert 1024 < payload["max_tokens"] < 131072
    assert ledger.data["requests"][index]["cost_limited_completion"]
    assert ledger.data["spent_usd"] + ledger.data["reserved_usd"] <= .20
    ledger.settle(index, {"id": "fixture", "choices": [{"finish_reason": "length"}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": payload["max_tokens"],
                                   "cost": .01, "is_byok": False}})
    assert ledger.data["stop_reason"] == "cost"
    assert ledger.data["reserved_usd"] == 0


def test_short_completed_reply_does_not_exhaust_a_clamped_reservation(tmp_path):
    ledger = Ledger(tmp_path / "budget.json", copy.deepcopy(store.SEARCH_BUDGET))
    ledger.data["spent_usd"] = .15
    _, index = ledger.prepare(request())
    ledger.settle(index, {"choices": [{"finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 200, "cost": .001, "is_byok": False}})
    assert ledger.data["stop_reason"] is None


def test_search_reconciliation_exposes_actual_failure_and_agent_only_duration(search_job):
    job_dir = store.directory(search_job["id"])
    trial = job_dir / "runs/eval-1/harbor/fixture/trial"
    store.write_json(trial / "config.json", {})
    store.write_json(trial / "result.json", {
        "verifier_result": {"rewards": {"reward": 0}}, "exception_info": None,
        "environment_setup": {"started_at": "2026-10-06T00:00:00Z", "finished_at": "2026-10-06T00:01:00Z"},
        "agent_execution": {"started_at": "2026-10-06T00:01:00Z", "finished_at": "2026-10-06T00:04:00Z"},
        "verifier": {"started_at": "2026-10-06T00:04:00Z", "finished_at": "2026-10-06T00:04:10Z"}})
    store.write_json(trial / "verifier/ctrf.json", {"results": {"tests": [
        {"name": "test_transitive_invalidation", "status": "failed", "message": "Stale dependent artifact"}]}})
    store.write_json(job_dir / "runs/eval-1/budget.json", {"spent_usd": .006, "reserved_usd": 0})
    service.execute(job_dir, "eval-1")  # Reads the existing completed trial; never launches Harbor.
    state = store.read_json(job_dir / "runs/eval-1/state.json")
    assert state["status"] == "failed" and state["failure_reason"] == "verified_tests_failed"
    assert state["tests"][0]["message"] == "Stale dependent artifact"
    assert state["phase_durations_sec"] == {"environment_setup": 60, "agent_execution": 180, "verifier": 10}
    assert state["cost_usd"] == .006 and state["reserved_cost_usd"] == 0


@pytest.mark.parametrize("last_status,job_status", [("failed", "completed"), ("budget_exhausted", "error")])
def test_search_job_only_completes_when_all_five_outcomes_are_valid(search_job, monkeypatch, last_status, job_status):
    job_dir = store.directory(search_job["id"])
    monkeypatch.setattr(service, "validate", lambda _: {"passed": True, "errors": []})
    def fake_group(path, ids):
        for run_id in ids:
            state_path = path / "runs" / run_id / "state.json"
            state = store.read_json(state_path)
            state.update(status=(last_status if run_id == "eval-5" else "passed"),
                         tests=[{"name": "test_order", "status": "passed"}])
            store.write_json(state_path, state)
    monkeypatch.setattr(service, "run_group", fake_group)
    service.process_job(job_dir)
    result = store.job(search_job["id"])
    assert result["status"] == job_status
    if last_status == "budget_exhausted":
        assert result["summary"]["learnable"] is None
        assert result["summary"]["failures"] == 0
