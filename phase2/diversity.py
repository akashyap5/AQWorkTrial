"""Low-cost task diversity controls, independent of model calls and paid runs.

Families rotate by least historical exposure. Fingerprints detect strong textual
or structural duplication; they are deliberately conservative and are not an
embedding-based difficulty predictor. A separate semantic review should compare
solution approaches using ``compact_archive`` before spending on solver trials.
"""
from __future__ import annotations

import ast
import hashlib
import re
from collections import Counter
from pathlib import Path

FAMILIES = (
    {"family": "transaction-recovery", "title": "Storage and recovery",
     "topic": "Repair an original persistent storage subsystem with a public Python API and an on-disk format. Use interacting transaction, recovery, and visibility invariants; make recovery reproducible by explicit crash points or supplied partial records, never real timing races. Provide incomplete modules for the store and recovery path, with a fully specified format and deterministic restart tests.",
     "variants": ("segmented journal replay and snapshot boundaries", "copy-on-write pages and atomic commit visibility", "savepoints, rollback, and recovery from incomplete batches")},
    {"family": "dependency-planning", "title": "Dependency planning and builds",
     "topic": "Repair an original local build or dependency planning subsystem over fixture projects. The central work is graph reasoning with interacting constraints, deterministic planning, and correct invalidation or conflict explanation. Include existing planner modules and fixture project files, not merely a command that sorts a list. Specify cycles, tie breaking, unavailable alternatives, and observable behavior where relevant; all dependencies are local.",
     "variants": ("conditional dependencies and incremental rebuild invalidation", "version constraints and deterministic alternative selection", "artifact graph scheduling with dynamic dependency discovery")},
    {"family": "streaming-protocol", "title": "Streaming protocols and parsers",
     "topic": "Repair an original incremental decoder and protocol state machine exposed as a library. Inputs arrive in arbitrary chunks and the parser must preserve state across calls. Give an exact grammar or byte format and public error/resynchronization semantics. Include existing decoder and session modules, realistic encoded fixtures, and independent expected results. Tests must exercise split boundaries and stateful sequences without network access.",
     "variants": ("escaped framed messages with checksum and resynchronization", "nested incremental grammar with backpressure and end-of-stream validation", "multiplexed stream sequencing and deterministic replay")},
    {"family": "filesystem-migration", "title": "Filesystem migration",
     "topic": "Repair an original filesystem migration tool that plans and applies a nontrivial transformation to a local directory tree. Its solution should involve safe operations, dependencies between changes, and restart or rollback behavior rather than a table transformation. Supply real directory fixtures and partial planner/executor code. State path, collision, link, metadata, and failure rules explicitly where they apply; use controlled fault injection instead of OS timing.",
     "variants": ("rename cycles and resumable migration journals", "content-addressed extraction with links and path confinement", "atomic directory replacement with rollback and preserved metadata")},
    {"family": "relational-transforms", "title": "Relational data transformations",
     "topic": "Repair an original local relational transformation engine or SQLite-backed pipeline with interacting data semantics. Give an exact schema, supported operations, ordering, and null/duplicate/error behavior. The application should contain multiple unfinished components such as query planning, execution, and update propagation. Use small data fixtures that reveal algorithmic errors rather than a large-volume benchmark.",
     "variants": ("incremental joins with deletions and provenance", "event-time interval joins and deterministic correction handling", "schema evolution with constraints and transactional backfill")},
    {"family": "message-delivery", "title": "Stateful message delivery",
     "topic": "Repair an original deterministic local message delivery or actor simulation with a public API. The central challenge is stateful coordination: delivery, acknowledgment, retries, cancellation, or deduplication interact under an explicitly controlled event sequence. Provide partial broker/state-machine modules and a logical clock. All tests must run in one process or controlled subprocesses without wall-clock races or external services.",
     "variants": ("leases, acknowledgment fencing, and redelivery", "ordered subscriptions with replay cursors and cancellation", "request correlation with retries and stale response suppression")},
    {"family": "resource-scheduling", "title": "Resource scheduling",
     "topic": "Repair an original deterministic scheduler that allocates constrained local resources across jobs. Use an actual scheduling or search algorithm with precedence and state transitions, not simply a priority sort. Supply partially implemented planner and simulation modules. Define fairness or optimization objectives, infeasibility, tie breaking, and update behavior precisely, and test small independently verifiable schedules.",
     "variants": ("preemption with reservations and dependency constraints", "multi-resource admission with bounded lookahead and cancellation", "calendar interval packing with precedence and rescheduling")},
    {"family": "configuration-semantics", "title": "Configuration compilation",
     "topic": "Repair an original configuration compiler with a documented small language and distinct parsing, resolution, and evaluation stages. The central work should involve lexical scope, references, types, or recursive expansion and their interaction, not renaming keys. Provide partial modules and fixture programs. State evaluation order, errors, and cycle behavior explicitly; validate observable compiled outputs and diagnostics without requiring one implementation.",
     "variants": ("scoped references with type checking and cycle diagnostics", "template expansion with lexical binding and source locations", "layered configuration with lazy expressions and dependency resolution")},
)

AUTHORING_ENVELOPE = (
    "Invent a fresh scenario and solution structure within this direction. Supply meaningful "
    "partial application code, a readable descriptive task name, and a plain-language objective. "
    "Choose coherent interacting requirements and substantive implementation work; no fixed "
    "two-or-three-rule ceiling applies. Public instructions must state every tested behavior. "
    "Build independent deterministic tests for the full contract and adversarial combinations, "
    "plus a correct reference solution. Avoid ambiguity, network dependencies, arbitrary effort, "
    "and merely changing a previous task's names or constants."
)


def choose_briefs(start_index: int, count: int, archive: list[dict] | None = None) -> list[dict]:
    """Choose least-seen families, resolving ties with a reproducible rotation.

    All attempts count as exposure, including invalid or duplicate tasks, so a
    difficult family cannot silently starve the rest of the search. The caller
    persists selected briefs before generation and supplies prior attempts.
    """
    if isinstance(start_index, bool) or not isinstance(start_index, int) or start_index < 0:
        raise ValueError("start_index must be a nonnegative integer")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count must be a nonnegative integer")
    exposure = Counter(row.get("family") for row in archive or [])
    rotation = list(FAMILIES[start_index % len(FAMILIES):] + FAMILIES[:start_index % len(FAMILIES)])
    result = []
    for index in range(count):
        family = min(rotation, key=lambda item: exposure[item["family"]])
        previous = exposure[family["family"]]
        variant = family["variants"][previous % len(family["variants"])]
        result.append({"id": f"{family['family']}-{start_index + index + 1:04d}",
                       "family": family["family"], "name": family["title"],
                       "solution_pattern": variant,
                       "topic": f"{family['topic']}\nExplore: {variant}.\n{AUTHORING_ENVELOPE}"})
        exposure[family["family"]] += 1
    return result


def compact_archive(archive: list[dict] | None, limit: int = 12) -> list[dict]:
    """Return bounded, solution-aware descriptions without source or private tests.

    Prefer one recent member of each family, then fill remaining slots with recent
    tasks. Unknown family values are retained; they are still useful evidence.
    """
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    if not limit:
        return []
    rows = list(archive or [])
    chosen, used, families = [], set(), set()
    for index in reversed(range(len(rows))):
        family = rows[index].get("family")
        if family not in families:
            chosen.append(index)
            used.add(index)
            families.add(family)
        if len(chosen) == limit:
            break
    for index in reversed(range(len(rows))):
        if len(chosen) == limit:
            break
        if index not in used:
            chosen.append(index)
    fields = ("id", "family", "name", "display_name", "description", "solution_pattern",
              "status", "outcome")
    return [{key: str(rows[index][key])[:800] for key in fields if rows[index].get(key) is not None}
            for index in sorted(chosen)]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z_][a-z_0-9]*|\d+", text.lower())


def _features(tokens: list[str], width: int) -> dict:
    shingles = {hashlib.sha256(" ".join(tokens[i:i + width]).encode()).hexdigest()[:16]
                for i in range(max(0, len(tokens) - width + 1))}
    return {"sha256": hashlib.sha256(" ".join(tokens).encode()).hexdigest(),
            "token_count": len(tokens), "shingles": sorted(shingles)[:4096]}


class _Shape(ast.NodeTransformer):
    """Discard names/literals and standard import/docstring boilerplate, not logic."""
    def visit_Import(self, node):
        return None

    def visit_ImportFrom(self, node):
        return None

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)

    def generic_visit(self, node):
        node = super().generic_visit(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.name = "symbol"
        if isinstance(node, ast.Name):
            node.id = "symbol"
        if isinstance(node, ast.arg):
            node.arg = "symbol"
        if isinstance(node, ast.Attribute):
            node.attr = "member"
        if isinstance(node, ast.Constant):
            # Retain literal type and branching structure while detecting reskins
            # that only replace filenames, examples, or numerical constants.
            node.value = type(node.value).__name__
        return node


def _source_features(paths: list[Path]) -> dict:
    tokens = []
    for path in sorted(paths):
        source = path.read_text(errors="replace")
        try:
            tree = _Shape().visit(ast.parse(source))
            shape = ast.dump(tree, annotate_fields=False, include_attributes=False)
            tokens.extend(_tokens(shape))
        except SyntaxError:
            # Intentionally broken starter code can fail parsing; conservatively
            # compare its text rather than dropping a whole initial application.
            tokens.extend(_tokens(source))
        tokens.append("file_boundary")
    return _features(tokens, 8)


def fingerprint_task(task_path: str | Path) -> dict:
    """Fingerprint public instructions, initial Python code, and verifier shape.

    Dockerfiles, framework test.sh, dependencies and README boilerplate are omitted.
    Application and test shape are only strong signals together; generic pytest
    fixtures or two empty starters alone cannot cause duplicate rejection.
    """
    task = Path(task_path)
    instruction = (task / "instruction.md").read_text()
    return {"version": 1,
            "instruction": _features(_tokens(instruction), 5),
            "initial_code": _source_features(list((task / "environment").rglob("*.py"))),
            "tests": _source_features(list((task / "tests").rglob("*.py")))}


def _similarity(left: dict, right: dict) -> float:
    a, b = set(left.get("shingles", [])), set(right.get("shingles", []))
    return len(a & b) / len(a | b) if a and b else 0.0


def duplicate_check(fingerprint: dict, archive: list[dict] | None) -> dict:
    """Reject only strong duplicate evidence; return best-match diagnostics.

    Semantic similarity is not a failure criterion by itself. The thresholds are
    intentionally conservative; a semantic reviewer still audits new solution
    structures using compact archive entries.
    """
    best = {"duplicate": False, "reason": None, "match_id": None, "scores": {}}
    best_score = -1.0
    for entry in archive or []:
        other = entry.get("fingerprint") or {}
        if other.get("version") != fingerprint.get("version"):
            continue
        scores = {key: round(_similarity(fingerprint[key], other.get(key, {})), 4)
                  for key in ("instruction", "initial_code", "tests")}
        instruction = fingerprint["instruction"]
        same_instruction = instruction["sha256"] == other.get("instruction", {}).get("sha256")
        substantial = all(min(fingerprint[key]["token_count"],
                              other.get(key, {}).get("token_count", 0)) >= threshold
                          for key, threshold in (("initial_code", 120), ("tests", 120)))
        reason = None
        if same_instruction:
            reason = "identical_normalized_instruction"
        elif min(instruction["token_count"], other.get("instruction", {}).get("token_count", 0)) >= 40 and scores["instruction"] >= 0.9:
            reason = "near_identical_instruction"
        elif substantial and scores["initial_code"] >= 0.96 and scores["tests"] >= 0.96:
            reason = "same_initial_code_and_test_structure"
        combined = scores["instruction"] + scores["initial_code"] + scores["tests"]
        if reason or combined > best_score:
            best = {"duplicate": bool(reason), "reason": reason,
                    "match_id": entry.get("id"), "scores": scores}
            best_score = combined
        if reason:
            break
    return best
