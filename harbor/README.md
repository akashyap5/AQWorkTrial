# Harbor (vendored source, read-only)

A copy of the [Harbor](https://github.com/harbor-framework/harbor) source, here
so you can read how a task is defined, run, and graded without installing
anything. It's for reading only — install the real framework with
`pip install harbor` (or `uv tool install harbor`). Some imports here point at
modules that aren't vendored, so don't `pip install -e harbor/`.

## Where to look

- `src/harbor/models/task/config.py` — the `task.toml` schema (`TaskConfig`). The validator mirrors this.
- `src/harbor/models/task/task.py`, `paths.py` — the directory layout Harbor expects, and the canary-stripping rule.
- `src/harbor/models/difficulty.py` — the allowed `difficulty` values.
- `src/harbor/agents/oracle.py` — the Oracle agent: copies `solution/` into the container and runs `solve.sh`.
- `src/harbor/agents/{base,nop,factory}.py` — the agent interface and how agents are selected.
- `src/harbor/verifier/verifier.py` — how the reward is read back from `/logs/verifier/reward.txt`.
- `src/harbor/cli/template-task/` — the scaffold `harbor init` writes; the shape a generated task should take.

Upstream: <https://github.com/harbor-framework/harbor> · Docs: <https://harborframework.com/docs>
