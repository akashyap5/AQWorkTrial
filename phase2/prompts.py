"""Immutable prompts, an atomic active pointer, and a durable evolution journal.

Every mutation is journaled and fsynced before changing the registry, then receives
an applied record. An intent without an applied record is not proof of activation.
Older registries are recovered as snapshots; missing activation history is never
invented. ``python -m phase2.prompts --help`` exposes history and readable exports.
"""
from __future__ import annotations

import fcntl
import argparse
import difflib
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"baseline", "candidate", "validated", "rejected", "superseded"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _events(root: Path) -> list[dict]:
    path = root / "history.jsonl"
    if not path.exists():
        return []
    try:
        # Fail closed on a partial/corrupt journal rather than hide lost evidence.
        return [json.loads(line) for line in path.read_text().splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Prompt history is incomplete or corrupt; preserve and repair it before updating prompts.") from exc


def _append(root: Path, event: dict) -> dict:
    event = {"schema_version": 1, "event_id": uuid.uuid4().hex,
             "recorded_at": _now(), **event}
    with (root / "history.jsonl").open("a") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(root)
    return event


def _mutation(root: Path, operation: str, details: dict, apply) -> None:
    operation_id = uuid.uuid4().hex
    _append(root, {"operation": operation, "phase": "intent",
                   "operation_id": operation_id, **details})
    apply()
    _append(root, {"operation": operation, "phase": "applied",
                   "operation_id": operation_id})


def _backfill(root: Path) -> None:
    """Recover available facts once, without guessing historical activations."""
    events = _events(root)
    if any(event.get("operation") == "recovery_complete" for event in events):
        return
    keys = {event.get("recovery_key") for event in events}
    for path in sorted((root / "versions").glob("v*.json")):
        record = _read(root, path.stem)
        key = f"snapshot:{record['version']}"
        if key not in keys:
            _append(root, {"operation": "recovered_snapshot", "recovery_key": key,
                           "record": record,
                           "source": f"versions/{path.name} + states/{path.name}",
                           "note": "Snapshot at recovery; created_at/status_updated_at are source timestamps. Past activation times and order are unknown."})
    pointer = root / "current.json"
    if pointer.exists() and "current_snapshot" not in keys:
        _append(root, {"operation": "recovered_current", "recovery_key": "current_snapshot",
                       "version": json.loads(pointer.read_text())["version"],
                       "note": "Current pointer observed at recovery; this is not an activation event."})
    _append(root, {"operation": "recovery_complete"})


def registry_path(registry_dir=None) -> Path:
    if registry_dir is not None:
        return Path(registry_dir)
    return Path(os.environ.get("TASKLAB_PROMPTS_DIR", str(
        Path(os.environ.get("TASKLAB_DATA", str(ROOT / ".tasklab"))) / "prompts"
    )))


def _atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _locked(registry_dir=None):
    root = registry_path(registry_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        _backfill(root)
        yield root


def _read(root: Path, version: str) -> dict:
    if not re.fullmatch(r"v\d{4,}", version):
        raise ValueError("Invalid prompt version.")
    record = json.loads((root / "versions" / f"{version}.json").read_text())
    if hashlib.sha256(record["text"].encode()).hexdigest() != record["sha256"]:
        raise ValueError("Prompt contents do not match their immutable hash.")
    state = root / "states" / f"{version}.json"
    if state.exists():
        record.update(json.loads(state.read_text()))
    pointer = root / "current.json"
    record["active"] = pointer.exists() and json.loads(pointer.read_text())["version"] == version
    return record


def _create(root: Path, text: str, parent_version: str | None, evidence,
            activate: bool, status: str) -> dict:
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 65_536:
        raise ValueError("Prompt text must be nonempty and at most 64 KiB.")
    if status not in STATUSES:
        raise ValueError("Unsupported prompt status.")
    if parent_version is not None:
        _read(root, parent_version)
    directory = root / "versions"
    directory.mkdir(exist_ok=True)
    numbers = [int(p.stem[1:]) for p in directory.glob("v*.json")
               if re.fullmatch(r"v\d{4,}", p.stem)]
    # Never reuse a version reserved by an interrupted write-ahead operation.
    numbers += [int(event["record"]["version"][1:]) for event in _events(root)
                if event.get("operation") == "version_create" and event.get("phase") == "intent"]
    version = f"v{max(numbers, default=0) + 1:04d}"
    record = {
        "version": version, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "parent_version": parent_version, "evidence": evidence or {}, "status": status,
        "created_at": _now(),
    }
    # Publish a fully written inode without replacing any existing version.
    def publish():
        fd, temporary = tempfile.mkstemp(prefix=".version-", dir=directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(record, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, directory / f"{version}.json")
            _sync_directory(directory)
        finally:
            os.unlink(temporary)
    _mutation(root, "version_create", {"record": record}, publish)
    if activate:
        _activate(root, version, reason="Activate newly created prompt.")
    return _read(root, version)


def _initialize(root: Path) -> None:
    if (root / "current.json").exists():
        return
    existing = sorted((root / "versions").glob("v*.json"))
    if existing:
        # Recover only the baseline after a crash before its initial pointer write.
        _activate(root, existing[0].stem, reason="Recover baseline after missing initial pointer.")
    else:
        _create(root, (ROOT / "prompts" / "starter_prompt.txt").read_text(), None,
                {"source": "prompts/starter_prompt.txt"}, True, "baseline")


def current_prompt(registry_dir=None) -> dict:
    """Read the pointer afresh for every generation; active candidates are explicit."""
    with _locked(registry_dir) as root:
        _initialize(root)
        record = _read(root, json.loads((root / "current.json").read_text())["version"])
        if record["status"] == "rejected":
            raise ValueError("Current prompt is rejected; activate a retained version first.")
        return record


def get_prompt(version: str, registry_dir=None) -> dict:
    with _locked(registry_dir) as root:
        _initialize(root)
        return _read(root, version)


def list_prompts(registry_dir=None) -> list[dict]:
    with _locked(registry_dir) as root:
        _initialize(root)
        return [_read(root, p.stem) for p in sorted((root / "versions").glob("v*.json"))]


def create_version(text: str, parent_version: str | None, evidence=None, *,
                   activate: bool = True, status: str = "candidate", registry_dir=None,
                   require_current_parent: bool = False) -> dict:
    with _locked(registry_dir) as root:
        _initialize(root)
        if require_current_parent and json.loads((root / "current.json").read_text())["version"] != parent_version:
            raise ValueError("Current prompt changed since this batch started; candidate was not activated.")
        return _create(root, text, parent_version, evidence, activate, status)


def _activate(root: Path, version: str, reason: str | None = None) -> None:
    pointer = root / "current.json"
    previous = json.loads(pointer.read_text())["version"] if pointer.exists() else None
    _mutation(root, "activate", {"version": version, "previous_version": previous,
                                "reason": reason},
              lambda: _atomic(pointer, {"version": version}))


def activate_version(version: str, registry_dir=None, *, reason: str | None = None) -> dict:
    with _locked(registry_dir) as root:
        record = _read(root, version)
        if record["status"] == "rejected":
            raise ValueError("A rejected prompt cannot be activated.")
        _activate(root, version, reason)
        return _read(root, version)


def mark_status(version: str, status: str, registry_dir=None, *,
                reason: str | None = None, evidence=None) -> dict:
    if status not in STATUSES:
        raise ValueError("Unsupported prompt status.")
    with _locked(registry_dir) as root:
        previous = _read(root, version)
        state = {"status": status, "status_updated_at": _now()}
        _mutation(root, "status_change", {
            "version": version, "previous_status": previous["status"],
            "status": status, "reason": reason, "evidence": evidence or {},
        }, lambda: _atomic(root / "states" / f"{version}.json", state))
        return _read(root, version)


def recover_history(report_path=None, registry_dir=None) -> dict:
    """Idempotently attach checkpoint evidence to recovered prompt history."""
    with _locked(registry_dir) as root:
        _initialize(root)
        if report_path is not None:
            path = Path(report_path)
            raw = path.read_bytes()
            report = json.loads(raw)
            evolution = report["prompt_evolution"]
            key = "report:" + hashlib.sha256(raw).hexdigest()
            if not any(event.get("recovery_key") == key for event in _events(root)):
                mentioned = [evolution.get(name) for name in
                             ("source_version", "rejected_version", "current_candidate")]
                for version in filter(None, mentioned):
                    _read(root, version)
                _append(root, {"operation": "recovered_evidence", "recovery_key": key,
                               "source": str(path), "source_recorded_at": report.get("recorded_at"),
                               "batch_id": report.get("batch_id"),
                               "evidence": {name: evolution[name] for name in (
                                   "source_version", "rejected_version", "current_candidate",
                                   "candidate_validated", "rejection_reason", "proposal_attempts",
                                   "authored_by_model_verified", "model_response_id") if name in evolution},
                               "note": "Recovered report evidence, not a reconstruction of missing activation events."})
        return _history(root)


def _history(root: Path) -> dict:
    return {"schema_version": 1,
            "current_version": json.loads((root / "current.json").read_text())["version"],
            "versions": [_read(root, path.stem) for path in sorted((root / "versions").glob("v*.json"))],
            "events": _events(root)}


def history(registry_dir=None) -> dict:
    """Return immutable texts, current states, and chronological journal records."""
    with _locked(registry_dir) as root:
        _initialize(root)
        return _history(root)


def export_history(output_dir, registry_dir=None) -> Path:
    """Write reviewable text/diffs/evidence without changing the active prompt."""
    data = history(registry_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _atomic(destination / "history.json", data)
    records = {record["version"]: record for record in data["versions"]}
    lines = ["# Prompt evolution history", "",
             f"Current prompt at export: **{data['current_version']}**. Candidate does not mean validated.", "",
             "This is a portable snapshot of the runtime registry. Existing versions were recovered from immutable files and state records. Creation/status timestamps are preserved; missing historical activation times and ordering are unknown. Journal `recorded_at` is the time of recovery or logging, not a guessed historical event time.", "",
             "Runtime updates append and fsync `.tasklab/prompts/history.jsonl` under the registry lock **before** publishing a prompt, changing its status, or replacing the active pointer. A matching `applied` record follows a successful mutation. An intent without an applied record may be incomplete; the current registry files remain authoritative. Rejected versions are retained. The journal includes full text, hashes, parents, model feedback, evidence, activations, and rollbacks.", "",
             "Read or refresh from the repository root (neither command calls a model or starts a batch):", "",
             "```sh", ".venv/bin/python -m phase2.prompts history",
             ".venv/bin/python -m phase2.prompts export --output prompts/history", "```", "",
             "Python: `from phase2.prompts import history; history()`. Use `--registry-dir PATH` (before the subcommand), `TASKLAB_PROMPTS_DIR`, or `TASKLAB_DATA` for a different registry. A fresh checkout seeds from `prompts/starter_prompt.txt`; these exports do not silently change the active prompt.", "",
             "The original checkpoint evidence was attached idempotently with:", "", "```sh",
             ".venv/bin/python -m phase2.prompts history --recover-report reports/phase-2-checkpoint.json", "```", "",
             "## Versions", "", "| Version | Parent | Status | Created (UTC) | Changes |",
             "| --- | --- | --- | --- | --- |"]
    for version, record in records.items():
        (destination / f"{version}.txt").write_text(record["text"])
        parent = record["parent_version"]
        link = "Seed"
        if parent in records:
            name = f"{parent}-to-{version}.diff"
            diff = "".join(difflib.unified_diff(records[parent]["text"].splitlines(keepends=True),
                                               record["text"].splitlines(keepends=True),
                                               fromfile=parent, tofile=version))
            (destination / name).write_text(diff)
            link = f"[Diff]({name})"
        lines.append(f"| [{version}]({version}.txt) | {parent or '—'} | {record['status']} | {record['created_at']} | {link} |")
    lines += ["", "## Decisions and feedback", ""]
    for version, record in records.items():
        feedback = record.get("evidence", {}).get("feedback", {})
        lines += [f"### {version}", "", f"SHA-256: `{record['sha256']}`.", ""]
        matching = [other for other, previous in records.items()
                    if other < version and previous["text"].strip() == record["text"].strip()]
        if matching:
            lines += [f"The prompt wording is unchanged from {matching[-1]} apart from surrounding whitespace. This version preserves its own feedback, provenance, and status; it does not demonstrate an additional content improvement.", ""]
        for field in ("rationale", "expected_effect", "uncertainty"):
            if feedback.get(field):
                lines += [f"**{field.replace('_', ' ').capitalize()}:** {feedback[field]}", ""]
        for event in data["events"]:
            evidence = event.get("evidence", {})
            if event.get("operation") == "recovered_evidence" and evidence.get("rejected_version") == version:
                lines += [f"**Rejected:** {evidence.get('rejection_reason', 'See recovered evidence.')}", "",
                          f"Source: `{event['source']}` (recorded {event.get('source_recorded_at')}).", ""]
    lines += ["Full registry records and the write-ahead journal are in [history.json](history.json). Refresh this export after later batches to include subsequent changes.", ""]
    (destination / "README.md").write_text("\n".join(lines))
    return destination


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-dir")
    subparsers = parser.add_subparsers(dest="command", required=True)
    show = subparsers.add_parser("history", help="Print prompt history as JSON.")
    show.add_argument("--recover-report", type=Path)
    export = subparsers.add_parser("export", help="Export texts, diffs, decisions, and JSON.")
    export.add_argument("--output", type=Path, default=ROOT / "prompts" / "history")
    args = parser.parse_args(argv)
    if args.command == "history":
        print(json.dumps(recover_history(args.recover_report, args.registry_dir), indent=2, ensure_ascii=False))
    else:
        print(export_history(args.output, args.registry_dir))


if __name__ == "__main__":
    main()
