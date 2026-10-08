# Task Lab implementation notes

The original work-trial requirements are preserved in [README.md](README.md).

## Batch generator: records, resume, and concurrent runs (Part 4)

`generator/fast_author.py` is the batch pipeline that produced the shipped tasks. GLM-5.1 authors each task. The
pipeline validates it in Docker, probes it with 5 GLM-5.3-Flash/high runs on the Task Lab worker (`scripts/dev.sh`),
and accepts it into `output/learnable/` only at 1-3 passes out of 5 completed runs, with the oracle passing and
no-op failing.

```bash
export OPENROUTER_API_BASE=https://openrouter.ai/api/v1   # OPENROUTER_API_KEY must already be set
python3 generator/fast_author.py --families everyday-trap --count 6 --parallel 6 --max-edits 1
```

**Records.** Everything is checkable without reading code:

| File | Contents |
|---|---|
| `output/ledger.jsonl` | Append-only, one JSON event per line: batch starts, slot stage changes, authoring, every validation attempt and repair, probe jobs, verdicts, strategy-library updates |
| `output/candidates/<slug>/verdict.json` | Decision and reason; prompt version and levers used; every probe job with per-run status, duration, and failing tests; for failed runs, the wrong answers next to the expected ones |
| `output/learnable/<slug>/` | Shipped tasks; `task.toml` records `measured_passes` and `probe_job`, plus the same `verdict.json` |
| `output/batches/<batch-id>/state.json` | Each slot's stage (`pending` → `authored` → `valid` → `probing` with job id → `done` with outcome) |
| `prompts/*_versions/` | Every prompt version, what changed, and why |
| `prompts/fast_author_changelog.md` | Why each version changed |
| `prompts/strategies.json`, `prompts/strategy_versions/` | The hard/easy lever library, with per-lever measured pass counts and GLM-5.1 failure notes |
| `reports/failure_rules.md` | Measured rules about what makes the solver fail |

**Resume after a kill.** Every batch prints its id. Restart with:

```bash
python3 generator/fast_author.py --resume latest
```

`--resume` also accepts a batch id. Finished slots are skipped. A slot with a probe job re-attaches to that job,
whether it is still running or already finished, so no finished run is repeated or re-paid. Authored tasks are
re-validated from their saved spec without a new authoring call. If the Task Lab worker itself was killed, only the
runs it left `interrupted` are re-queued inside the same job.

**Two copies at once.** Two copies can run at the same time, fresh or both resuming the same batch:
- Task directories are reserved with an atomic `mkdir`, so two copies never share a slug.
- Each slot runs under a non-blocking `flock`; a slot held by another copy is skipped.
- The ledger, batch state, prompt registry, stats, strategy library, and acceptance into `output/learnable/` are
  written under file locks with atomic replaces.

**Testing all three without spending.** `FAST_AUTHOR_FAKE=1` swaps the paid and slow steps for stand-ins: canned
authoring output, no Docker validation, and simulated probe jobs. All output goes under `output/fake/`.

```bash
rm -rf output/fake
FAST_AUTHOR_FAKE=1 FAST_AUTHOR_FAKE_PROBE_SEC=20 python3 generator/fast_author.py --families grid-image-puzzle --count 4 --parallel 4 --max-edits 0 &
sleep 9; kill -9 %1                                   # kill mid-probe
FAST_AUTHOR_FAKE=1 python3 generator/fast_author.py --resume latest &   # two copies resume the same batch
FAST_AUTHOR_FAKE=1 python3 generator/fast_author.py --resume latest
ls output/fake/.fast-author/fake_jobs | wc -l          # 4: no probe was re-run
```

Measured on 2026-10-07:
- **Kill and resume:** the resumed batch re-attached to all 4 probe jobs, with no re-authoring.
- **Two fresh batches at once:** 8 unique task directories and no corrupt ledger lines.
- **Two copies resuming one batch:** one copy processed all 4 slots and the other skipped them as claimed.

## Local Task Lab

The local UI lets you select any of the six supplied examples, run oracle and
nop controls, and then watch five independent Terminus-2 attempts. It reuses the
earlier take-home's Task Lab design, with live turn details, verifier results,
logs, cancellation, and saved job history. Up to **five Docker trials** run at
once. Each trial gets a fresh container; examples are built from their supplied
Dockerfiles with Harbor's `--force-build`.

```bash
# Use an installed Python 3.12+ (the macOS system python3 may be older).
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Keep the key in the shell, never in committed files.
# Set OPENROUTER_API_KEY before launching.
# Use this endpoint for an OpenRouter-issued key:
export OPENROUTER_API_BASE="https://openrouter.ai/api/v1"
# For an AfterQuery gateway-issued key, use https://api.aqinference.com/v1 instead.
bash scripts/dev.sh
```

Open <http://127.0.0.1:8000>. **Controls only** incurs no model calls. **Run checks
+ 5 attempts** first requires oracle reward 1 and nop reward 0, with the same
nonempty test set and no verifier errors. The model is fixed to
`openrouter/z-ai/glm-5.3-flash` with `reasoning_effort=high` and the task's original
agent timeout; there is no extra turn cap. Timeouts and execution errors are
displayed separately and cannot qualify a task for the 1–3/5 band.

Job snapshots, hashes, state, Harbor configuration, trajectories, test reports,
and logs are stored in `.tasklab/jobs/` (override with `TASKLAB_DATA`). A separate
worker owns runs, so reloading the UI does not interrupt them. On restart it
reconciles existing results and live Harbor processes. Ambiguous interrupted
attempts are not automatically retried or counted as model failures. A worker
lock prevents two workers from consuming the same local queue. Jobs wait safely
when Docker is offline.

The generator stub is also implemented as a baseline authoring CLI:

```bash
bash scripts/generate.sh "an original task topic"
bash scripts/validate.sh output/<generated-task>
# With Task Lab's worker running, queue any local Harbor task:
bash scripts/evaluate.sh output/<generated-task> --controls-only --wait
bash scripts/evaluate.sh examples/openssl-selfsigned-cert --wait
.venv/bin/python -m pytest -q
```

Generation uses only GLM-5.1, validates file paths and structure, and records
requests, responses, usage, and file hashes under `output/.generation-runs/`.
`structurally_validated` does **not** imply oracle/nop success or learnability;
those require execution. Authoring includes at most two structural repair
requests. The phase-two batch loop below adds semantic review, measured feedback,
and prompt revisions. Clustering is outside this demo's scope.

The `fix-git` example's upstream repository is no longer publicly cloneable.
Its Dockerfile now copies the exact original Git fixture, including detached
commit and reflog, from the supplied image pinned by digest into a native image.
The example's instructions, verifier, and reference solution are unchanged.

The [first-stage validation report](reports/phase-1-validation.json) records
successful oracle/nop checks for all six examples and 5/5 solver passes each for
OpenSSL, log-summary, and fix-git. At the user's smoke-test checkpoint, remaining
model attempts were cancelled and excluded from solve rates. The generated
inventory-diff smoke task passes its controls but retains a `needs_review`
semantic audit; it is not classified as training-ready or learnable. It is not used as a generation seed.

## Phase two: bounded prompt evolution

The **Phase two** panel runs one explicitly requested batch of three original
Python CLI tasks. It freezes the active generation prompt for that batch, checks
structure and syntax, asks GLM-5.1 to audit the public contract against tests and
solution, and permits at most two evidence-driven repairs per task. Passing tasks
then receive oracle/nop checks and five GLM-5.3-Flash/high solver attempts, with
five Docker trials at a time. Public examples are not supplied to the author.

The demo profile allows **five minutes of agent execution and $0.05 per solver
attempt**. Queueing, image builds, and verification are outside the agent timer.
A local metered gateway reserves each inference request before forwarding it,
constrains provider prices/output tokens, and includes BYOK upstream charges.
Unknown charges retain their reservation; ambiguous interrupted requests are not
automatically replayed. Time/cost exhaustion is `budget_exhausted`, not a verified
solver failure. Even five completed demo runs only produce an `observed_demo_band`;
canonical learnability remains unset because this profile adds budget constraints.
The original specification explicitly excludes timeouts from model failures.
For example, two passes and three timeouts are inconclusive, not a learnable
2/5 result. Final classification requires five completed, valid outcomes with
actual test failures explaining the unsuccessful attempts. Operational deadlines
can stop expensive work without turning those stops into difficulty evidence.

Allowances in `phase2/config.toml` total at most **$3 per batch**: $0.50 authoring
and repairs, $0.166666 semantic review, and $0.25 solver attempts per task, plus
$0.25 for the shared prompt update. Configuration must agree with the runner's
fixed demo limits. This budgeted profile requires the direct OpenRouter endpoint.

After the batch, GLM-5.1 receives measured results, review defects, selected solver
turns, and recent prompt history. It proposes a complete replacement prompt or
keeps the incumbent. The immutable starting instructions live in
`prompts/starter_prompt.txt`; the fixed optimizer lives in `prompts/update_prompt.txt`.
Versioned text, hashes, evidence, and an atomic current-version pointer live in
`.tasklab/prompts/`. Each new generation reads that pointer afresh; an explicit
`--prompt-version` can pin the standalone generator. The UI shows the current
prompt, candidate status, feedback, and before/after diff.

Code-accessible [prompt history](prompts/history/README.md) contains recovered
versions `v0001`–`v0003`, full text, diffs, and the rejected proposal's evidence.
The live append-only journal at `.tasklab/prompts/history.jsonl` is flushed to
disk before every prompt creation, activation, rollback, or status change.
Read it with `.venv/bin/python -m phase2.prompts history` or
`from phase2.prompts import history; history()`.
Refresh the portable repository snapshot with
`.venv/bin/python -m phase2.prompts export --output prompts/history`.
Recovered snapshots preserve known timestamps and explicitly leave missing
historical activation timing unknown; they do not change the active prompt.

**Every manually started demo batch stops at a review checkpoint.** A proposed revision becomes the
current candidate for the next explicitly started batch. It is not promoted to
validated or claimed to be better without fresh comparative evidence. Automatic
candidate promotion, clustering, and strategy-prompt evolution are deferred.
Reloading the UI never starts a batch. Batch state and author/reviewer/update
request evidence are retained under `.tasklab/phase2/batches/`.
On macOS the generation worker prevents idle sleep while a batch is active and
releases that assertion at the checkpoint. Explicit system sleep can still
interrupt in-flight requests; interrupted measurements remain inconclusive.

`scripts/dev.sh` starts the API, trial runner, generation worker, and search worker. The same
flow is available from the CLI while those workers are running:

```bash
.venv/bin/python -m phase2.controller --start
.venv/bin/python -m phase2.controller  # inspect status without starting work
```

The [first phase-two checkpoint](reports/phase-2-checkpoint.json) records three
original tasks and one semantic repair. All oracle/nop checks passed; two tasks
scored 5/5 in the demo and the third exhausted its time budgets, with host sleep
confounding its first three attempts. GLM's first proposal was rejected for
misreading those timeouts as difficulty evidence; corrected candidate `v0003`
was current and awaited fresh-task validation at that checkpoint. Total accounted
inference was about $0.48, including $0.084 in unresolved reservations. The next
batch remained stopped until the subsequent search was authorized.

## Sustained search for ten learnable tasks

The search targets **ten distinct, audited learnable tasks** rather than stopping
after each demo batch. Start the workers with `bash scripts/dev.sh`, then use the
Task collection panel or these commands in another terminal with the same
OpenRouter environment:

```bash
export OPENROUTER_API_BASE="https://openrouter.ai/api/v1"
.venv/bin/python -m phase2.search --start
.venv/bin/python -m phase2.search         # inspect without starting work
.venv/bin/python -m phase2.search --stop  # stop the current search and active batch
```

Each round generates three fresh tasks with GLM-5.1 in concurrent author/review
pipelines, overlapping queued Docker evaluation. Author requests prefer
high-throughput providers. Task generation, semantic reviews, failure audits, and
prompt optimization disable optional reasoning for throughput after observed
reasoning-only responses exhausted their allowances without usable output.
The GLM-5.3-Flash solver remains fixed at `reasoning_effort=high`; every request
records its actual parameters and cost. Valid tasks receive oracle
and nop checks, then five independent GLM-5.3-Flash/high attempts, with at most
five Docker trials running across the application. The search profile permits
**600 seconds of agent execution and $0.20 inference per solver attempt**;
setup, queueing, and verification are outside that timer. Time/cost stops are
inconclusive and never count as solver failures. A task is accepted only after
five valid outcomes with 1–3 passes, passing controls, semantic and diversity
checks, and an evidence-based GLM-5.1 audit of the unsuccessful solver attempts.
Every generated task has a descriptive name and a plain-language `README.md`,
expandable from its task card in the existing UI.

Generation rotates among eight families: storage recovery, dependency planning,
streaming protocols, filesystem migration, relational transformations, message
delivery, resource scheduling, and configuration compilation. The author and
reviewer receive a compact archive of our generated tasks and their solution
patterns, never the public examples as seeds. Conservative instruction/code/test
fingerprints reject strong duplicates before solver evaluation, and semantic
review checks for repeated solution structures. The exported collection requires
at least four families and allows at most three accepted tasks per family.

GLM-5.1 first revises the demo prompt for the broader search scope. Each round
freezes its active prompt version; later generation automatically reads the
current registry pointer. A prompt that produces audited learnable tasks is
retained for fresh tasks in other families. After a round without accepted tasks,
the optimizer uses observed yield and failure evidence to revise the best
empirically observed prompt. At a checkpoint, the best prompt with accepted tasks
is restored as the active version. These are small-sample measurements, not proof
that a prompt is universally best; every proposal and activation remains in the
prompt history.

The defaults in `phase2/config.toml` stop for review after **three consecutive
rounds without an accepted task**, twelve rounds, or insufficient remaining
allowance under the $50 search cap. Authoring/infrastructure errors or an
unusable prompt update also stop work for inspection. Reloading a page never
starts a search, and ambiguous paid requests are not automatically replayed.

Accepted task snapshots are copied unchanged into
`output/learnable/<search-id>/<readable-task-name>-<job-id>/`. State and provider
evidence remain under `.tasklab/search/`, `.tasklab/phase2/batches/`, and
`.tasklab/jobs/`; the portable report is `reports/search-<search-id>.json`.
Reports include prompt scores, costs, failure audits, family coverage, all
candidate outcomes, and a separate first-ten-candidate view. Reaching the target
means **10 accepted tasks out of N candidate slots**, not a claimed 10/10 yield:
invalid, duplicate, failed, and inconclusive candidates remain in the denominator,
with generation/repair attempts reported separately. This is an adaptive
collection; a future frozen-prompt confirmation batch would be needed to estimate
independent yield. Ten accepted tasks remain the execution goal, not a result
implied by implementing the search.

## Search checkpoint: reviewer allowance

The first search (`9e52229dd22c4fbe`) stopped with three structurally validated drafts and zero accepted tasks. GLM authored prompt `v0004`; after reasoning-only task-generation truncations, non-thinking authoring produced usable bundles. All three subsequent semantic reviews exhausted their 8,192-token response allowance (8,190 reasoning tokens) without returning a verdict, so oracle/nop and solver runs were not started. The [checkpoint report](reports/search-9e52229dd22c4fbe.json) retains all attempts and costs, including the interrupted request reservation. Reviews now disable optional reasoning and allow 32,768 output tokens; a larger retry is permitted only after a confirmed, accounted truncation and within the remaining budget.

## Repair and review evidence

Repairs reuse the last structurally valid task bundle even when an intervening
authoring attempt returned malformed output. Repair input excludes framework-owned
files such as `task.toml` and `tests/test.sh`, and strips only the exact appended
verifier setup and inserted canary lines; the author's original files and Docker
instructions remain visible. Semantic review receives execution feedback only
when its task hash matches the revision under review, so an old oracle failure
cannot describe newly repaired files as still broken. Task cards show the current
attempt's semantic verdict, or pending/in-progress status during a new review;
earlier verdicts remain in authoring history. Cancelled, interrupted, or incomplete
calls without confirmed usage retain conservative cost reservations and do not
count as solver failures. These infrastructure fixes do not themselves establish
task validity or learnability.
