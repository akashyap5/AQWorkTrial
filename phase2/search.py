"""Durable search for ten distinct, audited learnable tasks.

Every candidate stays in the yield denominator. Budget stops are inconclusive.
Progress is assessed every three rounds. A sustained, evidenced difficulty stall
can stop for brainstorming; inconclusive runs never establish such a stall.
The worker never retries an ambiguous paid authoring or prompt-update request.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from phase2 import controller, diversity, prompts
from runner import store
from runner.service import process_alive

ROOT = Path(__file__).resolve().parents[1]
DATA = store.DATA / "search"
TERMINAL = {"completed", "checkpoint", "error", "cancelled"}


def settings():
    cfg = dict(controller.settings()["search"])
    if (cfg["target"] != 10 or cfg["tasks_per_round"] != 3
            or cfg["agent_timeout_sec"] != store.SEARCH_BUDGET["agent_timeout_sec"]
            or cfg["attempt_cost_usd"] != store.SEARCH_BUDGET["cost_usd"]):
        raise ValueError("Search configuration must match the fixed five-attempt runner profile.")
    cfg["round_allowance_usd"] = cfg["tasks_per_round"] * (
        cfg["generation_cost_usd"] + cfg["semantic_review_cost_usd"]
        + cfg["failure_audit_cost_usd"] + 5 * cfg["attempt_cost_usd"]
    ) + cfg["prompt_update_cost_usd"]
    return cfg


def _records():
    return sorted((store.read_json(p) for p in DATA.glob("*/search.json")),
                  key=lambda r: r["created_at"], reverse=True)


def _path(search_id):
    if not re.fullmatch(r"[a-f0-9]{16}", search_id):
        raise KeyError(search_id)
    path = DATA / search_id
    if not (path / "search.json").is_file():
        raise KeyError(search_id)
    return path


def _batches(record):
    # start_batch publishes before the search record can append its ID. Recover
    # that narrow crash window from ownership metadata, never author a replacement.
    owned = [b for b in reversed(controller._records())
             if b.get("metadata", {}).get("search_id") == record["id"]]
    for batch in owned:
        if batch["id"] not in record["rounds"]:
            record["rounds"].append(batch["id"])
    return [store.read_json(controller.DATA / "batches" / batch_id / "batch.json")
            for batch_id in record["rounds"]]


def _refresh(record):
    batches = _batches(record)
    bootstrap_dir = DATA / record["id"] / "bootstrap"
    preserved = (store.read_json(bootstrap_dir / "search-bootstrap-result.json")
                 or store.read_json(bootstrap_dir / "search-bootstrap-reservation.json"))
    if preserved is not None:
        record["bootstrap_cost_usd"] = float(preserved.get("cost_usd", 0))
    for batch in batches:
        controller._costs(batch)
    record["accounted_cost_usd"] = record.get("bootstrap_cost_usd", 0) + sum(
        b.get("accounted_cost_usd", 0) for b in batches)
    record["attempted_count"] = sum(bool(t.get("attempts")) for b in batches for t in b["tasks"])
    record["generation_attempts"] = sum(len(t.get("attempts", [])) for b in batches for t in b["tasks"])
    record["accepted_count"] = len(record.get("accepted", []))
    record["families"] = dict(Counter(row["family"] for row in record.get("accepted", [])))
    # Derive this from immutable completed-round outcomes. Incrementing a counter
    # in one file and flagging a second file could double count after a crash.
    record["stagnant_rounds"] = 0
    for batch in batches:
        if batch["status"] == "checkpoint":
            record["stagnant_rounds"] = (0 if any(t.get("accepted") for t in batch["tasks"])
                                         else record["stagnant_rounds"] + 1)
    _assess_progress(record, batches)


def _assess_progress(record, batches):
    """Derive auditable assessments without treating time/cost stops as failures."""
    completed = [batch for batch in batches if batch["status"] == "checkpoint"]

    def assess(window):
        tasks = [task for batch in window for task in batch["tasks"]]
        measured = [(task.get("summary") or {}) for task in tasks
                    if (task.get("summary") or {}).get("valid_runs") == 5
                    and (task.get("summary") or {}).get("outcome") in {"too_easy", "too_hard", "learnable_band"}]
        distances = [min(abs(summary["passes"] - target) for target in (1, 2, 3)) for summary in measured]
        return {"round_ids": [batch["id"] for batch in window],
                "prompt_versions": sorted({batch["prompt_version"] for batch in window}),
                "task_slots": len(tasks), "five_valid_outcome_slots": len(measured),
                "audited_learnable_slots": sum(task.get("accepted") is True for task in tasks),
                "mean_distance_to_band": sum(distances) / len(distances) if distances else None}

    record["progress_assessments"] = [assess(completed[end - 3:end])
                                      for end in range(3, len(completed) + 1, 3)]
    record["severe_bottleneck"] = None
    if len(completed) < 6:
        return
    recent = completed[-6:]
    tasks = [task for batch in recent for task in batch["tasks"]]
    early, late = assess(recent[:3]), assess(recent[3:])
    fully_measured_stall = bool(tasks) and all(
        not task.get("accepted") and (task.get("summary") or {}).get("valid_runs") == 5
        and (task.get("summary") or {}).get("outcome") in {"too_easy", "too_hard"}
        for task in tasks)
    if (fully_measured_stall and len({batch["prompt_version"] for batch in recent}) >= 3
            and late["mean_distance_to_band"] >= early["mean_distance_to_band"]):
        record["severe_bottleneck"] = {
            "reason": "Six completed rounds across at least two prompt revisions produced no audited learnable task and no improvement toward the target pass band; stop and brainstorm.",
            "earlier_three_rounds": early, "latest_three_rounds": late}


def _save(record):
    _refresh(record)
    record["updated_at"] = store.now()
    store.write_json(DATA / record["id"] / "search.json", record)


def status():
    records = _records()
    for record in records:
        _refresh(record)
    try:
        pid = int((DATA / "worker.lock").read_text().strip())
        alive = process_alive(pid, "phase2.search")
    except (OSError, ValueError):
        alive = False
    return {"worker_alive": alive, "search": records[0] if records else None, "searches": records}


def start_search():
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("Set OPENROUTER_API_KEY before starting the search.")
    if os.environ.get("OPENROUTER_API_BASE", "").rstrip("/") != "https://openrouter.ai/api/v1":
        raise ValueError("Metered search requires OPENROUTER_API_BASE=https://openrouter.ai/api/v1.")
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "queue.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if any(r["status"] not in TERMINAL for r in _records()):
            raise ValueError("A search is already queued or running.")
        if any(r["status"] not in controller.TERMINAL for r in controller._records()):
            raise ValueError("Finish or stop the active phase-two batch before starting a search.")
        cfg = settings()
        record = {"id": uuid.uuid4().hex[:16], "status": "queued", "phase": "Waiting for search worker",
                  "created_at": store.now(), "target": cfg["target"], "config": cfg,
                  "api_base": "https://openrouter.ai/api/v1", "rounds": [], "accepted": [],
                  "processed_rounds": [], "prompt_scores": {}, "stagnant_rounds": 0,
                  "stop_reason": None, "report_path": None, "bootstrap_cost_usd": 0,
                  "starting_prompt": prompts.current_prompt()["version"]}
        _save(record)
        return record


def stop_search(search_id=None):
    record = store.read_json(_path(search_id) / "search.json") if search_id else next(iter(_records()), None)
    if record is None:
        raise KeyError("No search exists.")
    if record["status"] not in TERMINAL:
        (_path(record["id"]) / "stop").touch()
        for batch in _batches(record):
            if batch["status"] not in controller.TERMINAL:
                controller.stop_batch(batch["id"])
    return record


def resume_search(search_id):
    """Resume an explicitly reviewed checkpoint, preserving all request evidence.

    This does not repair or reset task attempts. Any failed task that should be
    retried must first be explicitly prepared by the caller using its known,
    completed request evidence. Ambiguous authoring/update calls remain blocked.
    """
    path = _path(search_id)
    with (DATA / "queue.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = store.read_json(path / "search.json")
        if record["status"] not in {"checkpoint", "error", "cancelled"}:
            raise ValueError("Only a stopped search checkpoint can be resumed.")
        if any(other["id"] != search_id and other["status"] not in TERMINAL for other in _records()):
            raise ValueError("Another search is already queued or running.")
        if any(batch["status"] not in controller.TERMINAL
               and batch.get("metadata", {}).get("search_id") != search_id for batch in controller._records()):
            raise ValueError("An unrelated phase-two batch is active.")
        batches = _batches(record)
        pending = [batch for batch in batches if batch["status"] not in controller.TERMINAL
                   or any(task["status"] not in controller.TASK_TERMINAL for task in batch["tasks"])]
        for batch in pending:
            if batch.get("update_started_at") and batch.get("decision") in {None, "update_deferred", "deferred_to_search"}:
                raise ValueError("Interrupted prompt-update evidence must be resolved before resuming.")
            for task in batch["tasks"]:
                for attempt in task.get("attempts", []):
                    if (not attempt.get("finished") and not attempt.get("job_id")
                            and attempt.get("generation", {}).get("status") != "structurally_validated"):
                        raise ValueError("Ambiguous authoring evidence must be resolved before resuming.")
        record.setdefault("resume_events", []).append({
            "resumed_at": store.now(), "previous_status": record["status"],
            "previous_stop_reason": record.get("stop_reason"), "previous_error": record.get("error"),
            "previous_completed_at": record.get("completed_at"), "previous_config": record["config"],
            "reopened_rounds": [batch["id"] for batch in pending]})
        record["config"] = settings()
        reopened = {batch["id"] for batch in pending}
        record["processed_rounds"] = [batch_id for batch_id in record["processed_rounds"] if batch_id not in reopened]
        record["prompt_scores"] = {}
        record.pop("best_observed_prompt", None)
        for batch in batches:
            if batch["id"] in record["processed_rounds"] and batch["status"] == "checkpoint":
                update_scores(record, batch)
        record.update(status="queued", phase="Resumed: waiting for search worker",
                      error=None, stop_reason=None)
        record.pop("completed_at", None)
        (path / "stop").unlink(missing_ok=True)
        _save(record)
        return record


def _stopped(record):
    return (_path(record["id"]) / "stop").exists()


def generated_archive():
    """Only our own generated tasks; supplied public examples are never seeds."""
    rows = []
    for batch in reversed(controller._records()):
        for task in batch["tasks"]:
            if not task.get("attempts"):
                continue
            generation = next((a["generation"] for a in reversed(task["attempts"])
                               if a.get("generation", {}).get("status") == "structurally_validated"), {})
            fingerprint = task.get("fingerprint")
            path = Path(generation.get("task_dir", "/nonexistent"))
            if not fingerprint and (path / "instruction.md").is_file():
                fingerprint = diversity.fingerprint_task(path)
            rows.append({"id": f"{batch['id']}/{task['id']}", "family": task.get("family", task["id"]),
                         "name": task.get("display_name") or generation.get("display_name") or task["id"],
                         "description": task.get("description") or generation.get("description") or task["topic"],
                         "solution_pattern": task.get("solution_pattern") or (task.get("semantic_review") or {}).get("solution_pattern"),
                         "fingerprint": fingerprint, "status": task["status"],
                         "outcome": (task.get("summary") or {}).get("outcome")})
    return rows


def bootstrap(record):
    """Have GLM author the expanded-scope prompt; preserve the prior prompt."""
    if record.get("bootstrap_prompt"):
        return
    # Recover a completed atomic version publish, never replay the model request.
    existing = [p for p in prompts.list_prompts()
                if p.get("evidence", {}).get("search_bootstrap") == record["id"]]
    if existing:
        candidate = existing[-1]
        current = prompts.current_prompt()
        if current["version"] != candidate["version"]:
            if current["version"] != candidate["parent_version"]:
                raise RuntimeError("Current prompt changed during search bootstrap; inspect the recovered candidate.")
            prompts.activate_version(candidate["version"], reason="Recover completed search bootstrap after interruption.")
        record["bootstrap_prompt"] = candidate["version"]
        _save(record)
        return
    if record.get("bootstrap_started_at"):
        raise RuntimeError("Interrupted search bootstrap; inspect preserved provider evidence before retrying.")
    from phase2.llm import budgeted_json_call
    source = prompts.current_prompt()
    record.update(phase="GLM is adapting the prompt to the broader task search", bootstrap_started_at=store.now())
    _save(record)
    history = [{"prompt_version": b["prompt_version"], "metrics": b.get("metrics"),
                "tasks": [controller.prompt_task_feedback(t) for t in b["tasks"]]}
               for b in controller._records()[:2]]
    response = budgeted_json_call(
        [{"role": "system", "content": (ROOT / "prompts/update_prompt.txt").read_text()},
         {"role": "user", "content": json.dumps({"current_prompt": source,
          "scope_change": (ROOT / "prompts/search_seed_prompt.txt").read_text(),
          "history": history, "families": [f["title"] for f in diversity.FAMILIES],
          "evaluation_profile": "Five independent GLM-5.3-Flash/high attempts; 600s agent execution and $0.20 per attempt. Limits are inconclusive, never solver failures.",
          "instruction": "Revise for this explicitly expanded scope. Remove the compact CLI/two-or-three-rules ceiling. This is a scope-driven candidate, not proof of improvement."})}],
        evidence_dir=_path(record["id"]) / "bootstrap", budget_usd=record["config"]["prompt_update_cost_usd"],
        max_tokens=16384, request_name="search-bootstrap", reasoning_enabled=False)
    record["bootstrap_cost_usd"] = response.get("cost_usd", 0)
    record["bootstrap_response_status"] = response["status"]
    _save(record)
    data = response.get("data") or {}
    text = data.get("prompt")
    if response["status"] != "completed" or data.get("action") != "revise" or not isinstance(text, str) or not 100 <= len(text) <= 16000:
        raise RuntimeError("GLM did not return a usable expanded-scope prompt; stopped before task generation.")
    candidate = prompts.create_version(text, parent_version=source["version"], activate=True,
                                      require_current_parent=True, evidence={"search_bootstrap": record["id"],
                                      "rationale": data.get("rationale"), "evidence": data.get("evidence"),
                                      "uncertainty": data.get("uncertainty"), "expected_effect": data.get("expected_effect")})
    record["bootstrap_prompt"] = candidate["version"]
    _save(record)


def _digest(path):
    digest = hashlib.sha256()
    for file in sorted(path.rglob("*")):
        if file.is_file():
            digest.update(str(file.relative_to(path)).encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()


def collect(record, batch):
    """Export only fully audited results; collection diversity is separately capped."""
    known = {row["job_id"] for row in record["accepted"]}
    counts = Counter(row["family"] for row in record["accepted"])
    for task in batch["tasks"]:
        if not task.get("accepted") or task.get("job_id") in known or len(record["accepted"]) >= record["target"]:
            continue
        if counts[task["family"]] >= record["config"]["maximum_per_family"]:
            task["collection_exclusion"] = "Family quota reached; retained as an audited result outside the diverse collection."
            continue
        job = store.job(task["job_id"])
        controls = [run for run in job["runs"] if run["kind"] in {"oracle", "nop"}]
        if (job.get("profile") != "search" or job["status"] != "completed"
                or job["summary"].get("learnable") is not True
                or len(controls) != 2 or any(run["status"] != "passed" for run in controls)
                or not job.get("metadata", {}).get("accepted")
                or not (job.get("metadata", {}).get("failure_audit") or {}).get("accepted")):
            raise ValueError("Refusing to export a task without five valid runs, controls, and failure-audit acceptance.")
        source = store.directory(job["id"]) / "task"
        if _digest(source) != job["task_sha256"]:
            raise ValueError("Evaluated task snapshot changed; refusing to export.")
        generation = task["attempts"][-1].get("generation", {})
        slug = re.sub(r"[^a-z0-9]+", "-", (generation.get("name") or task["id"]).lower()).strip("-")
        destination = ROOT / "output" / "learnable" / record["id"] / f"{slug}-{job['id'][:6]}"
        if destination.exists():
            if _digest(destination) != job["task_sha256"]:
                raise ValueError("Conflicting exported task; retained both original artifacts for inspection.")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp")
            if temporary.exists():
                shutil.rmtree(temporary)
            shutil.copytree(source, temporary)
            os.rename(temporary, destination)
        row = {"name": task.get("display_name") or generation.get("display_name") or slug,
               "family": task["family"], "batch_id": batch["id"], "task_id": task["id"], "job_id": job["id"],
               "prompt_version": batch["prompt_version"], "passes": job["summary"]["passes"],
               "valid_runs": 5, "task_sha256": job["task_sha256"], "path": str(destination.relative_to(ROOT)),
               "readme": str((destination / "README.md").relative_to(ROOT)), "accepted_at": store.now()}
        record["accepted"].append(row)
        counts[task["family"]] += 1
        known.add(job["id"])
        _save(record)
    controller._save(batch)


def update_scores(record, batch):
    scores = record["prompt_scores"].setdefault(batch["prompt_version"], {"slots": 0, "accepted": 0, "rounds": []})
    if batch["id"] not in scores["rounds"]:
        scores["slots"] += len(batch["tasks"])
        scores["accepted"] += sum(bool(t.get("accepted")) for t in batch["tasks"])
        scores["rounds"].append(batch["id"])
        scores["yield"] = scores["accepted"] / scores["slots"]
    # Empirical incumbent, not a statistical claim of superiority.
    record["best_observed_prompt"] = max(record["prompt_scores"], key=lambda v: (
        record["prompt_scores"][v]["yield"], record["prompt_scores"][v]["accepted"], v))


def stopping_reason(record):
    if len(record["accepted"]) >= record["target"] and len(record["families"]) >= record["config"]["minimum_families"]:
        return "target_reached"
    stagnant_limit = record["config"].get("stagnant_rounds", 0)
    if stagnant_limit > 0 and record["stagnant_rounds"] >= stagnant_limit:
        return f"No accepted task in {stagnant_limit} consecutive rounds; stop and brainstorm before spending more."
    if record.get("severe_bottleneck"):
        return record["severe_bottleneck"]["reason"]
    if len(record["rounds"]) >= record["config"]["max_rounds"]:
        return "Round limit reached; review observed yield before extending the search."
    if record["accounted_cost_usd"] + record["config"]["round_allowance_usd"] > record["config"]["max_cost_usd"]:
        return "Remaining search budget cannot cover another complete round."
    return None


def write_report(record):
    from datetime import datetime

    _refresh(record)
    candidates = []
    counts = Counter({key: 0 for key in (
        "candidate_slots", "attempted_slots", "structurally_validated_task_slots",
        "oracle_nop_valid_task_slots", "five_valid_outcome_task_slots",
        "audited_learnable_task_slots", "invalid_task_slots", "duplicate_task_slots",
        "inconclusive_task_slots", "generation_attempts")})
    costs = {"bootstrap_prompt_usd": record.get("bootstrap_cost_usd", 0),
             "task_generation_usd": 0.0, "semantic_review_usd": 0.0,
             "failure_audit_usd": 0.0, "solver_accounted_usd": 0.0,
             "prompt_updates_usd": 0.0}
    for batch in _batches(record):
        controller._costs(batch)
        costs["prompt_updates_usd"] += batch.get("update_cost_usd", 0)
        for task in batch["tasks"]:
            attempts = task.get("attempts", [])
            jobs = [store.job(job_id) for job_id in {a.get("job_id") for a in attempts} - {None}]
            structural = any(a.get("generation", {}).get("status") == "structurally_validated"
                             for a in attempts)
            # Read actual trial states, not optimistic task summaries. Both
            # controls must pass on the same snapshot for this stage to count.
            controls = any({r["kind"]: r["status"] for r in job["runs"]
                            if r["kind"] in {"oracle", "nop"}}
                           == {"oracle": "passed", "nop": "passed"} for job in jobs)
            five_valid = any(len(evaluations := [r for r in job["runs"] if r["kind"] == "evaluation"]) == 5
                             and all(r["status"] in {"passed", "failed"} for r in evaluations)
                             for job in jobs)
            accepted = task.get("accepted") is True
            outcome = (task.get("summary") or {}).get("outcome")
            audit = task.get("failure_audit") or {}
            inconclusive = (not accepted and task["status"] not in {"invalid", "duplicate"}
                            and (task["status"] in {"budget_exhausted", "error", "cancelled"}
                                 or outcome in {"budget_limited", "inconclusive", "execution_error"}
                                 or audit.get("verdict") == "inconclusive"))
            stage = {"attempted": bool(attempts), "structurally_validated": structural,
                     "oracle_nop_valid": controls, "five_valid_outcomes": five_valid,
                     "audited_learnable": accepted}
            counts.update({"candidate_slots": 1, "attempted_slots": bool(attempts),
                           "structurally_validated_task_slots": structural,
                           "oracle_nop_valid_task_slots": controls,
                           "five_valid_outcome_task_slots": five_valid,
                           "audited_learnable_task_slots": accepted,
                           "invalid_task_slots": task["status"] == "invalid",
                           "duplicate_task_slots": task["status"] == "duplicate",
                           "inconclusive_task_slots": inconclusive,
                           "generation_attempts": len(attempts)})
            for source, target in (("generation_cost_usd", "task_generation_usd"),
                                   ("review_cost_usd", "semantic_review_usd"),
                                   ("failure_audit_cost_usd", "failure_audit_usd"),
                                   ("solver_accounted_cost_usd", "solver_accounted_usd")):
                costs[target] += task.get(source, 0)
            candidates.append({"batch_id": batch["id"], "prompt_version": batch["prompt_version"],
                               "stages": stage, "generation_attempts": len(attempts),
                               **{key: task.get(key) for key in (
                                   "id", "display_name", "family", "status", "summary", "accepted", "job_id",
                                   "semantic_review", "failure_audit", "collection_exclusion", "accounted_cost_usd", "error")}})
    evaluated_slots = len(candidates)
    prospective = candidates[:10]
    counts["collected_task_slots"] = len(record["accepted"])
    costs["accounted_total_usd"] = record["accounted_cost_usd"]
    costs["note"] = "Accounted cost includes unresolved request reservations; it is not necessarily settled provider billing."
    as_of = store.now()
    elapsed = max(0.0, (datetime.fromisoformat(record.get("completed_at") or as_of)
                        - datetime.fromisoformat(record["created_at"])).total_seconds())
    report = {**record, "candidates": candidates, "counts": dict(counts), "costs": costs,
              "elapsed_wall_sec": round(elapsed, 3), "report_as_of": as_of,
              "measurement_notes": {
                  "stages": "Each stage counts candidate slots reaching it in at least one repair attempt; oracle/nop must both pass in the same actual job. These control checks alone do not establish semantic task validity.",
                  "elapsed_wall_sec": "Search creation to completion, or report time if active; includes queueing, generation, model waits, setup, evaluation, and verification."},
              "yield": {"accepted_collection": len(record["accepted"]), "candidate_slots": evaluated_slots,
                        "rate": len(record["accepted"]) / evaluated_slots if evaluated_slots else None,
                        "audited_learnable_rate": counts["audited_learnable_task_slots"] / evaluated_slots if evaluated_slots else None,
                        "first_ten_slots": len(prospective),
                        "first_ten_audited_learnable": sum(bool(t.get("accepted")) for t in prospective),
                        "note": "Adaptive collection, not an unbiased confirmation set. Every failed, invalid, duplicate and inconclusive candidate slot remains in the denominator; repair attempts are reported separately."}}
    path = ROOT / "reports" / f"search-{record['id']}.json"
    store.write_json(path, report)
    record["report_path"] = str(path.relative_to(ROOT))
    _save(record)
    prompts.export_history(ROOT / "prompts" / "history")


def finish(record, reason, status="checkpoint"):
    best = record.get("best_observed_prompt")
    if best and record["prompt_scores"][best]["accepted"]:
        prompts.activate_version(best, reason="Retain the best empirically observed prompt at the search checkpoint.")
    record.update(status=status, stop_reason=reason, phase="Search complete" if status == "completed" else "Checkpoint: review results",
                  completed_at=store.now())
    _save(record)
    write_report(record)


def process_search(record):
    os.environ["OPENROUTER_API_BASE"] = record["api_base"]
    os.environ["TASKLAB_AUTHOR_TIMEOUT_SEC"] = str(record["config"]["author_timeout_sec"])
    record.update(status="running")
    _save(record)
    if _stopped(record):
        finish(record, "Stopped by user.", "cancelled")
        return
    bootstrap(record)
    while True:
        if _stopped(record):
            finish(record, "Stopped by user.", "cancelled")
            return
        pending = [b for b in _batches(record) if b["id"] not in record["processed_rounds"]]
        if not pending:
            reason = stopping_reason(record)
            if reason:
                finish(record, reason, "completed" if reason == "target_reached" else "checkpoint")
                return
            archive = generated_archive()
            briefs = diversity.choose_briefs(record["attempted_count"], record["config"]["tasks_per_round"], archive)
            batch = controller.start_batch(profile="search", briefs=briefs, archive=archive,
                                           metadata={"search_id": record["id"]}, defer_update=True)
            record["rounds"].append(batch["id"])
            record.update(phase=f"Round {len(record['rounds'])}: generate, validate, and evaluate three fresh tasks")
            _save(record)
        else:
            batch = pending[0]
        while batch["status"] not in controller.TERMINAL:
            if _stopped(record):
                controller.stop_batch(batch["id"])
            _save(record)
            time.sleep(3)
            batch = store.read_json(controller._path(batch["id"]) / "batch.json")
        if _stopped(record):
            finish(record, "Stopped by user.", "cancelled")
            return
        if batch["status"] != "checkpoint":
            finish(record, f"Round stopped with {batch['status']}: {batch.get('error') or batch.get('phase')}")
            return
        collect(record, batch)
        update_scores(record, batch)
        hits = sum(bool(t.get("accepted")) for t in batch["tasks"])
        record["phase"] = "Reviewing the round and deciding whether to retain or revise its prompt"
        _save(record)
        if any(t["status"] == "error" for t in batch["tasks"]):
            finish(record, "A task encountered an authoring, infrastructure, audit, or incomplete-evaluation error. Inspect the retained evidence before continuing.")
            return
        reason = stopping_reason(record)
        if reason:
            record["processed_rounds"].append(batch["id"])
            finish(record, reason, "completed" if reason == "target_reached" else "checkpoint")
            return
        if not batch.get("search_prompt_decision"):
            if hits:
                prompts.mark_status(batch["prompt_version"], "validated", reason="Produced audited learnable tasks on a fresh batch; yield estimate remains small-sample.",
                                    evidence={"search_id": record["id"], "batch_id": batch["id"], "accepted": hits})
                batch["search_prompt_decision"] = "Keep this measured prompt for fresh tasks in other families."
            else:
                if batch.get("update_started_at") and batch.get("decision") in {None, "deferred_to_search", "update_deferred"}:
                    raise RuntimeError("Interrupted search prompt update; no automatic paid retry.")
                best = record["best_observed_prompt"]
                source = prompts.activate_version(best, reason="Use the best empirically observed prompt as the optimizer's starting point.")
                batch["update_source_prompt"] = source
                controller.propose_update(batch, correction_feedback={
                    "search_scope": (ROOT / "prompts/search_seed_prompt.txt").read_text(),
                    "prompt_scores": record["prompt_scores"], "stagnant_rounds": record["stagnant_rounds"],
                    "instruction": "Use only five-complete-run difficulty evidence. Improve validity or novelty when those are the bottleneck. Keep the expanded family scope and deterministic independent tests."})
                batch["search_prompt_decision"] = batch.get("decision")
            controller._save(batch)
        if batch.get("search_prompt_decision") in {"update_failed", "invalid_prompt_proposal"}:
            finish(record, "Prompt optimizer did not return a usable revision; inspect its evidence before retrying.")
            return
        record["processed_rounds"].append(batch["id"])
        _save(record)
        write_report(record)


def worker(once=False):
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A search worker is already running.")
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        while True:
            pending = [r for r in reversed(_records()) if r["status"] not in TERMINAL]
            if not pending:
                if once:
                    return
                time.sleep(1)
                continue
            record = pending[0]
            try:
                from phase2.runtime import keep_awake
                with keep_awake():
                    process_search(record)
            except Exception as exc:
                record["error"] = store.redact(str(exc))
                finish(record, store.redact(str(exc)), "error")
                print(store.redact(str(exc)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--resume", metavar="SEARCH_ID", help="Resume a reviewed search checkpoint; retained task attempts are not reset")
    args = parser.parse_args()
    if args.start:
        print(json.dumps(start_search(), indent=2))
    if args.stop:
        print(json.dumps(stop_search(), indent=2))
    if args.resume:
        print(json.dumps(resume_search(args.resume), indent=2))
    if args.worker:
        worker(args.once)
    if not (args.start or args.stop or args.resume or args.worker):
        print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
