"""Runner contracts tested without Docker, model requests, or real job state."""

import json
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient

from api import main as api
from runner import artifacts, service, store


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A supplied example and a private job store for every test."""
    root = tmp_path / "workspace"
    source = root / "examples" / "fixture-task"
    files = {
        "instruction.md": "Write the correct answer to /app/answer.txt.\n",
        "task.toml": '[task]\ndescription = "Isolated runner fixture"\n',
        "environment/Dockerfile": "FROM scratch\n",
        "solution/solve.sh": "#!/bin/bash\nprintf 'correct' > /app/answer.txt\n",
        "tests/test.sh": "#!/bin/bash\nexit 0\n",
        "tests/test_outputs.py": "def test_answer():\n    assert True\n",
    }
    for relative, content in files.items():
        file = source / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    data = tmp_path / "job-store"
    monkeypatch.setattr(store, "ROOT", root)
    monkeypatch.setattr(store, "DATA", data)
    monkeypatch.setattr(service, "ROOT", root)
    monkeypatch.setattr(service, "DATA", data)
    monkeypatch.setenv("OPENROUTER_API_KEY", "runner-test-credential")
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://fixture.invalid/v1")
    return source


@pytest.fixture
def client(workspace):
    with TestClient(api.app) as connection:
        yield connection


def result(reward, exception=None):
    return {
        "exception_info": exception,
        "verifier_result": {"rewards": {"reward": reward}},
    }


def verifier_tests(*statuses):
    return [
        {"name": f"test_requirement_{number}", "status": status, "message": ""}
        for number, status in enumerate(statuses, start=1)
    ]


@pytest.mark.parametrize(
    "kind,reward,test_statuses,expected_status,passed",
    [
        ("evaluation", 1, ("passed", "passed"), "passed", True),
        ("evaluation", 0, ("passed", "failed"), "failed", False),
        ("oracle", 1, ("passed",), "passed", True),
        ("oracle", 0, ("failed",), "failed", False),
        ("nop", 0, ("passed", "failed"), "passed", True),
        ("nop", 1, ("passed",), "failed", False),
    ],
)
def test_verified_outcomes_have_agent_specific_expectations(
    kind, reward, test_statuses, expected_status, passed
):
    outcome = artifacts.classify(result(reward), verifier_tests(*test_statuses), kind)
    assert outcome == {
        "status": expected_status, "passed": passed, "reward": reward, "error": None
    }


@pytest.mark.parametrize(
    "exception_type,status",
    [
        ("AgentTimeoutError", "timeout"),
        ("VerifierTimeoutError", "timeout"),
        ("AuthenticationError", "error"),
        ("EnvironmentBuildError", "error"),
    ],
)
def test_harness_exceptions_never_become_model_failures(exception_type, status):
    outcome = artifacts.classify(
        result(0, {"exception_type": exception_type, "exception_message": "failed upstream"}),
        verifier_tests("failed"),
        "evaluation",
    )
    assert outcome["status"] == status
    assert outcome["passed"] is None
    assert outcome["reward"] is None
    assert exception_type in outcome["error"]


@pytest.mark.parametrize("reward", [None, 0.5, 2, "1"])
def test_nonbinary_or_missing_rewards_are_execution_errors(reward):
    outcome = artifacts.classify(result(reward), verifier_tests("passed"), "evaluation")
    assert outcome["status"] == "error"
    assert outcome["passed"] is None


@pytest.mark.parametrize("test_statuses", [(), ("error",), ("skipped",), ("passed", "error")])
def test_nop_requires_real_failing_tests_not_collection_errors(test_statuses):
    outcome = artifacts.classify(result(0), verifier_tests(*test_statuses), "nop")
    assert outcome["status"] == "error"
    assert outcome["passed"] is None


@pytest.mark.parametrize("reward,test_status", [(0, "passed"), (1, "failed")])
def test_reward_and_verifier_disagreement_is_invalid(reward, test_status):
    outcome = artifacts.classify(result(reward), verifier_tests(test_status), "evaluation")
    assert outcome["status"] == "error"
    assert "disagrees" in outcome["error"]


@pytest.mark.parametrize("original_status,expected_status", [("failed", "failed"), ("error", "error")])
def test_all_ctrf_reports_contribute_to_the_result(tmp_path, original_status, expected_status):
    trial = tmp_path / "trial"
    store.write_json(trial / "verifier" / "original-ctrf.json", {
        "results": {"tests": [{"name": "test_answer", "status": original_status, "trace": "Original repository assertion"}]}
    })
    store.write_json(trial / "verifier" / "ctrf.json", {
        "results": {"tests": [{"name": "test_answer", "status": "passed", "message": "Additional task assertion"}]}
    })
    tests = artifacts.test_results(trial)
    assert len(tests) == 2
    assert {test["name"] for test in tests} == {"ctrf: test_answer", "original-ctrf: test_answer"}
    assert {test["message"] for test in tests} == {"Original repository assertion", "Additional task assertion"}
    assert artifacts.classify(result(0), tests, "evaluation")["status"] == expected_status


@pytest.mark.parametrize("raw_status", ["setup_failed", "teardown_failed"])
def test_pytest_fixture_failures_are_not_model_failures(tmp_path, raw_status):
    trial = tmp_path / "trial"
    store.write_json(trial / "verifier" / "ctrf.json", {
        "results": {"tests": [{"name": "test_answer", "status": "failed", "raw_status": raw_status}]}
    })
    tests = artifacts.test_results(trial)
    assert tests[0]["raw_status"] == raw_status
    assert tests[0]["status"] == "error"
    assert artifacts.classify(result(0), tests, "evaluation")["status"] == "error"
    assert artifacts.classify(result(0), tests, "nop")["status"] == "error"


@pytest.mark.parametrize("invalid_report", ['{"results":{"tests":[]}}', '{}', '{invalid-json'])
def test_empty_or_malformed_report_cannot_be_hidden_by_another_report(tmp_path, invalid_report):
    trial = tmp_path / "trial"
    store.write_json(trial / "verifier" / "ctrf.json", {"results": {"tests": verifier_tests("passed")}})
    (trial / "verifier" / "original-ctrf.json").write_text(invalid_report)
    tests = artifacts.test_results(trial)
    assert len(tests) == 2
    invalid = next(test for test in tests if test["status"] == "error")
    assert invalid["name"].startswith("original-ctrf")
    assert "report" in invalid["message"]
    assert artifacts.classify(result(1), tests, "evaluation")["status"] == "error"


def test_turns_exclude_summaries_and_order_continuations_numerically(tmp_path):
    run_dir = tmp_path / "run"
    trial = run_dir / "harbor" / "job" / "trial"
    store.write_json(trial / "config.json", {})

    def step(message, **extra):
        return {
            "source": "agent", "message": message,
            "tool_calls": [{"arguments": {"commands": [{"keystrokes": f"echo {message}", "duration_sec": 1}]}}],
            "observation": {"results": [{"content": [{"type": "text", "text": f"output: {message}"}]}]},
            **extra,
        }

    store.write_json(trial / "agent" / "trajectory.json", {"steps": [
        {"source": "system", "message": "System prompt"},
        {"source": "user", "message": "Task instruction"},
        step("initial"),
    ]})
    for number in [10, 2, 1]:
        store.write_json(trial / "agent" / f"trajectory.cont-{number}.json", {"steps": [
            step("copied", is_copied_context=True), step(f"continuation-{number}"),
        ]})
    store.write_json(trial / "agent" / "trajectory.summarization-1-summary.json", {
        "steps": [step("internal summary, not a solver turn")]
    })

    episodes = artifacts.episodes(run_dir)
    assert [episode["analysis"] for episode in episodes] == ["initial", "continuation-1", "continuation-2", "continuation-10"]
    assert [episode["index"] for episode in episodes] == [1, 2, 3, 4]
    assert episodes[-1]["commands"] == [{"command": "echo continuation-10", "duration": 1}]
    assert episodes[-1]["output"] == "output: continuation-10"


def test_turn_commands_are_redacted_and_completion_is_not_a_command(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "runner-test-credential")
    run_dir = tmp_path / "run"
    trial = run_dir / "harbor" / "job" / "trial"
    store.write_json(trial / "config.json", {})
    agent = trial / "agent"
    agent.mkdir()
    # Write raw data, bypassing the store's own redaction, to exercise the reader.
    (agent / "trajectory.json").write_text(json.dumps({"steps": [
        {"source": "agent", "message": "Run a command", "tool_calls": [
            {"arguments": {"commands": [{"keystrokes": "echo runner-test-credential"}]}}
        ]},
        {"source": "agent", "message": "Complete", "tool_calls": [
            {"function_name": "mark_task_complete", "arguments": {}}
        ]},
    ]}))
    episodes = artifacts.episodes(run_dir)
    assert episodes[0]["commands"][0]["command"] == "echo [REDACTED]"
    assert episodes[1]["commands"] == []


def test_job_snapshots_survive_source_edits_and_have_seven_independent_slots(workspace):
    first = store.create_job("fixture-task")
    first_dir = store.directory(first["id"])
    original_instruction = (workspace / "instruction.md").read_text()
    (workspace / "instruction.md").write_text("Different task version.\n")
    second = store.create_job("fixture-task")

    assert (first_dir / "task" / "instruction.md").read_text() == original_instruction
    assert first["task_sha256"] != second["task_sha256"]
    assert first["run_ids"] == ["oracle", "nop", "eval-1", "eval-2", "eval-3", "eval-4", "eval-5"]
    assert len({run["id"] for run in first["runs"]}) == 7
    assert [run["number"] for run in first["runs"] if run["kind"] == "evaluation"] == [1, 2, 3, 4, 5]
    assert all(run["status"] == "queued" for run in first["runs"])
    assert (first_dir / "runs" / "oracle" / "state.json").is_file()
    assert first["model"] == "openrouter/z-ai/glm-5.3-flash"
    assert first["reasoning_effort"] == "high"
    assert first["max_concurrency"] == 5
    assert first["api_base"] == "https://fixture.invalid/v1"


@pytest.mark.parametrize("task_id", ["not-an-example", "../fixture-task", "/etc/passwd"])
def test_unknown_task_ids_do_not_publish_jobs(workspace, task_id):
    with pytest.raises(ValueError, match="Unknown example task"):
        store.create_job(task_id)
    assert store.jobs() == []


def test_controls_only_skips_all_paid_slots(workspace):
    job = store.create_job("fixture-task", mode="controls")
    assert [run["status"] for run in job["runs"][:2]] == ["queued", "queued"]
    assert all(run["status"] == "skipped" for run in job["runs"][2:])
    assert job["summary"]["learnable"] is None


@pytest.mark.parametrize("passes,learnable", [(0, False), (1, True), (2, True), (3, True), (4, False), (5, False)])
def test_learnability_requires_the_full_five_valid_trials(passes, learnable):
    evaluations = [
        {"kind": "evaluation", "status": "passed" if index < passes else "failed"}
        for index in range(5)
    ]
    controls = [{"kind": "oracle", "status": "passed"}, {"kind": "nop", "status": "passed"}]
    summary = store.summarize(controls + evaluations)
    assert summary == {
        "passes": passes, "failures": 5 - passes, "valid_runs": 5,
        "total_runs": 5, "learnable": learnable,
    }
    assert store.summarize(controls + evaluations[:4])["learnable"] is None


@pytest.mark.parametrize("invalid_status", ["error", "timeout", "queued", "running", "cancelled", "skipped", "interrupted"])
def test_invalid_trials_are_excluded_even_when_pass_count_is_in_range(invalid_status):
    summary = store.summarize([
        {"kind": "evaluation", "status": status}
        for status in ["passed", "passed", "failed", "failed", invalid_status]
    ])
    assert summary["passes"] == 2
    assert summary["failures"] == 2
    assert summary["valid_runs"] == 4
    assert summary["learnable"] is None


def test_harbor_evaluation_command_pins_the_protocol_without_a_turn_cap(workspace):
    job = store.create_job("fixture-task")
    job_dir = store.directory(job["id"])
    command = service.command(job_dir, "eval-1", "evaluation")
    assert command[command.index("-p") + 1] == str(job_dir / "task")
    assert command[command.index("-a") + 1] == "terminus-2"
    assert command[command.index("-m") + 1] == "openrouter/z-ai/glm-5.3-flash"
    assert command[command.index("--ak") + 1] == "reasoning_effort=high"
    assert command[command.index("--n-attempts") + 1] == "1"
    assert command[command.index("--n-concurrent") + 1] == "1"
    assert command[command.index("--max-retries") + 1] == "0"
    assert command[command.index("--jobs-dir") + 1] == str(job_dir / "runs" / "eval-1" / "harbor")
    assert "--force-build" in command
    assert not any("turn" in option or "episode" in option or "timeout" in option for option in command if option.startswith("-"))
    assert not any("max_turns=" in option or "max_episodes=" in option for option in command)
    assert service.CONCURRENCY == 5


def test_five_solver_attempts_can_run_concurrently(workspace, monkeypatch):
    job = store.create_job("fixture-task")
    job_dir = store.directory(job["id"])
    barrier = threading.Barrier(5)
    lock = threading.Lock()
    active = 0
    peak = 0
    finished = []

    def trial(_job_dir, run_id):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=2)
            with lock:
                finished.append(run_id)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(service, "execute", trial)
    ids = [f"eval-{number}" for number in range(1, 6)]
    service.run_group(job_dir, ids)
    assert peak == 5
    assert sorted(finished) == ids


@pytest.mark.parametrize("kind", ["oracle", "nop"])
def test_control_commands_make_no_model_requests(workspace, kind):
    job = store.create_job("fixture-task")
    command = service.command(store.directory(job["id"]), kind, kind)
    assert command[command.index("-a") + 1] == kind
    assert "-m" not in command
    assert "--ak" not in command
    assert "--force-build" in command


def write_run_outcome(job_dir, run_id, status, test_names=("test_answer",)):
    path = job_dir / "runs" / run_id / "state.json"
    state = store.read_json(path)
    state.update(
        status=status,
        passed=status == "passed",
        tests=[{"name": name, "status": "passed", "message": ""} for name in test_names],
    )
    store.write_json(path, state)


@pytest.mark.parametrize("invalid_control", ["oracle", "nop", "mismatched-tests"])
def test_paid_runs_are_gated_on_both_controls_and_matching_test_sets(workspace, monkeypatch, invalid_control):
    job = store.create_job("fixture-task")
    job_dir = store.directory(job["id"])
    calls = []
    monkeypatch.setattr(service, "validate", lambda _: {"passed": True, "errors": [], "warnings": []})

    def fake_group(path, run_ids):
        calls.append(run_ids)
        for run_id in run_ids:
            names = ("different_test",) if invalid_control == "mismatched-tests" and run_id == "nop" else ("test_answer",)
            write_run_outcome(path, run_id, "failed" if run_id == invalid_control else "passed", names)

    monkeypatch.setattr(service, "run_group", fake_group)
    service.process_job(job_dir)
    final = store.job(job["id"])
    assert calls == [["oracle", "nop"]]
    assert final["status"] == "validation_failed"
    assert all(run["status"] == "skipped" for run in final["runs"][2:])
    assert final["summary"]["learnable"] is None


def test_successful_controls_schedule_exactly_five_evaluations(workspace, monkeypatch):
    job = store.create_job("fixture-task")
    calls = []
    monkeypatch.setattr(service, "validate", lambda _: {"passed": True, "errors": [], "warnings": []})

    def fake_group(path, run_ids):
        calls.append(run_ids)
        for run_id in run_ids:
            write_run_outcome(path, run_id, "failed" if run_id in {"eval-3", "eval-4", "eval-5"} else "passed")

    monkeypatch.setattr(service, "run_group", fake_group)
    service.process_job(store.directory(job["id"]))
    final = store.job(job["id"])
    assert calls == [["oracle", "nop"], ["eval-1", "eval-2", "eval-3", "eval-4", "eval-5"]]
    assert final["status"] == "completed"
    assert final["summary"]["passes"] == 2
    assert final["summary"]["learnable"] is True


def test_completed_harbor_results_are_reconciled_without_repeating_paid_work(workspace, monkeypatch):
    job = store.create_job("fixture-task")
    job_dir = store.directory(job["id"])
    trial = job_dir / "runs" / "eval-1" / "harbor" / "job" / "trial"
    store.write_json(trial / "config.json", {})
    store.write_json(trial / "result.json", result(1))
    store.write_json(trial / "verifier" / "ctrf.json", {"results": {"tests": verifier_tests("passed")}})

    def unexpected_process(*args, **kwargs):
        pytest.fail("A saved Harbor result must not start another process")

    monkeypatch.setattr(service.subprocess, "Popen", unexpected_process)
    service.execute(job_dir, "eval-1")
    saved = store.read_json(job_dir / "runs" / "eval-1" / "state.json")
    assert saved["status"] == "passed"
    assert saved["reward"] == 1
    service.execute(job_dir, "eval-1")
    assert store.read_json(job_dir / "runs" / "eval-1" / "state.json") == saved


def test_interrupted_running_trial_is_not_silently_retried(workspace, monkeypatch):
    job = store.create_job("fixture-task")
    job_dir = store.directory(job["id"])
    state_path = job_dir / "runs" / "eval-1" / "state.json"
    state = store.read_json(state_path)
    state.update(status="running", pid=123456)
    store.write_json(state_path, state)
    monkeypatch.setattr(service, "process_alive", lambda *_: False)
    monkeypatch.setattr(service, "recover_pid", lambda *_: None)
    monkeypatch.setattr(service.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("Unexpected paid retry"))
    service.execute(job_dir, "eval-1")
    saved = store.read_json(state_path)
    assert saved["status"] == "interrupted"
    assert saved["passed"] is None
    assert store.job(job["id"])["summary"]["valid_runs"] == 0


def test_missing_evaluation_report_is_detected_against_oracle(workspace, monkeypatch):
    job = store.create_job("fixture-task")
    job_dir = store.directory(job["id"])
    write_run_outcome(job_dir, "oracle", "passed", ("ctrf: test_answer", "original-ctrf: test_answer"))
    trial = job_dir / "runs" / "eval-1" / "harbor" / "job" / "trial"
    store.write_json(trial / "config.json", {})
    store.write_json(trial / "result.json", result(1))
    store.write_json(trial / "verifier" / "ctrf.json", {
        "results": {"tests": [{"name": "test_answer", "status": "passed"}]}
    })
    monkeypatch.setattr(service.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("Unexpected paid retry"))
    service.execute(job_dir, "eval-1")
    saved = store.read_json(job_dir / "runs" / "eval-1" / "state.json")
    assert saved["status"] == "error"
    assert saved["passed"] is None
    assert "different tests" in saved["error"]
    assert store.job(job["id"])["summary"]["valid_runs"] == 0


def test_restart_cancellation_drains_running_trials_before_finishing_the_job(workspace, monkeypatch):
    job = store.create_job("fixture-task")
    job_dir = store.directory(job["id"])
    job_path = job_dir / "job.json"
    record = store.read_json(job_path)
    record["status"] = "running"
    store.write_json(job_path, record)
    for run_id in ["oracle", "nop", "eval-1"]:
        write_run_outcome(job_dir, run_id, "passed")
    for run_id in ["eval-2", "eval-3"]:
        write_run_outcome(job_dir, run_id, "running")
    (job_dir / "cancel").touch()
    calls = []

    def drain_running(path, run_ids):
        calls.append(run_ids)
        # Recovery must wait for active processes before publishing a terminal job
        # or changing queued slots. Completed work must not be rescheduled.
        assert store.read_json(job_path)["status"] == "running"
        assert all(store.read_json(path / "runs" / run_id / "state.json")["status"] == "queued"
                   for run_id in ["eval-4", "eval-5"])
        for run_id in run_ids:
            state_path = path / "runs" / run_id / "state.json"
            state = store.read_json(state_path)
            state.update(status="cancelled", passed=None, completed_at=store.now())
            store.write_json(state_path, state)

    monkeypatch.setattr(service, "run_group", drain_running)
    monkeypatch.setattr(service, "validate", lambda _: pytest.fail("Cancellation must not revalidate or start new work"))
    service.process_job(job_dir)
    final = store.job(job["id"])
    assert calls == [["eval-2", "eval-3"]]
    assert final["status"] == "cancelled"
    assert [run["status"] for run in final["runs"]] == ["passed", "passed", "passed", "cancelled", "cancelled", "cancelled", "cancelled"]
    assert final["summary"]["valid_runs"] == 1
    assert final["summary"]["learnable"] is None


def test_api_exposes_examples_jobs_and_run_details(client):
    examples = client.get("/api/examples").json()["examples"]
    assert examples[0]["id"] == "fixture-task"
    assert "answer.txt" in examples[0]["instruction"]
    response = client.post("/api/jobs", json={"task_id": "fixture-task", "mode": "full"})
    assert response.status_code == 202
    job = response.json()
    assert len(job["runs"]) == 7
    assert client.get("/api/jobs").json()["jobs"][0]["id"] == job["id"]
    assert client.get(f"/api/jobs/{job['id']}").json()["summary"]["valid_runs"] == 0
    detail = client.get(f"/api/jobs/{job['id']}/runs/eval-1").json()
    assert detail["episodes"] == []
    assert detail["log"] == ""
    assert detail["verifier_output"] == ""
    assert client.get(f"/api/jobs/{job['id']}/runs/unknown").status_code == 404


def test_api_rejects_unknown_tasks_and_modes_without_creating_jobs(client):
    assert client.post("/api/jobs", json={"task_id": "absent", "mode": "full"}).status_code == 422
    assert client.post("/api/jobs", json={"task_id": "fixture-task", "mode": "adaptive"}).status_code == 422
    assert client.get("/api/jobs/invalid").status_code == 404
    assert store.jobs() == []


def test_controls_remain_available_without_a_model_key(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert client.post("/api/jobs", json={"task_id": "fixture-task", "mode": "full"}).status_code == 503
    assert store.jobs() == []
    assert client.post("/api/jobs", json={"task_id": "fixture-task", "mode": "controls"}).status_code == 202


def test_health_reports_three_slots_without_exposing_credentials(client, monkeypatch):
    monkeypatch.setattr(api.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "27.0.0", ""))
    response = client.get("/api/health")
    health = response.json()
    assert health["docker"] is True
    assert health["key_present"] is True
    assert health["max_concurrency"] == 5
    assert health["active_runs"] == 0
    assert health["worker_alive"] is False
    assert health["status"] == "degraded"
    assert "runner-test-credential" not in response.text


def test_untrusted_host_header_is_rejected(client):
    assert client.get("/api/jobs", headers={"Host": "unrelated.invalid"}).status_code == 400


def test_api_cancellation_marks_the_selected_job_only(client):
    first = client.post("/api/jobs", json={"task_id": "fixture-task", "mode": "controls"}).json()
    second = client.post("/api/jobs", json={"task_id": "fixture-task", "mode": "controls"}).json()
    assert client.post(f"/api/jobs/{first['id']}/cancel").status_code == 200
    assert (store.directory(first["id"]) / "cancel").exists()
    assert not (store.directory(second["id"]) / "cancel").exists()


def test_cross_origin_pages_cannot_launch_paid_trials(client):
    response = client.post(
        "/api/jobs", json={"task_id": "fixture-task", "mode": "full"},
        headers={"Origin": "https://unrelated.invalid"},
    )
    assert response.status_code == 403
    assert store.jobs() == []
