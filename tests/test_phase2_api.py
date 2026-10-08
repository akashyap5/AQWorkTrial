"""Phase-two HTTP boundaries; no worker, Docker, or provider requests."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import main
from phase2 import controller as real_controller
from runner import store


@pytest.fixture
def api(monkeypatch):
    calls = []
    snapshot = {"current_prompt": {"version": "v000002", "text": "A candidate", "status": "candidate"},
                "batches": [], "worker_alive": True}

    def start():
        calls.append("start")
        return {"id": "abcdef0123456789", "status": "queued", "prompt_version": "v000002"}

    def stop(batch_id):
        calls.append(("stop", batch_id))
        return {"id": batch_id, "status": "cancelling"}

    controller = SimpleNamespace(status=lambda: snapshot, start_batch=start, stop_batch=stop)
    monkeypatch.setattr(main, "phase2_controller", lambda: controller)
    monkeypatch.setenv("OPENROUTER_API_KEY", "phase2-fixture-key")
    with TestClient(main.app) as client:
        yield client, controller, calls


def test_status_is_read_only_and_reports_latest_candidate(api):
    client, _, calls = api
    response = client.get("/api/phase2")
    assert response.status_code == 200
    assert response.json()["current_prompt"]["version"] == "v000002"
    assert response.json()["current_prompt"]["status"] == "candidate"
    assert response.headers["cache-control"] == "no-store"
    assert calls == []


def test_start_submits_once_without_accepting_prompt_or_budget_overrides(api):
    client, _, calls = api
    response = client.post("/api/phase2/batches", json={"prompt_version": "v000001", "budget": 999})
    assert response.status_code == 202
    assert response.json()["prompt_version"] == "v000002"
    assert response.json()["status"] == "queued"
    assert calls == ["start"]


def test_missing_key_does_not_submit_a_batch(api, monkeypatch):
    client, _, calls = api
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert client.post("/api/phase2/batches").status_code == 503
    assert calls == []


def test_active_batch_conflict_is_explained_without_exposing_credentials(api):
    client, controller, _ = api

    def fail():
        raise ValueError("Active batch; phase2-fixture-key")

    controller.start_batch = fail
    response = client.post("/api/phase2/batches")
    assert response.status_code == 409
    assert response.json()["detail"] == "Active batch; [REDACTED]"


def test_stop_routes_only_the_named_batch(api):
    client, _, calls = api
    response = client.post("/api/phase2/batches/abcdef0123456789/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelling"
    assert calls == [("stop", "abcdef0123456789")]


def test_missing_batch_is_not_found(api):
    client, controller, _ = api

    def missing(batch_id):
        raise KeyError(batch_id)

    controller.stop_batch = missing
    assert client.post("/api/phase2/batches/unknown/stop").status_code == 404


@pytest.mark.parametrize("endpoint", ["/api/phase2/batches", "/api/phase2/batches/abcdef0123456789/stop"])
def test_cross_origin_requests_cannot_start_or_stop_work(api, endpoint):
    client, _, calls = api
    response = client.post(endpoint, headers={"Origin": "https://outside.example"})
    assert response.status_code == 403
    assert calls == []


def test_foreign_host_cannot_read_local_prompt_history(api):
    client, _, calls = api
    assert client.get("/api/phase2", headers={"Host": "outside.example"}).status_code == 400
    assert calls == []


@pytest.fixture
def preview_workspace(api, tmp_path, monkeypatch):
    client, controller, calls = api
    data = tmp_path / "data"
    monkeypatch.setattr(store, "DATA", data)
    monkeypatch.setattr(real_controller, "DATA", data / "phase2")
    batch_id = "1234567890abcdef"
    job_id = "abcdef0123456789"
    batch_dir = data / "phase2" / "batches" / batch_id
    batch_dir.mkdir(parents=True)
    (batch_dir / "batch.json").write_text("{}")
    task_dir = data / "jobs" / job_id / "task"
    task_dir.mkdir(parents=True)
    (task_dir.parent / "job.json").write_text("{}")
    task = {"id": "generated-task", "job_id": job_id}
    snapshot = {"batches": [{"id": batch_id, "tasks": [task]}]}
    controller.status = lambda: snapshot
    controller._path = real_controller._path
    return client, task_dir, batch_dir, snapshot, calls


def test_generated_preview_uses_safe_markdown_from_the_frozen_snapshot(preview_workspace, tmp_path):
    client, task_dir, _, snapshot, calls = preview_workspace
    (task_dir / "README.md").write_text(
        '# Frozen task\n\n<script>alert("x")</script>\n\n'
        '![external](https://external.example/image.png)\n\n'
        '[unsafe](javascript:alert(1))\n\nphase2-fixture-key\n'
    )
    (task_dir / "instruction.md").write_text("Return the exact total.\n")
    mutable_source = tmp_path / "mutable-source"
    mutable_source.mkdir()
    (mutable_source / "README.md").write_text("Changed after evaluation")
    snapshot["batches"][0]["tasks"][0]["task_dir"] = str(mutable_source)

    response = client.get("/api/phase2")
    assert response.status_code == 200
    task = response.json()["batches"][0]["tasks"][0]
    assert "<h1>Frozen task</h1>" in task["readme_html"]
    assert "&lt;script&gt;" in task["readme_html"]
    assert "<script" not in task["readme_html"]
    assert "<img" not in task["readme_html"]
    assert 'href="javascript:' not in task["readme_html"]
    assert "phase2-fixture-key" not in task["readme_html"]
    assert "[REDACTED]" in task["readme_html"]
    assert "Changed after evaluation" not in task["readme_html"]
    assert task["instruction"] == "Return the exact total.\n"
    assert "readme_html" not in snapshot["batches"][0]["tasks"][0]
    assert calls == []


def test_generated_preview_omits_missing_files_and_never_follows_outside_symlinks(preview_workspace, tmp_path):
    client, task_dir, _, _, _ = preview_workspace
    external = tmp_path / "private.txt"
    external.write_text("Host-private content")
    (task_dir / "README.md").symlink_to(external)
    task = client.get("/api/phase2").json()["batches"][0]["tasks"][0]
    assert "readme_html" not in task
    assert "instruction" not in task
    assert "Host-private content" not in json.dumps(task)


@pytest.mark.parametrize("job_id", [None, "../outside", "0000000000000000"])
def test_no_preview_is_trusted_without_an_existing_snapshot(preview_workspace, job_id):
    client, _, _, snapshot, _ = preview_workspace
    snapshot["batches"][0]["tasks"][0].update(
        job_id=job_id, readme_html="<script>unsafe()</script>", instruction="Unverified author text"
    )
    task = client.get("/api/phase2").json()["batches"][0]["tasks"][0]
    assert "readme_html" not in task
    assert "instruction" not in task


def test_runtime_observations_are_read_from_validated_batch_sidecar(preview_workspace):
    client, _, batch_dir, snapshot, calls = preview_workspace
    observation = {"summary": "Host sleep affected runs 1–3.", "difficulty_evidence": "inconclusive"}
    (batch_dir / "observations.json").write_text(json.dumps({"observations": [observation]}))
    response = client.get("/api/phase2")
    assert response.status_code == 200
    assert response.json()["batches"][0]["runtime_observations"] == [observation]
    assert "runtime_observations" not in snapshot["batches"][0]
    assert calls == []


def test_malformed_or_outside_batch_observations_are_ignored(preview_workspace):
    client, _, batch_dir, snapshot, _ = preview_workspace
    (batch_dir / "observations.json").write_text("{incomplete")
    assert "runtime_observations" not in client.get("/api/phase2").json()["batches"][0]
    snapshot["batches"][0]["id"] = "../outside"
    assert client.get("/api/phase2").status_code == 200
    assert "runtime_observations" not in client.get("/api/phase2").json()["batches"][0]
