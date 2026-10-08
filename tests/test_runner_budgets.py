"""Demo admission, cost accounting and timeout semantics without paid calls."""
import copy
import json
import threading

import httpx
import pytest

from runner import service, store
from runner.budget_proxy import BudgetProxy, BudgetStop, Ledger, reported_cost, reconcile_completed_ledger


def request():
    return {"model": "z-ai/glm-5.3-flash", "messages": [{"role": "user", "content": "Solve the task."}]}


def response(cost=0.001, upstream=None):
    return {"id": "gen-fixture", "choices": [{"message": {"role": "assistant", "content": "done"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "cost": cost,
                      "cost_details": {"upstream_inference_cost": upstream}}}


def ledger(tmp_path, **budget):
    return Ledger(tmp_path / "budget.json", {**copy.deepcopy(store.DEMO_BUDGET), **budget})


def test_preflight_reserves_before_request_and_applies_provider_caps(tmp_path):
    book = ledger(tmp_path)
    payload, index = book.prepare(request())
    assert payload["max_tokens"] == 16384
    assert payload["provider"] == {"max_price": {"prompt": 0.3, "completion": 1.0, "request": 0}, "require_parameters": True}
    persisted = store.read_json(book.path)
    assert persisted["requests"][index]["status"] == "pending"
    assert 0 < persisted["reserved_usd"] <= 0.05
    book.settle(index, response())
    assert book.data["spent_usd"] == 0.001
    assert book.data["reserved_usd"] == 0


def test_cost_is_admission_limit_not_posthoc_observation(tmp_path):
    book = ledger(tmp_path)
    book.data["spent_usd"] = 0.0499
    with pytest.raises(BudgetStop):
        book.prepare(request())
    assert not book.data["requests"]
    assert book.data["stop_reason"] == "cost"


def test_last_request_output_is_restricted_to_remaining_allowance(tmp_path):
    book = ledger(tmp_path)
    book.data["spent_usd"] = 0.04
    payload, index = book.prepare(request())
    assert 1024 <= payload["max_tokens"] < 16384
    assert book.data["spent_usd"] + book.data["reserved_usd"] <= 0.05


def test_simultaneous_requests_cannot_overreserve(tmp_path):
    book = ledger(tmp_path)
    results = []
    def reserve():
        try:
            results.append(book.prepare(request())[1])
        except BudgetStop:
            pass
    threads = [threading.Thread(target=reserve) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results
    assert book.data["reserved_usd"] <= book.data["limit_usd"]


@pytest.mark.parametrize("payload", [response(cost=0, upstream=0.006), response(cost=0.0003, upstream=0.006)])
def test_byok_provider_spend_is_not_hidden_by_zero_openrouter_bill(payload):
    assert reported_cost(payload) >= 0.006


def test_non_byok_upstream_cost_is_informational_not_an_extra_charge():
    payload = response(cost=0.006, upstream=0.006)
    payload["usage"]["is_byok"] = False
    assert reported_cost(payload) == 0.006


def test_explicit_byok_includes_upstream_invoice_and_openrouter_fee():
    payload = response(cost=0.0003, upstream=0.006)
    payload["usage"]["is_byok"] = True
    assert reported_cost(payload) == pytest.approx(0.0063)


def test_missing_byok_flag_conservatively_counts_both_known_charges():
    assert reported_cost(response(cost=0.0003, upstream=0.006)) == pytest.approx(0.0063)


@pytest.mark.parametrize("usage", [
    {"is_byok": True, "cost": 0},
    {"is_byok": False, "cost_details": {"upstream_inference_cost": 0.01}},
    {"cost": False},
])
def test_incomplete_billing_or_boolean_cost_cannot_release_reservations(usage):
    with pytest.raises(ValueError):
        reported_cost({"usage": usage})


def old_ledger(tmp_path):
    payload = response(cost=0.002, upstream=0.002)
    payload["usage"]["is_byok"] = False
    data = {"spent_usd": 0.004, "reserved_usd": 0.02, "requests": [
        {"status": "settled", "cost_usd": 0.004, "generation_id": "fixture-duplicate", "usage": payload["usage"]},
        {"status": "unknown", "reserved_usd": 0.02, "error": "ReadTimeout"}]}
    path = tmp_path / "budget.json"
    store.write_json(path, data)
    return path, data


def test_reconciliation_defaults_to_read_only_report(tmp_path):
    path, original = old_ledger(tmp_path)
    before = path.read_bytes()
    report = reconcile_completed_ledger(path)
    assert report["old_spent_usd"] == 0.004 and report["new_spent_usd"] == 0.002
    assert report["applied"] is False
    assert report["reserved_usd_unchanged"] == 0.02
    assert path.read_bytes() == before


def test_reconciliation_preserves_raw_usage_and_audits_old_and_new_amounts(tmp_path):
    path, original = old_ledger(tmp_path)
    report = reconcile_completed_ledger(path, apply=True)
    assert report["applied"] is True
    updated = store.read_json(path)
    assert updated["spent_usd"] == 0.002
    assert updated["reserved_usd"] == original["reserved_usd"]
    assert updated["requests"][0]["usage"] == original["requests"][0]["usage"]
    assert updated["requests"][1] == original["requests"][1]
    assert updated["accounting_adjustments"][0]["changes"][0]["old_cost_usd"] == 0.004
    assert reconcile_completed_ledger(path, apply=True)["applied"] is False


def test_reconciliation_refuses_pending_requests_and_inconsistent_totals(tmp_path):
    path, data = old_ledger(tmp_path)
    data["requests"][1]["status"] = "pending"
    store.write_json(path, data)
    with pytest.raises(ValueError, match="pending"):
        reconcile_completed_ledger(path, apply=True)
    data["requests"][1]["status"] = "unknown"
    data["spent_usd"] = 99
    store.write_json(path, data)
    with pytest.raises(ValueError, match="total"):
        reconcile_completed_ledger(path, apply=True)


def test_settlement_is_idempotent_and_delivery_failure_does_not_erase_billing(tmp_path):
    book = ledger(tmp_path)
    _, index = book.prepare(request())
    book.settle(index, response())
    original = copy.deepcopy(book.data)
    book.settle(index, response())
    book.settle(index, error="BrokenPipeError")
    assert book.data == original
    assert book.data["spent_usd"] == 0.001
    assert book.data["reserved_usd"] == 0
    assert book.data["accounting_error"] is None


def test_unknown_settlement_cannot_be_released_or_charged_a_second_time(tmp_path):
    book = ledger(tmp_path)
    _, index = book.prepare(request())
    book.settle(index, error="ReadTimeout")
    original = copy.deepcopy(book.data)
    book.settle(index, response())
    book.settle(index, error="BrokenPipeError")
    assert book.data == original


def test_reconcile_proven_delivery_mislabel_keeps_error_and_usage_in_audit(tmp_path):
    path, data = old_ledger(tmp_path)
    known = data["requests"][0]
    known.update(status="unknown", error="BrokenPipeError", reserved_usd=0.03)
    data["accounting_error"] = "Unresolved request; reservation retained and further requests blocked"
    store.write_json(path, data)
    original_usage = copy.deepcopy(known["usage"])
    report = reconcile_completed_ledger(path, apply=True)
    updated = store.read_json(path)
    assert updated["requests"][0]["status"] == "settled"
    assert updated["requests"][0]["delivery_error"] == "BrokenPipeError"
    assert updated["requests"][0]["usage"] == original_usage
    assert report["changes"][0]["old_status"] == "unknown"
    assert report["changes"][0]["original_error"] == "BrokenPipeError"
    assert updated["reserved_usd"] == 0.02  # Other genuinely unknown request retained.
    assert updated["accounting_error"] is not None


def test_reconciliation_requires_proof_delivery_mislabel_reservation_was_released(tmp_path):
    path, data = old_ledger(tmp_path)
    data["requests"][0].update(status="unknown", error="BrokenPipeError", reserved_usd=0.03)
    data["reserved_usd"] += 0.03
    store.write_json(path, data)
    with pytest.raises(ValueError, match="reservations"):
        reconcile_completed_ledger(path, apply=True)


def test_reconcile_only_delivery_mislabel_clears_false_accounting_error(tmp_path):
    path, data = old_ledger(tmp_path)
    data["requests"] = data["requests"][:1]
    data["requests"][0].update(status="unknown", error="BrokenPipeError", reserved_usd=0.03)
    data["reserved_usd"] = 0
    data["accounting_error"] = "Unresolved request; reservation retained and further requests blocked"
    store.write_json(path, data)
    reconcile_completed_ledger(path, apply=True)
    updated = store.read_json(path)
    assert updated["accounting_error"] is None
    assert updated["accounting_adjustments"][-1]["accounting_error_correction"]["old"] == data["accounting_error"]


def test_disconnected_client_during_response_headers_cannot_unsettle_provider_bill(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    proxy = BudgetProxy(tmp_path / "budget.json", store.DEMO_BUDGET,
                        "https://openrouter.ai/api/v1",
                        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response())))
    def broken_headers(self):
        raise BrokenPipeError("solver disconnected")
    monkeypatch.setattr(proxy.server.RequestHandlerClass, "end_headers", broken_headers)
    try:
        with pytest.raises(httpx.RemoteProtocolError):
            httpx.post(proxy.url + "/chat/completions", headers={"Authorization": "Bearer " + proxy.key}, json=request())
        data = store.read_json(tmp_path / "budget.json")
        assert data["requests"][0]["status"] == "settled"
        assert data["spent_usd"] == 0.001
        assert data["reserved_usd"] == 0
        assert data["accounting_error"] is None
    finally:
        proxy.close()


@pytest.mark.parametrize("bad", [{}, {"usage": {"cost": -1}}, {"usage": {"cost": float("nan")}}])
def test_missing_or_invalid_usage_closes_admission(tmp_path, bad):
    book = ledger(tmp_path)
    _, index = book.prepare(request())
    reserved = book.data["reserved_usd"]
    book.settle(index, bad)
    assert book.data["reserved_usd"] == reserved
    assert book.data["accounting_error"]
    with pytest.raises(ValueError):
        book.prepare(request())


def test_ambiguous_transport_failure_retains_reservation_across_restart(tmp_path):
    book = ledger(tmp_path)
    _, index = book.prepare(request())
    book.settle(index, error="read timeout")
    recovered = ledger(tmp_path)
    assert recovered.data["reserved_usd"] > 0
    with pytest.raises(ValueError):
        recovered.prepare(request())


@pytest.mark.parametrize("change", [
    {"model": "some-other-model"}, {"stream": True}, {"n": 2},
    {"plugins": [{"id": "web"}]},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "https://example.com/a.png"}]}]},
])
def test_unsupported_or_unbounded_requests_never_get_admitted(tmp_path, change):
    book = ledger(tmp_path)
    with pytest.raises(ValueError):
        book.prepare({**request(), **change})
    assert book.data["reserved_usd"] == 0


def test_proxy_only_forwards_after_reservation_and_does_not_leak_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-upstream-secret")
    seen = []
    def upstream(req):
        seen.append(req)
        assert store.read_json(tmp_path / "budget.json")["reserved_usd"] > 0
        assert req.headers["Authorization"] == "Bearer fixture-upstream-secret"
        return httpx.Response(200, json=response(cost=0, upstream=0.002))
    proxy = BudgetProxy(tmp_path / "budget.json", store.DEMO_BUDGET,
                        "https://openrouter.ai/api/v1", transport=httpx.MockTransport(upstream))
    try:
        assert httpx.post(proxy.url + "/chat/completions", json=request()).status_code == 401
        headers = {"Authorization": "Bearer " + proxy.key}
        reply = httpx.post(proxy.url + "/chat/completions", headers=headers, json=request())
        assert reply.status_code == 200
        assert len(seen) == 1
        proxy.ledger.data["spent_usd"] = 0.0499
        assert httpx.post(proxy.url + "/chat/completions", headers=headers, json=request()).status_code == 402
        assert len(seen) == 1
        assert "fixture-upstream-secret" not in (tmp_path / "budget.json").read_text()
    finally:
        proxy.close()


def test_provider_failure_is_not_retried_with_unknown_spend(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    seen = []
    def upstream(req):
        seen.append(req)
        return httpx.Response(503, json={"error": "unavailable"})
    proxy = BudgetProxy(tmp_path / "budget.json", store.DEMO_BUDGET,
                        "https://openrouter.ai/api/v1", transport=httpx.MockTransport(upstream))
    try:
        headers = {"Authorization": "Bearer " + proxy.key}
        for _ in range(2):
            assert httpx.post(proxy.url + "/chat/completions", headers=headers, json=request()).status_code >= 400
        assert len(seen) == 1
        assert proxy.ledger.data["accounting_error"]
    finally:
        proxy.close()


def test_demo_command_uses_agent_execution_override_not_task_or_global_timeout(tmp_path):
    store.write_json(tmp_path / "job.json", {"profile": "demo", "budget": store.DEMO_BUDGET})
    args = service.command(tmp_path, "eval-1", "evaluation", "http://127.0.0.1:123/v1")
    config = store.read_json(tmp_path / "runs/eval-1/demo-config.json")
    assert config["agents"][0] == {"name": "terminus-2", "model_name": store.MODEL,
        "override_timeout_sec": 300, "kwargs": {"reasoning_effort": "high", "api_base": "http://127.0.0.1:123/v1"}}
    assert "-a" not in args and "-m" not in args
    assert "--timeout-multiplier" not in args
    assert "--config" not in service.command(tmp_path, "oracle", "oracle")
    with pytest.raises(ValueError, match="metered"):
        service.command(tmp_path, "eval-1", "evaluation")


@pytest.mark.parametrize("reason", ["cost", "time"])
def test_budget_stop_overrides_missing_tests_but_not_as_model_failure(reason):
    state = {"kind": "evaluation", "status": "error", "passed": None}
    result = {"exception_info": {"exception_type": "AgentTimeoutError"}} if reason == "time" else {}
    service.apply_demo_outcome(state, result, {"profile": "demo"}, {"stop_reason": reason if reason == "cost" else None})
    assert state["status"] == "budget_exhausted"
    assert state["budget_reason"] == reason
    assert state["passed"] is None and state["reward"] is None


def test_verifier_timeout_is_infrastructure_error_not_demo_time_limit():
    state = {"kind": "evaluation", "status": "timeout", "passed": None}
    service.apply_demo_outcome(state, {"exception_info": {"exception_type": "VerifierTimeoutError"}}, {"profile": "demo"}, {})
    assert state["status"] == "timeout"


def test_demo_five_complete_runs_never_claim_canonical_learnability():
    runs = [{"kind": "evaluation", "status": status} for status in ["passed", "failed", "failed", "failed", "failed"]]
    assert store.summarize(runs)["learnable"] is True
    demo = store.summarize(runs, "demo")
    assert demo["learnable"] is None and demo["observed_demo_band"] is True
    runs[-1]["status"] = "budget_exhausted"
    demo = store.summarize(runs, "demo")
    assert demo["learnable"] is None and demo["observed_demo_band"] is None
    assert demo["budget_exhausted"] == 1 and demo["failures"] == 3


def test_demo_job_snapshots_profile_and_prompt_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("Fixture")
    job = store.create_job("private-fixture", task_path=task, profile="demo",
                           metadata={"batch_id": "batch-1", "prompt_version": "v0002"})
    assert job["profile"] == "demo"
    assert job["budget"]["agent_timeout_sec"] == 300
    assert job["metadata"]["prompt_version"] == "v0002"
    assert job["max_concurrency"] == 5
