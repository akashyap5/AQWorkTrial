# Changelog

## 2026-10-07 — Checklist tasks, a frozen fairness gate, robustness, and a difficulty dial

The image family and single-trick tasks rarely failed the solver: with one stated trick per task, 1 of 31 first probes
(prompts t001–t004) landed in 1–3/5, because the agent checks any single rule. The trap author prompt (t005) instead asks
GLM-5.1 for one easy formatter or exporter with 8–12 independent, plainly stated rules that each differ from the
programming default. The solver slips on a few percent of stated rules (0.93^10 ≈ 0.48), so breadth lowers the pass
rate without timeouts or ambiguity. On that prompt, 6 of 12 first probes landed in band.
A GLM-5.1 strategist edits a lever library after each probe. Every authoring prompt is versioned
(`prompts/trap_author_versions`), and `generator/prompt_governor.py` restores the best-measured instructions when the
current version trails by 15 points over 8 probes or 60% of 5 probes are too easy. It restored t005 as t012.
Fairness gate (policy fixed at 14:35, no manual overrides): three GLM-5.1 audits trace failed runs to requirements
quoted verbatim from the instruction. A task ships when 2 of 3 audits trace at least half of its failed runs.
Otherwise the auditor's clarification is appended and the task is re-probed. The first decision on a probe job is
final (`output/gate_decisions.jsonl`), and every path (batch slots, re-probes, `generator/gate_sweeper.py`) uses the
same gate. The sweeper withdraws any shipped task without an accepted first decision. It withdrew five that resumed
batch slots had re-audited after the gate held them.
Most 0/5 results were unstated output formats (every run missed the same assertion), so 0/5 probes are now audited
too, and the auditor's clarification replaces the author's hint. Clarified tasks usually measure too easy (11 of 15),
so a rule-count dial (stretch goal A) asks GLM-5.1 to add two or three more stated rules to a too-easy task,
re-validates it, and re-probes it once. In early runs, 1 of 3 dialled tasks landed in band, and the gate held it for an
ambiguity the new rule introduced.
Robustness (Part 4): an append-only ledger, a `verdict.json` per task, durable batch state with slot claims, atomic
task-name reservation, `--resume`, and a fake mode for tests. Found and fixed during the run: a non-reentrant lock that
self-deadlocked the strategist and froze generators from 12:00 to 16:15, an early-stopped probe re-run after resume,
and leaked Docker networks exhausting address pools (the sweeper now prunes them; at most six concurrent jobs).
Shipped tasks now record `difficulty = "medium"` with `calibration = "learnable"`, because Harbor's schema rejects
`difficulty = "learnable"`.
Task Lab now shows shipped tasks with every measurement of each (collapsed by default), the prompt history (collapsed),
and the strategy library's evolution. Re-measuring leaderboard-checklist-format gave 2/5 three times out of three.
Tradeoffs: probes stop after four passes because the band is already decided; the gate passes a task when half
of its failed runs trace to a stated rule; shipped tasks vary by domain and shape but share one difficulty mechanism.

## 2026-10-07 — Measured failure rules and image-decoding task family

Wrote `reports/failure_rules.md` from about 100 five-run probes of GLM-5.3-Flash/high (GLM-5.1 tasks, exploration
probes, and public Terminal-Bench 2 tasks run only as diagnostics). Any independent check (a runnable oracle,
round-trip tool, or readable internals) makes a task easy. Repeatable wrong submissions came only from
decode-and-trust steps (a square board read transposed, `O` read for `0`). Large or deep tasks time out instead
of failing.
The GLM-5.1 author prompt (v028) now targets one family built on that evidence: original grid puzzles whose
boards exist only as PNG images, a partial legend completed by a worked example, and all minimal move sequences
as the answer.
The pipeline injects the shared board literal into the hidden tests, creates per-board starter gaps, fills
reference answers from the tests, appends a 15-minute time box, and rejects unfair renders: tiles must be
pixel-identical, distinct, and present in the example; board text may not be visible; boards must be
solvable within bounded lengths.
Probe jobs created by `generator/fast_author.py` now default to the OpenRouter endpoint. Jobs created without
`OPENROUTER_API_BASE` had fallen back to an endpoint that rejects OpenRouter keys and errored before the first
model call.

## 2026-10-06 — Sustained diverse-task search

Added an explicitly started search that targets ten audited learnable tasks in
three-task rounds with concurrent author/review pipelines, five GLM-5.3-Flash/high attempts per valid task, and at most
five Docker trials in parallel.
Search attempts allow 600 seconds and $0.20 inference, while timeouts and budget
stops remain inconclusive rather than model failures.
Generation now rotates task families, checks generated-task fingerprints and
solution patterns for duplication, and requires failure audits before exporting
accepted tasks with readable names and expandable README overviews.
GLM-5.1 revises prompts from measured evidence, retains productive prompts for
fresh tasks, and preserves every revision before activation; author calls favor throughput and disable optional reasoning after observed reasoning-only truncations, while repairs retain the latest valid source and reviews require matching task revisions for execution evidence.
The search stops for sustained measured stagnation across multiple prompt revisions or other
configured limits, and reports adaptive yield as accepted tasks over all candidate
slots rather than treating the ten-task target as a 10/10 result.
Added code-accessible search controls, accepted-task exports, and historical
reports; achieving ten accepted tasks remains an execution goal.
Restored the original work-trial README and moved implementation documentation to `NOTES.md`.

## 2026-10-06 — Recoverable prompt history

Recovered the three existing prompt versions, diffs, and rejection evidence into
`prompts/history/` without changing the active `v0003` candidate.
Added a durable journal that records prompt changes before replacing registry
state, plus Python and CLI commands to inspect history or export a readable snapshot.
Recovery distinguishes observed snapshots from historical events whose exact timing
is unavailable, and preserves rejected proposals.
Clarified that operational timeouts remain inconclusive and cannot supply the
failed attempts needed for a learnability claim.

## 2026-10-06 — Phase 2 prompt-evolution checkpoint

Added a three-task generation loop with semantic review, bounded repairs,
oracle/nop gates, and five GLM-5.3-Flash/high attempts per valid task.
Demo attempts use five-minute and $0.05 inference limits, with a $3 batch
allowance and separate outcomes for budget stops, invalid tasks, and execution errors.
GLM-5.1 now proposes prompt revisions from recorded results and solver traces;
each new batch automatically reads the current version and stops for review.
The UI exposes prompt text, revision diffs, generation feedback, and accounted
inference costs, while candidate revisions remain explicitly unvalidated.
At the live checkpoint, all controls passed, two tasks finished 5/5, and one was
budget-limited; GLM repaired a faulty test expectation and saved corrected prompt
`v0003` after its first proposal made an unsupported difficulty claim.
All 208 automated checks and desktop/mobile UI checks pass, and the next batch
remains stopped pending review of the unvalidated candidate.

## 2026-10-06 — Task overview preview

Added an expandable README overview beneath the example selector, with formatted
headings, lists, and code alongside the existing task instructions.
Verified all six previews in Chrome, including the mobile layout, and passed all
65 runner/API tests without launching model runs.
Fixed the development launcher's port check so the idle UI can restart immediately.

## 2026-10-06 — Phase 1 validation checkpoint

Implemented baseline GLM-5.1 task generation and the example Task Lab UI with
oracle/nop checks, five solver slots, and turn-by-turn inspection.
Docker execution is limited to three concurrent trials.
All six examples passed their controls, three examples achieved 5/5 solver
passes each, and all 89 automated tests passed.
Stopped remaining model attempts once the infrastructure smoke test was
sufficient; cancelled attempts are excluded from solve rates.
The generated smoke task passes its controls but still needs semantic review,
and adaptive generation remains deferred pending approval.

## 2026-10-06 — Baseline generator and example Task Lab

- Implemented GLM-5.1 task authoring with structured file bundles, safe paths,
  deterministic Harbor configuration/verifier boilerplate, canaries, syntax and
  schema checks, bounded structural repair, and redacted authoring evidence.
  Functional checks remain explicit rather than being implied by schema success.
- Adapted the earlier take-home's Task Lab UI for an example dropdown, oracle
  and nop controls, five fixed GLM-5.3-Flash/high attempts, per-run tests, and live
  terminal turns. Docker concurrency is three; the evaluator has no added turn
  limit. The adaptive generation phase remains deferred for user review.
- Added a separate file-backed worker with immutable task snapshots, atomic
  per-run state, exclusive worker locking, cancellation, and restart recovery.
  Completed trials are reused; ambiguous interrupted work is never silently
  repaid. Jobs wait while Docker is unavailable.
- Parse Harbor trial results and all CTRF suites, including Bottle's original
  regression suite. Require nonempty matching test sets, distinguish verifier
  setup/collection failures, and exclude timeouts/errors from solve counts.
- Repaired `fix-git`'s unavailable public GitHub dependency by copying the exact
  original fixture from its pinned supplied image. Preserve the task's original
  detached history, oracle, tests, and intended behavior.
- Added 89 passing offline generator/runner/API tests. Live Chrome validation
  confirms real turns, tests, and logs render correctly, open details survive
  polling, and the layout fits a 390px mobile viewport. Pinned tested Python
  dependencies and added local UI/evaluation commands.
- Collect every generated verifier test module under `/tests`; a regression
  check proves an additional failing module changes the reward to zero. Keep
  application subprocesses on system Python, outside the verifier's isolated
  environment. Increased the authoring response limit to accommodate reasoning
  tokens as well as the task bundle; truncated bundles are never published.
- Verified the provided key authenticates on OpenRouter directly; the README's
  AfterQuery gateway returns 401 for this key. Endpoint remains configurable.
- Completed live oracle/nop verification for all six examples. OpenSSL,
  log-summary, and fix-git each produced five valid solver passes (15/15 total).
  Stopped remaining model work at the user's smoke-test checkpoint; cancelled
  attempts are excluded from scores. Observed concurrency peaked at three.
- Recorded `reports/phase-1-validation.json`, including job IDs, task hashes,
  control/test counts, solver outcomes, and browser checks. The original
  generated smoke task was withheld after semantic review. Its GLM-authored
  repair passes oracle/nop but remains explicitly `needs_review` because of
  overly strict assertions. No learnability claim is made for generated tasks.
