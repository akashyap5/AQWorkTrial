"""A provisional band is not evidence of five valid solver completions."""
import json

import pytest

from generator import fast_author


@pytest.mark.parametrize(
    "initial,final,expected_valid,expected_passes,decided_too_easy",
    [
        (["passed", "failed", "failed", "running", "running"],
         ["passed", "failed", "failed", "timeout", "passed"], 4, 2, False),
        (["passed", "failed", "failed", "running", "running"],
         ["passed", "failed", "failed", "passed", "failed"], 5, 2, False),
        # Four passes already rule out 1-3/5: the last run is cancelled and the probe reports "too easy".
        (["passed", "passed", "passed", "passed", "running"],
         ["passed", "passed", "passed", "passed", "timeout"], 4, 4, True),
    ],
)
def test_probe_waits_for_real_five_run_outcomes(
    tmp_path, monkeypatch, initial, final, expected_valid, expected_passes, decided_too_easy
):
    monkeypatch.setattr(fast_author.store, "DATA", tmp_path)
    monkeypatch.setattr(fast_author, "RUNS", tmp_path)
    # Even an old operator flag must not cancel the remaining paid runs.
    (tmp_path / "cancel_early.flag").write_text("old flag")
    job_dir = tmp_path / "jobs" / "probe-job"
    run_ids = ["oracle", "nop"] + [f"eval-{n}" for n in range(1, 6)]
    for run_id in run_ids:
        (job_dir / "runs" / run_id).mkdir(parents=True)

    def persist(statuses, status):
        for run_id, result in zip(run_ids, ["passed", "passed"] + statuses):
            (job_dir / "runs" / run_id / "state.json").write_text(
                json.dumps({"status": result, "tests": []})
            )
        (job_dir / "job.json").write_text(json.dumps({
            "status": status, "controls_passed": True, "run_ids": run_ids,
        }))

    persist(initial, "running")
    waits = []

    def finish_after_wait(seconds):
        waits.append(seconds)
        persist(final, "completed" if expected_valid == 5 else "error")

    monkeypatch.setattr(fast_author.time, "sleep", finish_after_wait)
    result = fast_author.probe(tmp_path / "task", "example", attach="probe-job")

    assert waits == [10], "A partial band must wait for the outstanding attempts"
    assert result["valid"] == expected_valid
    assert result["passes"] == expected_passes
    assert not result.get("early")
    # A provisional 1-3/5 never cancels paid runs; only a decided "too easy" stops the remaining one.
    assert (job_dir / "cancel").exists() == decided_too_easy
    assert result["early_too_easy"] == decided_too_easy
