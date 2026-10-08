"""Search HTTP boundaries without provider calls or worker execution."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import main


@pytest.fixture
def api(monkeypatch):
    calls = []
    record = {"id": "abcdef0123456789", "status": "queued", "target": 10,
              "accepted_count": 0, "attempted_count": 0, "rounds": []}

    def start():
        calls.append("start")
        return record

    def stop():
        calls.append("stop")
        return {**record, "status": "cancelled"}

    controller = SimpleNamespace(
        status=lambda: {"worker_alive": True, "search": record, "searches": [record]},
        start_search=start, stop_search=stop,
    )
    monkeypatch.setattr(main, "search_controller", lambda: controller)
    monkeypatch.setenv("OPENROUTER_API_KEY", "search-fixture-key")
    with TestClient(main.app) as client:
        yield client, controller, calls


def test_search_status_is_read_only(api):
    client, _, calls = api
    response = client.get("/api/search")
    assert response.status_code == 200
    assert response.json()["search"]["target"] == 10
    assert response.headers["cache-control"] == "no-store"
    assert calls == []


def test_start_only_submits_durable_work_and_does_not_accept_budget_overrides(api):
    client, _, calls = api
    response = client.post("/api/search/start", json={"target": 100, "budget": 999})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["target"] == 10
    assert calls == ["start"]


def test_missing_key_does_not_submit(api, monkeypatch):
    client, _, calls = api
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert client.post("/api/search/start").status_code == 503
    assert calls == []


def test_search_conflict_redacts_secret(api):
    client, controller, _ = api

    def fail():
        raise ValueError("Active search: search-fixture-key")

    controller.start_search = fail
    response = client.post("/api/search/start")
    assert response.status_code == 409
    assert response.json()["detail"] == "Active search: [REDACTED]"


def test_stop_routes_to_latest_search_without_requiring_key(api, monkeypatch):
    client, _, calls = api
    monkeypatch.delenv("OPENROUTER_API_KEY")
    response = client.post("/api/search/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert calls == ["stop"]


def test_missing_search_stop_is_not_found(api):
    client, controller, _ = api

    def missing():
        raise KeyError("none")

    controller.stop_search = missing
    assert client.post("/api/search/stop").status_code == 404


@pytest.mark.parametrize("endpoint", ["/api/search/start", "/api/search/stop"])
def test_cross_origin_requests_cannot_start_or_stop_work(api, endpoint):
    client, _, calls = api
    assert client.post(endpoint, headers={"Origin": "https://outside.example"}).status_code == 403
    assert calls == []


def test_foreign_host_cannot_read_search_status(api):
    client, _, calls = api
    assert client.get("/api/search", headers={"Host": "outside.example"}).status_code == 400
    assert calls == []


def test_poll_omits_large_diversity_fingerprints_without_changing_evidence(api, monkeypatch):
    client, _, calls = api
    fingerprint = {"instruction": {"shingles": ["a" * 64] * 100}}
    snapshot = {"batches": [{"id": "abcdef0123456789", "archive": [{"fingerprint": fingerprint}],
                              "tasks": [{"id": "fixture", "fingerprint": fingerprint}]}]}

    def unavailable(_):
        raise KeyError("No sidecar in fixture")

    monkeypatch.setattr(main, "phase2_controller", lambda: SimpleNamespace(status=lambda: snapshot, _path=unavailable))
    response = client.get("/api/phase2")
    assert response.status_code == 200
    batch = response.json()["batches"][0]
    assert "archive" not in batch
    assert "fingerprint" not in batch["tasks"][0]
    assert snapshot["batches"][0]["archive"][0]["fingerprint"] == fingerprint
    assert snapshot["batches"][0]["tasks"][0]["fingerprint"] == fingerprint
    assert calls == []
