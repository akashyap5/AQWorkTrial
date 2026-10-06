# Changelog

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
