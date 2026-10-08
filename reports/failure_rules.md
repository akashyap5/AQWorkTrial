# What makes GLM-5.3-flash fail (measured rules)

Solver: `openrouter/z-ai/glm-5.3-flash`, reasoning effort high, terminus-2, 5 runs per task.
Pass = all hidden tests pass. Timeouts and infrastructure errors are excluded from model failures (README).
Evidence below comes from Task Lab jobs in `.tasklab/jobs/`. Public Terminal-Bench 2 tasks were run only as
diagnostics (copies under `output/diagnostics/`, never shipped or adapted). Claude-authored probes
(`generator/probes/`) are exploration only and are excluded from the shipped set and from the yield.

## Rule 1: an independent check makes a task easy

If the environment gives the agent any independent way to confirm an answer, the agent finds it, uses it, and
passes. Independent checks include a runnable reference that accepts arbitrary inputs, readable internals, a
round-trip tool, a command that reports counts, or a text form of the input.

| Task | Independent check the agent used | Result |
|---|---|---|
| probe-hidden-rules-game (v1) | binary accepted custom level files: agents built micro-boards per tile, ran randomized differential tests against their simulator, one run disassembled the binary | 5/5 |
| tb2-write-compressor | the decompressor: round-trip until output matches | 3 pass, 2 timeouts |
| tb2-model-extraction-relu-logits | the query oracle | 5/5 |
| probe-silent-export | `ledgerctl stats` revealed the silently skipped rows | 5/5 |
| probe-synth-stack, probe-mutant-kill-parking | interpreter / test runner available | 5/5, 5/5 |

**Design consequence:** a task can only be hard if the decisive step can't be confirmed from inside the
container. The test must be hidden, the input must not be reproducible from an oracle, and no tool may reveal the
right answer.

## Rule 2: decode-and-trust produces real wrong answers

The only repeatable *submitted wrong answers* come from decoding steps that can't be confirmed, where the wrong
reading still produces a well-formed answer:

| Task | Failure mechanism | Result |
|---|---|---|
| tb2-gcode-to-text | read letter `O` where the plotted glyph is digit `0` (`flag{gcOd3...}` vs `flag{gc0d3...}`), all three failures identical | 1 pass, 3 fail, 1 timeout |
| probe-conveyor-d (PNG, partial legend, 1 board) | square board read transposed: every D and R swapped (`RRRDRDDD` vs `DDDRDRRR`); one run missed one of two minimal sequences | 4/5 then 3/5 (7/10) |
| probe-conveyor-e (8 boards) | same, failures nearly all-or-nothing (one run failed all 8 boards, one failed 6) | 3/5 |
| probe-conveyor-c (PNG, full legend) | one decoding slip | 4/5 |
| probe-conveyor-a/b (text boards) | none | 5/5, 5/5 |

Ladder reading: text → image costs about 0.1–0.2 pass rate. A partial legend (tile looks inferred from a worked
example) costs about another 0.1–0.2. Eight boards instead of one add little, because a decoding mistake is
systematic.

## Rule 3: scale and search depth produce timeouts, not failures

Public "hard" tasks rarely produce wrong submissions. The agent keeps iterating until the hour runs out:

| Task | Result |
|---|---|
| tb2-circuit-fibsqrt | 1 pass, 4 timeouts |
| tb2-dna-assembly | 1 pass, 4 timeouts |
| tb2-raman-fitting | 1 pass, 4 timeouts |
| tb2-path-tracing-reverse | 5 timeouts |
| probe-synth-stack-v2 (longer programs) | 1 pass, 4 timeouts |
| visual-circuit-analysis-701 (GLM, 6–8 instances, deep search) | 1 fail, 4 timeouts |

Time limits matter as much as the tasks. The work trial's runner requires a 60-minute agent limit, while TB2's own
limits are shorter for most of these tasks:

| Task | TB2 limit | Our pass times | Passes within TB2's limit |
|---|---|---|---|
| model-extraction-relu-logits | 15 min | 13, 21, 28, 35, 38 min | 1/5 |
| write-compressor | 15 min | 26, 30, 49 min (+2 timeouts) | 0/5 |
| dna-assembly | 30 min | 45 min (+4 timeouts) | 0/5 |
| circuit-fibsqrt | 60 min | 29 min (+4 timeouts) | 1/5 |

Each of these ships its own checker: a query oracle, a decompressor, or a gate simulator. With the extra time, the
agent iterates until its answer verifies (Rule 1).

**Time box result:** an instruction to stop after about 15 minutes and submit the best answer was not obeyed. The
agents ran `date` 2–7 times and kept working past 35 minutes. One timeboxed dna-assembly run submitted a confidently
wrong answer at 39 minutes: it missed the stated 1-nucleotide clamp before the BsaI site. One timeboxed
circuit-fibsqrt run passed at 16 minutes.

**Design consequence:** difficulty must come from Rules 2 and 4, not from size. Every new task instruction ends
with a time box ("spend at most about 15 minutes ... save your best answer and finish"). That turns would-be
timeouts into submitted answers, which count. Timeboxed copies of dna-assembly and circuit-fibsqrt are measuring
how well the agent complies.

## Rule 4: explicit-spec bug fixing is a strength

About 48 GLM-5.1-authored bug-fix and implementation tasks scored 4–5/5, regardless of:
- code size (up to ~1000 lines across 8 modules)
- number of injected bugs (up to 12)
- stubbed core functions (up to 6)
- spec density (30+ numbered rules)

The agent writes one check per stated rule and iterates until its checks pass. The only near-band GLM results
came from counter-idiomatic rule interactions and ordered recovery sequences (streaming-protocol-50: 3 pass,
1 fail, 1 cancelled).

## Rule 5: rule out fake hardness before trusting a 0/5

| Task | Apparent hardness | Actual cause |
|---|---|---|
| visual-circuit-analysis-707-rail-routing | 0/5 | collinear overlapping edges in the render made the topology ambiguous |
| probe-beam-d3 | 1/5 | ambiguous detector-numbering wording (my probe) |

Guards now enforced for the image family (`generator/fast_author.py`):
- every tile type is drawn as one pixel-identical block, and different tiles never share a block
- the worked example contains every tile type
- image size equals columns × rows × CELL
- the boards come from one literal shared by the renderer and the hidden tests
- the board text appears in no file the agent can see
- every board is solvable with a 6–16 move minimum and 1–8 minimal sequences
- a task is accepted only at 1–3 passes, so at least one run reproduced the exact answers from the docs

## Rule 6: removing the free oracle is not enough while internals stay readable (game probe v2: 4/5)

Game v1 was easy because of Rule 1. In v2 the binary plays only its four built-in practice levels, so the rules
must be inferred from fixed boards. The hidden levels are chosen so that each plausible half-right rule model
loses on at least one of them, under every BFS move ordering:
- ice is floor
- a switch only fires when stepped on
- an open gate blocks
- a switch only opens gates

Each half-right model is also shown to be observably wrong on a practice level within a few moves, so every rule
can be discovered.

Result: 4 pass, 1 fail. Without custom boards, the agents disassembled the stripped AArch64 binary (objdump over
small address ranges), extracted the rules, and validated their solvers on the practice levels. One passing run's
model was still wrong: it treats a blocked slide as cancelling the whole move. No hidden level punished that, so
predicted half-right models don't cover every mistake an agent invents. Readable internals count as an
independent check under Rule 1.

## What the generator does now (prompt v028, family `grid-image-puzzle`)

GLM-5.1 authors an original single-player grid puzzle:
- **Mechanic:** 4–6 rules, at least two of which interact.
- **Boards:** 4–5 boards that exist only as PNGs, at least half of them square.
- **Legend:** a partial legend plus a non-square worked example (image and text) that contains every tile type.
- **Answer:** the minimum move count and all minimal move sequences per board.
- **Tests:** an exact BFS solver over the shared board literal.

The pipeline adds the time box, the placeholder answer file, and one starter gap per board. It fills reference
answers from the tests and runs the fairness checks above. Too-easy tasks (4–5/5) are dropped. Too-hard tasks
(0/5) get one deterministic back-off: the most-missed board is pre-filled.

## Rule 7: stated traps are caught, even the famous ones

"Easy task, one stated detail that autopilot misses" (2026-10-07):

| Source | Tasks | Result |
|---|---|---|
| Claude-authored probes | car-wash errand planner, Alice-in-Wonderland sibling perspective, round-half-away-from-zero invoices with truncated tax, logs with empty fields and natural sort, negative refund splits with truncation toward zero | 25/25 runs passed |
| GLM-5.1 trap tasks (prompts t001-t003) | half-open intervals, day-first dates, fenceposts, retention, word counts, self-inclusion counting, banker's-rounding traps | 18 tasks at 5/5; 2 at 4/5 with fair slips; 1 at 3/5 caused by an unstated edge case (Rule 9) |

These questions fool chat models: in one public comparison, 11 of 53 models passed the car-wash question. They don't
fool an agent that reasons at length, writes code, and tests it. Each stated detail is caught about 97% of the time.

## Rule 8: fair failures come from rushed runs

Every fair failure in the trap family was among the fastest runs on its task:
- enclosed-room-counter: 84 s, 4 turns; the run counted border-connected areas as rooms.
- shift-merge-halfopen: 201 s, 5 turns; the run merged touching half-open intervals.

The model occasionally answers an easy-looking task at once, without testing the stated edge cases. That gives a
per-detail slip rate of roughly 3-10% on easy tasks, and close to 0% on tasks that look hard. The latter get
careful, longer runs.

## Rule 9: underspecified edge cases cause real failures, and are unfair until made determinable

euro-expense-exclusive-range (GLM-5.1, prompt t002) scored 3/5. Both failures were on one hidden record with extra
semicolons (and an impossible date), while the instruction said each record had four fields. One run raised an
error and one skipped the record; the hidden test expected it to be accepted. All five runs handled the stated
traps.

This is the model choosing between two reasonable readings, so the task was held back. The repair keeps the edge
case but makes it determinable ("a free-text description"; valid dates) and re-probes the task. Family 7 (prompt
t004) generalizes the idea: the edge case is visible in the sample data, the instruction states only the general
fact, and the natural implementation is silently wrong on the hidden data.

## Rule 10: checklist breadth lands tasks in band; a GLM-5.1 fairness gate keeps them honest

Prompt t005 asks GLM-5.1 for one easy-looking task with 8-12 independent, trivial, plainly stated requirements, each
a measured slip type (rounding mode, inclusive bounds, day-first dates, dense vs competition ranking, separators,
plural wording, trailing newline). Results, 2026-10-07 13:50-14:10:

| Task | Result | Gate decision |
|---|---|---|
| leaderboard-checklist-format | 2/5 | accepted; every failure used competition ranking (1, 2, 2, 4) against the stated dense ranking |
| sales-leaderboard-checklist | 2/5 | accepted; failures put thousands separators on the Avg column, while the instruction scopes them to Total amounts |
| catalog-export-checklist | 3/5 | accepted; one failure wrote `Jan 1` against the stated `Mon DD`, the other counted products where the example counts quantity |
| contact-list-cleaner-breadth | 1/5 | held: whitespace-only categories and the summary's trailing newline were tested but unstated; clarified and re-probing |
| shift-roster-checklist-export | 2/5 | held: blank-line layout for an empty week and case-insensitive department matching were unstated; clarified and re-probing |

The gate is GLM-5.1 (README-compliant). Three independent audits each see:
- the instruction
- the complete assertion output of every failed run
- the failing hidden tests with their inputs
- the hidden reference implementation

Each audit must quote the instruction sentence that determines every expected output. Quotes are checked verbatim
(elided quotes fragment by fragment), every failed run must be covered, and a task ships only on a 2-of-3 fair
majority. Held tasks get the auditor's clarification appended and are re-probed.

Calibration against my manual audits: a single audit agreed on 5 of 7 cases and flipped between runs on 2. That is
why the gate uses a majority of three and coverage checks. `generator/gate_sweeper.py` applies the gate to every
learnable GLM task and to every completed in-band probe.

## Rule 11: easy to generate, hard to verify works (perception without a reference renderer)

probe-hardverify-label-codes (Claude-authored exploration probe, not shipped): six 8-character parcel codes are
rendered in a custom 5x7 bitmap font that is deleted after the build. The docs show only the letters A-Z, and the
codes mix in digits that resemble letters.

Result: 0 of 3 completed runs correct, plus 2 timeouts. Every wrong answer is a confident misreading:
- `LG8XI1KB` read as `LGBXI1KB` (8 read as B)
- `1EOILE0G` read as `IEOILE0G` (1 read as I)
- digits absent from the sample guessed wrong (3 read as 6 or 4, 5 read as 9)

This is the gcode O/0 mechanism reproduced on an original task. It is too hard when the sample shows letters only.
The dial is how much of the font the sample shows; family 9 (prompt t008) shows every symbol except two or three
look-alike digits.
