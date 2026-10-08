# Prompt evolution history

Current prompt at export: **v0006**. Candidate does not mean validated.

This is a portable snapshot of the runtime registry. Existing versions were recovered from immutable files and state records. Creation/status timestamps are preserved; missing historical activation times and ordering are unknown. Journal `recorded_at` is the time of recovery or logging, not a guessed historical event time.

Runtime updates append and fsync `.tasklab/prompts/history.jsonl` under the registry lock **before** publishing a prompt, changing its status, or replacing the active pointer. A matching `applied` record follows a successful mutation. An intent without an applied record may be incomplete; the current registry files remain authoritative. Rejected versions are retained. The journal includes full text, hashes, parents, model feedback, evidence, activations, and rollbacks.

Read or refresh from the repository root (neither command calls a model or starts a batch):

```sh
.venv/bin/python -m phase2.prompts history
.venv/bin/python -m phase2.prompts export --output prompts/history
```

Python: `from phase2.prompts import history; history()`. Use `--registry-dir PATH` (before the subcommand), `TASKLAB_PROMPTS_DIR`, or `TASKLAB_DATA` for a different registry. A fresh checkout seeds from `prompts/starter_prompt.txt`; these exports do not silently change the active prompt.

The original checkpoint evidence was attached idempotently with:

```sh
.venv/bin/python -m phase2.prompts history --recover-report reports/phase-2-checkpoint.json
```

## Versions

| Version | Parent | Status | Created (UTC) | Changes |
| --- | --- | --- | --- | --- |
| [v0001](v0001.txt) | — | baseline | 2026-10-06T21:00:23.134031+00:00 | Seed |
| [v0002](v0002.txt) | v0001 | rejected | 2026-10-06T21:55:17.200912+00:00 | [Diff](v0001-to-v0002.diff) |
| [v0003](v0003.txt) | v0001 | candidate | 2026-10-06T21:59:52.965523+00:00 | [Diff](v0001-to-v0003.diff) |
| [v0004](v0004.txt) | v0003 | candidate | 2026-10-06T22:58:57.681856+00:00 | [Diff](v0003-to-v0004.diff) |
| [v0005](v0005.txt) | v0004 | candidate | 2026-10-06T23:39:56.260446+00:00 | [Diff](v0004-to-v0005.diff) |
| [v0006](v0006.txt) | v0005 | candidate | 2026-10-06T23:45:44.582038+00:00 | [Diff](v0005-to-v0006.diff) |

## Decisions and feedback

### v0001

SHA-256: `078b16ab234372088471ae54320563d210f839f326410f9742caca577dbf2045`.

### v0002

SHA-256: `2304ba15f6cb7f2498ca620dbe2fdb93f7da01700bc89fe562ce98be5002e5cd`.

**Rationale:** Two of three tasks were solved by all five attempts (too easy: bugs were single-line fixes visible on first read), while the third exhausted the budget on every attempt (too hard: 12 fixture files and multiple interacting rules exceeded reasoning time). The prompt needs to calibrate between these extremes by requiring interacting bugs where fixing one alone is insufficient, and by limiting fixture count so the solver can reason within the demo budget.

**Expected effect:** Next batch should yield tasks where 1-3 of 5 solver attempts succeed, because bugs will require understanding rule interactions rather than spotting a single wrong line, and fixture scope will stay within budget reasoning limits.

**Uncertainty:** The interacting-bugs guidance may not be sufficient if the model generates bugs that are still too obvious when combined, or if it overcorrects toward excessive complexity. Fixture count guidance is approximate; the right number depends on rule complexity.

**Rejected:** Unsupported difficulty claim: all ledger attempts were budget-limited; first three were also confounded by confirmed host sleep. There is no valid 0/5 difficulty measurement or evidence that fixture count caused timeouts.

Source: `reports/phase-2-checkpoint.json` (recorded 2026-10-06T22:02:29.238577+00:00).

### v0003

SHA-256: `f47e097b1ef60c06fe9947acac86c07e3b9c789ff0b70a64b2ff8d2781dfbc6c`.

The prompt wording is unchanged from v0002 apart from surrounding whitespace. This version preserves its own feedback, provenance, and status; it does not demonstrate an additional content improvement.

**Rationale:** Two completed tasks (manifest-reconciliation, ordered-rule-resolution) both achieved 5/5 solver success, indicating they fell below the target difficulty band. Each had isolated bugs fixable by one obvious edit. The prompt should require interacting defects so that partial fixes leave most tests failing, forcing the solver to understand rule interactions. Limiting fixtures to three to five keeps the task compact for the demo budget; the ledger task's 12 fixtures required multiple turns just to read, though its budget outcomes are inconclusive due to confirmed host-sleep confounding on runs 1–3.

**Expected effect:** Next batch should yield tasks where success requires understanding how two or three rules interact, producing 1–3 successes out of 5 valid solver attempts rather than 5/5 or 0/5.

**Uncertainty:** The ledger task's difficulty is genuinely unknown (host sleep confounded 3 of 5 runs; runs 4–5 also timed out but at shorter wall durations). It is possible that interacting defects plus fewer fixtures will overshoot into too-difficult territory; this must be measured, not assumed. The 5/5 easy outcomes may also reflect the specific topics chosen rather than the prompt alone.

### v0004

SHA-256: `c6a31d68cb9023f9c44154f4b13a19539ddc85c5f529de131e682dc4d754476b`.

### v0005

SHA-256: `5001d020b5c21a83520f68db4fc316129559b8e85e2ca942a3e3e4f7d386426c`.

**Rationale:** All three tasks in the v0004 batch were invalid due to authoring defects in the reference solution, not difficulty or scope issues. The transaction-recovery task failed oracle verification (test_multiple_checkpoints returned 'None' instead of '3'). The dependency-planning and streaming-protocol tasks had reference solutions with incorrect in-degree computation and broken escape-sequence state machines respectively. The prompt already requests an independent contract checklist, but this hasn't prevented the generator from producing reference solutions that disagree with their own tests. Adding an explicit post-authoring verification step—requiring the generator to confirm the reference solution passes every test before finalizing—directly addresses the observed failure mode: reference-solution/test contract mismatches.

**Expected effect:** Next batch should yield a higher proportion of valid tasks (oracle and nop controls both passing) because the generator will catch and fix reference-solution/test mismatches before submission, reducing the dominant authoring defect observed in v0004.

**Uncertainty:** The verification instruction relies on the generator model faithfully simulating test execution, which may not catch subtle bugs (e.g., the streaming-protocol ESC state machine error). The root cause may also partly be model capability limits on complex stateful code. Adding verification may slightly reduce task complexity if the generator simplifies contracts to ensure they pass, but this is preferable to invalid tasks.

### v0006

SHA-256: `77c330e8366b3e4dcf2b31c177f4d27e185c3195021d1d2dde2eedb580cbd3a2`.

**Rationale:** All 6/6 tasks across v0004 and v0005 were invalid due to reference-solution/test contract mismatches. The v0005 CRITICAL instruction asked the generator to 'verify that the reference solution produces the correct output for every test case' but this was too vague—the generator apparently did not actually trace through the failing scenarios. The specific failure patterns are highly consistent: (1) boundary condition errors (lease_until < vs <=, rmdir inclusion when no temp paths used), (2) multi-step state sequences (delete-then-reinsert provenance, partial-apply-then-resume journal recovery, rename chain content mapping), (3) queries without transitions (pending/expired before tick, provenance table existence). Naming these three specific failure categories in the prompt gives the generator concrete checkpoints to trace, making the verification instruction actionable rather than aspirational.

**Expected effect:** Next batch should yield a higher proportion of valid tasks (oracle and nop controls both passing) because the generator will trace through the three named failure categories—boundary transitions, multi-step state sequences, and queries-without-transitions—catching the bugs that currently slip through vague verification instructions.

**Uncertainty:** The generator model may still not faithfully trace complex multi-step scenarios even with explicit checkpoints, especially for tasks with many interacting rules. The three named categories cover the observed failures but may not cover all future failure modes. There is a risk the generator simplifies contracts to avoid these categories, slightly reducing task complexity, but this is preferable to continued 100% invalid-task yield.

Full registry records and the write-ahead journal are in [history.json](history.json). Refresh this export after later batches to include subsequent changes.
