"""Offline checks for durable, honest prompt evolution history."""
import hashlib
import json

import pytest

from phase2 import prompts


def legacy_registry(tmp_path):
    root = tmp_path / "registry"
    (root / "versions").mkdir(parents=True)
    (root / "states").mkdir()
    for index, text in enumerate(("original\n", "rejected proposal\n", "corrected proposal\n"), 1):
        record = {"version": f"v{index:04d}", "text": text,
                  "sha256": hashlib.sha256(text.encode()).hexdigest(),
                  "parent_version": "v0001" if index > 1 else None,
                  "status": "baseline" if index == 1 else "candidate",
                  "created_at": f"2026-10-06T21:0{index}:00+00:00",
                  "evidence": {"feedback": {"rationale": "Measured outcomes"}}}
        (root / "versions" / f"v{index:04d}.json").write_text(json.dumps(record))
    (root / "states" / "v0002.json").write_text(json.dumps({
        "status": "rejected", "status_updated_at": "2026-10-06T21:04:00+00:00"}))
    (root / "current.json").write_text('{"version": "v0003"}\n')
    return root


def test_backfill_preserves_rejected_version_current_pointer_and_unknown_history(tmp_path):
    root = legacy_registry(tmp_path)
    before = {str(path.relative_to(root)): path.read_bytes()
              for path in root.rglob("*.json")}
    result = prompts.history(root)
    assert result["current_version"] == "v0003"
    assert [record["status"] for record in result["versions"]] == ["baseline", "rejected", "candidate"]
    recovered = [event for event in result["events"] if event["operation"] == "recovered_snapshot"]
    assert len(recovered) == 3
    assert recovered[1]["record"]["status_updated_at"] == "2026-10-06T21:04:00+00:00"
    assert not any(event["operation"] == "activate" for event in result["events"])
    assert all("unknown" in event["note"] for event in recovered)
    assert all((root / path).read_bytes() == content for path, content in before.items())
    journal = (root / "history.jsonl").read_bytes()
    assert prompts.history(root) == result
    assert (root / "history.jsonl").read_bytes() == journal


def test_checkpoint_evidence_recovery_is_idempotent_and_preserves_rejection_reason(tmp_path):
    root = legacy_registry(tmp_path)
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"recorded_at": "2026-10-06T22:00:00+00:00",
                                 "batch_id": "old-batch", "prompt_evolution": {
                                     "source_version": "v0001", "rejected_version": "v0002",
                                     "current_candidate": "v0003", "candidate_validated": False,
                                     "rejection_reason": "Timeouts are not measured solver failures."}}))
    first = prompts.recover_history(report, root)
    second = prompts.recover_history(report, root)
    assert first == second
    evidence = [event for event in first["events"] if event["operation"] == "recovered_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["evidence"]["rejection_reason"] == "Timeouts are not measured solver failures."
    assert prompts.current_prompt(root)["version"] == "v0003"


def test_prompt_and_activation_intents_are_durable_before_pointer_replacement(tmp_path, monkeypatch):
    root = legacy_registry(tmp_path)
    prompts.history(root)
    atomic = prompts._atomic
    real_fsync = prompts.os.fsync
    flushes = []
    pointer_checks = []

    def fsync(fd):
        real_fsync(fd)
        flushes.append(fd)

    def inspect(path, value):
        if path == root / "current.json":
            events = prompts._events(root)
            assert events[-1]["operation"] == "activate"
            assert events[-1]["phase"] == "intent"
            assert events[-1]["previous_version"] == "v0003"
            assert events[-1]["version"] == "v0004"
            creation = next(event for event in events if event["operation"] == "version_create")
            assert creation["record"]["text"] == "fresh text"
            assert creation["record"]["parent_version"] == "v0003"
            assert creation["record"]["evidence"] == {"batch": "new"}
            assert len(flushes) >= 4  # Create intent, version contents, applied record, activation intent.
            pointer_checks.append(True)
        atomic(path, value)

    monkeypatch.setattr(prompts.os, "fsync", fsync)
    monkeypatch.setattr(prompts, "_atomic", inspect)
    candidate = prompts.create_version("fresh text", "v0003", {"batch": "new"}, registry_dir=root)
    assert candidate["active"] is True
    assert pointer_checks == [True]
    events = prompts.history(root)["events"]
    assert events[-1]["operation"] == "activate"
    assert events[-1]["phase"] == "applied"
    assert events[-1]["operation_id"] == events[-2]["operation_id"]


def test_journal_write_failure_prevents_prompt_publication_and_pointer_change(tmp_path, monkeypatch):
    root = legacy_registry(tmp_path)
    prompts.history(root)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(prompts, "_append", fail)
    with pytest.raises(OSError, match="disk full"):
        prompts.create_version("cannot be audited", "v0003", registry_dir=root)
    assert json.loads((root / "current.json").read_text())["version"] == "v0003"
    assert not (root / "versions" / "v0004.json").exists()
    with pytest.raises(OSError, match="disk full"):
        prompts.mark_status("v0003", "rejected", root)
    assert not (root / "states" / "v0003.json").exists()


def test_status_changes_and_rollback_are_logged_before_the_mutation(tmp_path, monkeypatch):
    root = legacy_registry(tmp_path)
    prompts.history(root)
    atomic = prompts._atomic
    checked = []

    def inspect(path, value):
        event = prompts._events(root)[-1]
        assert event["phase"] == "intent"
        if path.name == "v0003.json":
            assert event["operation"] == "status_change"
            assert event["previous_status"] == "candidate"
            assert event["status"] == "rejected"
            assert event["reason"] == "Invalid interpretation"
        else:
            assert event["operation"] == "activate"
            assert event["previous_version"] == "v0003"
            assert event["version"] == "v0001"
            assert event["reason"] == "Retain original"
        checked.append(event["operation"])
        atomic(path, value)

    monkeypatch.setattr(prompts, "_atomic", inspect)
    prompts.mark_status("v0003", "rejected", root, reason="Invalid interpretation")
    prompts.activate_version("v0001", root, reason="Retain original")
    assert checked == ["status_change", "activate"]
    assert prompts.get_prompt("v0003", root)["text"] == "corrected proposal\n"
    assert prompts.current_prompt(root)["version"] == "v0001"


def test_failed_activation_is_not_reported_as_applied(tmp_path, monkeypatch):
    root = legacy_registry(tmp_path)
    prompts.history(root)

    def fail(*args, **kwargs):
        raise OSError("pointer write interrupted")

    monkeypatch.setattr(prompts, "_atomic", fail)
    with pytest.raises(OSError):
        prompts.activate_version("v0001", root)
    result = prompts.history(root)
    assert result["current_version"] == "v0003"
    assert result["events"][-1]["phase"] == "intent"
    assert result["events"][-1]["version"] == "v0001"


def test_export_includes_all_texts_parent_diffs_and_complete_evidence(tmp_path):
    root = legacy_registry(tmp_path)
    output = prompts.export_history(tmp_path / "export", root)
    assert (output / "v0002.txt").read_text() == "rejected proposal\n"
    assert (output / "v0003.txt").read_text() == "corrected proposal\n"
    assert "-original" in (output / "v0001-to-v0003.diff").read_text()
    assert "snapshot" in (output / "README.md").read_text()
    assert "before" in (output / "README.md").read_text()
    data = json.loads((output / "history.json").read_text())
    assert data["current_version"] == "v0003"
    assert data["versions"][1]["status"] == "rejected"


def test_corrupt_journal_fails_closed_before_registry_changes(tmp_path):
    root = legacy_registry(tmp_path)
    prompts.history(root)
    with (root / "history.jsonl").open("a") as stream:
        stream.write('{"partial":')
    with pytest.raises(ValueError, match="history is incomplete or corrupt"):
        prompts.create_version("do not publish", "v0003", registry_dir=root)
    assert not (root / "versions" / "v0004.json").exists()
    assert json.loads((root / "current.json").read_text())["version"] == "v0003"


def test_cli_history_and_export_are_offline_and_do_not_activate(tmp_path, capsys):
    root = legacy_registry(tmp_path)
    prompts.main(["--registry-dir", str(root), "history"])
    assert json.loads(capsys.readouterr().out)["current_version"] == "v0003"
    output = tmp_path / "export"
    prompts.main(["--registry-dir", str(root), "export", "--output", str(output)])
    assert capsys.readouterr().out.strip() == str(output)
    assert prompts.current_prompt(root)["version"] == "v0003"
