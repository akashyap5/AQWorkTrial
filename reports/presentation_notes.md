# Learnable task generation for GLM-5.3-flash: notes (2026-10-07)

## Goal

GLM-5.1 generates original Harbor tasks. A task counts as learnable when GLM-5.3-flash (high reasoning,
terminus-2) passes 1-3 of 5 runs, the oracle passes, and no-op fails. Timeouts are not failures.

## What we learned (measured, `reports/failure_rules.md`)

1. **An independent check makes a task easy.** Given a runnable oracle, round-trip tool, simulator, or readable
   binary, the agent verifies until it is correct:
   - game probe v1: 5/5
   - game probe v2: 4/5, after the agents disassembled the binary
   - write-compressor and model-extraction: every pass verified against the shipped checker
2. **Hard public tasks time out instead of failing.** Across 7 TB2 tasks ranked hard: 12 of 35 runs solved, 20
   timed out, 3 wrong. Under TB2's own shorter limits, several passes would have been timeouts.
3. **Size and depth don't help.** About 48 GLM bug-fix tasks scored 4-5/5; image-grid puzzles passed 29 of 30 runs.
4. **Famous traps don't transfer to an agent.** Car-wash, sibling-perspective, rounding, and empty-field traps:
   25/25 passed. Silent data-visible edge cases: 15/15 passed. Single stated traps are caught about 97% of the time.
5. **What does fail it:**
   - rushed slips on stated details (a few percent per detail, only in fast runs)
   - unverifiable perception (O vs 0 on gcode-to-text: 3 of 4 completed runs wrong)
   - unstated behaviour that the tests check, which is unfair
6. **The lever that lands in band is checklist breadth.** One easy task carries 8-12 independent stated requirements
   (dense ranking, `Mon DD`, separator scope, ...). About 65% of these tasks landed at 1-3/5.

## Quality gate (GLM-5.1, README-compliant, policy fixed at 14:35)

Three independent GLM-5.1 audits each get:
- the instruction
- the full failing assertions
- the failing tests' inputs
- the hidden reference

An audit counts as fair when at least half of the failed runs (and at least one) trace to a requirement quoted
verbatim from the instruction or docs. A task ships on 2 of 3 fair audits. Otherwise the auditor's clarification is
appended to the instruction and the task is re-probed automatically. There are no manual fairness interventions.

The policy is deliberately lenient, but defensible: every shipped failure is backed by a quoted, verified sentence.

## Shipped (GLM-5.1, gated): 5 as of 14:45

| Task | Result | Failures the gate traced to stated rules |
|---|---|---|
| catalog-export-checklist | 3/5 | `Jan 1` vs the stated `Mon DD`; product count vs the example's quantity count |
| leaderboard-checklist-format | 2/5 | competition ranking (1,2,2,4) vs the stated dense ranking |
| metrics-log-checklist-summary | 3/5 | comma-separated values passed to Decimal; empty fields lost to whitespace splitting |
| workshop-timetable-checklist-export | 2/5 | the stated blank line before Total, missing or doubled |
| shift-roster-checklist-export | 2/5 | shipped after a gate clarification and re-probe |

The domains and failure mechanisms differ, but all five share one shape (tabular input -> formatted report).
Prompt t007 keeps the lever and varies the shape: a CLI with exit codes, a file reorganiser, a config migrator, an
event-log replay, a validator, an allocator, a normaliser, and a ledger reconciler.

A keeper sustains about 32 GLM-5.1 authors until 14 tasks are shipped or 19:00. The sweeper gates and re-probes
automatically.

## Pipeline robustness (Part 4, tested)

| README requirement | What we built |
|---|---|
| Records | `output/ledger.jsonl` (every event), `verdict.json` per task, batch state files, versioned prompts and strategy library |
| Resume | `--resume latest` re-attaches to existing probe jobs (kill -9 test: 0 re-authored, 0 re-probed); runs interrupted by a worker restart are re-queued in place |
| Concurrency | atomic slug reservation and per-slot locks (two-copy tests: 8 unique tasks, no corrupt records, one copy skips claimed slots) |
| Free verification | `FAST_AUTHOR_FAKE=1` |

## Meta-learning

- **Prompt versions:** grid v028-v032 and trap t001-t006, each with a changelog row stating why it changed.
- **Strategy library:** hard/easy/avoid levers with per-lever pass counts; GLM-5.1 strategist edits from failure
  forensics.
