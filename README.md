# AfterQuery Platform Engineer Work Trial: Task Generation Pipeline

## Context

Terminal-Bench 2 evaluates AI agents on coding tasks. An agent gets an instruction, works in a terminal, and a verifier decides whether it solved the problem. You've seen how this works from the take-home, which used the similar Terminal-Bench 1.

Now build the other side: **a pipeline that generates the tasks themselves.**

A generated task is only useful if it sits in the right difficulty band. Too easy and every model solves it. Too hard and none do. The sweet spot is a **learnable** task: when run 5 times with `terminus-2` using `glm-5.3-flash` at `reasoning_effort=high`, exactly 1 to 3 out of 5 runs pass all tests. Generate tasks with `glm-5.1` and benchmark difficulty only against `glm-5.3-flash`. Use no other models for either, and keep the evaluator's reasoning effort pinned at `high` so every band measurement is comparable. Your pipeline should produce tasks that consistently land in this range.

## What's Provided

- `generator/generate.py`: a task generator stub, currently incomplete.
- `validator/validate.py`: a structural validator that checks the task layout and that `task.toml` parses and validates against Harbor's schema.
- `examples/`: 6 reference tasks in the standard Harbor format. Study these carefully, though they likely aren't in the target difficulty band themselves.
- `harbor/`: the Harbor framework source. This is the harness that runs agents against tasks. Read it to understand how it works: the `task.toml` schema, the agents, the verifier. Install the framework itself with `pip install harbor`.
- `scripts/`: helper scripts for generation and validation.

This is a template, feel free to change any of it as you see fit, but try to maintain the general structure.

### Task Format

Each task directory contains:

```
task-name/
├── task.toml            # Task config: difficulty, timeouts, metadata
├── instruction.md       # The instruction the agent sees
├── environment/
│   ├── Dockerfile       # Container environment setup
│   └── [task files]     # Anything copied into the container
├── solution/
│   └── solve.sh         # Deterministic reference solution
└── tests/
    ├── test.sh          # Runs the checks, writes the reward
    └── test_outputs.py  # Pytest tests that verify the task was solved
```

`tests/test.sh` writes `1` (pass) or `0` (fail) to `/logs/verifier/reward.txt`. Your generated tasks must follow this format exactly.

### What Makes a Good Task

A well-crafted task has:

- A clear, unambiguous instruction that describes what needs to be fixed or built
- Tests that pass when the task is correctly solved. 
- Tests that fail on an unsolved container (if the tests pass without anyone doing anything, the task is broken). 
- A Dockerfile that installs all necessary dependencies
- Appropriate difficulty. Not trivially solvable, not impossibly hard
- Important -- we want the tasks to be useful for model training, that is our core business
  - Think about what quality means in a task, we want the pipeline to not only make tasks, but also quality ones. 
  - Whenever making the tasks, make sure that they are diverse. A task set that are too similiar to eachother (up to you to interpert what that means), is not as useful for model training than a diverse set.



## Requirements



### Part 1: Generate Valid Tasks

Implement the generator so that generated tasks:

1. Pass the structural validator
2. Are functionally correct: the reference solution passes all tests (oracle), and an unsolved container fails (nop)

Verify by running the structural validator and by checking generated tasks against the examples. Note: passing only the structural validator does not necessarily mean it's a quality task. 

### Part 2: Hit the Target Distribution

This is the core deliverable.

A task is learnable if, when run 5 times with `terminus-2` using `glm-5.3-flash` (`reasoning_effort=high`), exactly 1 to 3 runs pass all tests. Tasks where 0 runs pass are too hard. Tasks where 4-5 runs pass are too easy.

Build a pipeline that produces learnable tasks. Your system should:

- Generate a task
- Evaluate it (run it against the agent)
- Report the pass distribution
- Only output tasks that land in the 1-3 out of 5 range



### Part 3: Pipeline Metrics

Generate a batch of tasks (aim for 10+). Report:

- How many pass structural validation
- How many are functionally correct
- How many land in the 1-3/5 learnable range
- Total cost and time (do not index on cost too much, if you run out of API credits, please ask us to top it up with more credits, but keep in mind that this has to be reasonable). i.e it can't cost $200+ to create 1 task
- We want to see a yield rate here on a batch of 10 tasks and see how many land in the learnable range



### Part 4: Make It Robust

Do this after Parts 1-3. If your batch isn't where you want it yet, keep working on that instead.

Your pipeline should:

- Keep a record of everything it did, so we can check what you shipped and why each task got its verdict without reading your code or taking your word for it
- Pick up where it left off if it gets killed mid-batch, without redoing (and re-paying for) runs that already finished
- Not break if two copies of it are running at the same time

We will test all three.

Parts 1-3 matter more than this. A good batch with a weak Part 4 beats a weak batch with a good Part 4. A mediocre job on both is the worst outcome. If Parts 1-3 aren't coming together, a great Part 4 with a weak batch still counts for a lot.

### Stretch Goals

If you keep going (and are 100% confident in the last 4 parts), do these in order if you have time (only if Parts 1-4 are done and you have time left):

A. Difficulty tuning. Given a task that's too easy or too hard, can your pipeline adjust it to land in the target band?

B. Human-likeness. Compare your generated tasks side-by-side with the hand-crafted examples in `examples/`. How close are they on instruction clarity, test thoroughness, Dockerfile quality, and overall polish? Can your pipeline produce tasks indistinguishable from human-written ones?

## Caveats

- A failing run should mean the model failed, not the task. What counts as the model failing is up to your judgment, but for every task you ship, know why it fails. Timeouts don't count as the model failing
- Tasks must be novel. Don't reuse or adapt any public task, including the examples here. How the pipeline generates tasks at the top level can come from inspiration on how public tasks are made, but should not be the model going through public tasks and running an autonomous pipeline on making tasks similiar to them. 
- Make tasks worth training on. Aim for tasks where a scaled-up version would plausibly improve a model trained on it with RL or SFT. Reward hacking is something you should keep in mind too here.
- Your API key starts with 500 dollars on it. That is a starting budget, not a ceiling: we don't expect you to fit everything into it, and if you run low, ask and we will top it up. Do not overindex on cost here, but keep it in a reasonable range.



## Setup



### Prerequisites

- Docker (running)
- Python 3.12+
- `uv` (recommended) or `pip`



### Install Harbor

```bash
uv tool install harbor
```

Or with pip:

```bash
pip install harbor
```

Verify: `harbor --help`

### Install Generator Dependencies

```bash
pip install -r generator/requirements.txt
```



### Environment

You'll be given an inference API key. Harbor calls models through LiteLLM, which routes any `openrouter/...` model through `OPENROUTER_API_BASE` using `OPENROUTER_API_KEY`. Point both at the inference gateway:

```bash
export OPENROUTER_API_KEY="<your inference key>"
export OPENROUTER_API_BASE="https://api.aqinference.com/v1"
```

The generator reads the same two variables.

### Running the Generator

```bash
bash scripts/generate.sh "fix a broken Python script"
```



### Running the Validator

```bash
bash scripts/validate.sh output/fix-a-broken-python-script
```



### Evaluating a Task

```bash
harbor run -p output/<task> -a oracle    # reward 1 = the reference solution passes
harbor run -p output/<task> -a nop       # reward 0 = an unsolved container fails
harbor run -p output/<task> -a terminus-2 -m openrouter/z-ai/glm-5.3-flash --ak reasoning_effort=high --n-attempts 5
```



## Evaluation Criteria

In order of importance:

1. Do your tasks hit the target distribution? What fraction of generated tasks are learnable (1-3 out of 5)? Do these have valid failure modes (reason why the agent failed and your task landed in the learnable band)?
2. Pipeline design. How did you decompose generation, validation, and evaluation?
3. Robustness. Can we check what you shipped from your records alone? Does the pipeline survive being killed and restarted, or run twice at once?
4. How do you know your tasks are good? What's your validation and evaluation strategy?
5. Reliability and throughput. Can you generate calibrated tasks consistently?
6. Code quality. Clean, readable, well-structured.

Include:

- All source code
- `CHANGELOG.md` describing what you did, why, and any design tradeoffs
- Your git commit history (we read it)
