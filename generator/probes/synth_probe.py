"""Exploration probe (Claude-authored, NOT shipped): planted-solution program synthesis.

Easy to generate (run a random program forward), hard to solve (search for a program), cheap to verify
(run the agent's program). The agent must find ANY program of at most N instructions for an original
stack machine that maps every example input to its output. Difficulty knob: N.
"""
import inspect
import itertools
import json
import random
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

MOD = 997
OPS = ["DUP", "SWAP", "OVER", "DROP", "ROT", "ADD", "SUB", "MUL", "INC", "DEC", "NEG", "PUSH1", "PUSH2"]
OPS_V2 = OPS + ["PUSH3", "SQR", "HALF"]


def run(program, inputs):
    """Execute a program; return the top of the stack, or None on any error (underflow, empty, bad op)."""
    stack = list(inputs)
    for op in program:
        if op == "DUP":
            if not stack: return None
            stack.append(stack[-1])
        elif op == "SWAP":
            if len(stack) < 2: return None
            stack[-1], stack[-2] = stack[-2], stack[-1]
        elif op == "OVER":
            if len(stack) < 2: return None
            stack.append(stack[-2])
        elif op == "DROP":
            if not stack: return None
            stack.pop()
        elif op == "ROT":
            if len(stack) < 3: return None
            a = stack.pop(-3)
            stack.append(a)
        elif op in ("ADD", "SUB", "MUL"):
            if len(stack) < 2: return None
            b, a = stack.pop(), stack.pop()
            stack.append((a + b) % 997 if op == "ADD" else (a - b) % 997 if op == "SUB" else (a * b) % 997)
        elif op in ("INC", "DEC", "NEG"):
            if not stack: return None
            x = stack.pop()
            stack.append((x + 1) % 997 if op == "INC" else (x - 1) % 997 if op == "DEC" else (-x) % 997)
        elif op == "PUSH1":
            stack.append(1)
        elif op == "PUSH2":
            stack.append(2)
        elif op == "PUSH3":
            stack.append(3)
        elif op == "SQR":
            if not stack: return None
            x = stack.pop()
            stack.append((x * x) % 997)
        elif op == "HALF":
            if not stack: return None
            x = stack.pop()
            stack.append((x * 499) % 997)
        else:
            return None
    return stack[-1] if stack else None


def fits(program, examples):
    return all(run(program, ex["input"]) == ex["output"] for ex in examples)


def shortest_exists_upto(examples, limit, ops=None):
    for n in range(1, limit + 1):
        for prog in itertools.product(ops or OPS, repeat=n):
            if fits(prog, examples):
                return list(prog)
    return None


def make_problem(rng, n, n_examples=8, shortcut_limit=5, ops=None):
    ops = ops or OPS
    while True:
        prog = [rng.choice(ops) for _ in range(n)]
        inputs = [[rng.randint(2, 40) for _ in range(3)] for _ in range(n_examples)]
        outs = [run(prog, i) for i in inputs]
        if any(o is None for o in outs) or len(set(outs)) < n_examples - 1:
            continue
        # output must depend on every input position
        if any(len({run(prog, [x if k != pos else x + 1 for k, x in enumerate(i)]) for i in inputs[:1]} | {outs[0]}) < 2
               for pos in range(3)):
            continue
        examples = [{"input": i, "output": o} for i, o in zip(inputs, outs)]
        if shortest_exists_upto(examples, min(shortcut_limit, n - 2), ops):
            continue  # a much shorter program exists: too easy
        return prog, examples


SPEC = f"""# Stack machine

Programs are lists of instructions, one per line. Execution starts with the task's three input integers on
the stack, pushed in order (the third input is on top). After the last instruction the program's result is
the value on top of the stack. All arithmetic is modulo {MOD} (results are always in 0..{MOD - 1}).

| Instruction | Effect (top of stack is rightmost) |
|---|---|
| DUP | a -> a a |
| SWAP | a b -> b a |
| OVER | a b -> a b a |
| DROP | a -> (removed) |
| ROT | a b c -> b c a |
| ADD | a b -> (a+b) mod {MOD} |
| SUB | a b -> (a-b) mod {MOD} |
| MUL | a b -> (a*b) mod {MOD} |
| INC | a -> (a+1) mod {MOD} |
| DEC | a -> (a-1) mod {MOD} |
| NEG | a -> (-a) mod {MOD} |
| PUSH1 | -> 1 |
| PUSH2 | -> 2 |
| PUSH3 | -> 3 |
| SQR | a -> (a*a) mod {MOD} |
| HALF | a -> (a*499) mod {MOD}  (the modular half: 2*HALF(a) = a mod {MOD}) |

Any instruction that needs more values than the stack holds is an error; a program that errors, or ends with
an empty stack, produces no result. `/app/vm.py` is a reference interpreter (`python3 /app/vm.py PROGRAM_FILE
a b c`).
"""


def build(seed=31337, slug="probe-synth-stack", lengths=(6, 7, 8), ops=None):
    rng = random.Random(seed)
    problems = {}
    for k, n in enumerate(lengths, start=1):
        prog, examples = make_problem(rng, n, ops=ops, shortcut_limit=4 if ops else 5)
        problems[f"task{k}"] = {"max_len": n, "examples": examples, "planted": prog}
        print(f"task{k}: N={n} planted={' '.join(prog)}", flush=True)
    run_src = inspect.getsource(run)
    vm = ('"""Reference interpreter for the stack machine (see docs/MACHINE.md)."""\nimport sys\n\n' + run_src +
          '\n\nif __name__ == "__main__":\n    prog = [l.strip() for l in open(sys.argv[1]) if l.strip() and not l.startswith("#")]\n'
          '    print(run(prog, [int(x) for x in sys.argv[2:5]]))\n')
    files = {"vm.py": vm, "docs/MACHINE.md": SPEC}
    for name, p in problems.items():
        files[f"problems/{name}.json"] = json.dumps({"max_instructions": p["max_len"], "examples": p["examples"]}, indent=1) + "\n"
        files[f"solutions/{name}.txt"] = "\n".join(p["planted"]) + "\n"
    tests = textwrap.dedent(f'''
        import json
        from pathlib import Path
        PROBLEMS = {{k: {{"max_len": v["max_len"], "examples": v["examples"]}} for k, v in {problems!r}.items()}}
    ''') + "\n" + run_src + textwrap.dedent('''

        def _program(name):
            p = Path(f"/app/solutions/{name}.txt")
            assert p.exists(), f"missing /app/solutions/{name}.txt"
            return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.strip().startswith("#")]

        def _check(name):
            prog = _program(name)
            spec = PROBLEMS[name]
            assert prog, "empty program"
            assert len(prog) <= spec["max_len"], f"program has {len(prog)} instructions, limit {spec['max_len']}"
            for ex in spec["examples"]:
                got = run(prog, ex["input"])
                assert got == ex["output"], f"input {ex['input']}: got {got}, expected {ex['output']}"
    ''')
    for name in problems:
        tests += f"\n\ndef test_{name}():\n    _check(\"{name}\")\n"
    instruction = ("# Stack machine programs\n\nFor each problem in `/app/problems/` (task1..task3), write a program for the "
                   "stack machine described in `/app/docs/MACHINE.md` to `/app/solutions/<task>.txt` (one instruction per "
                   "line). A program must use at most the problem's `max_instructions` and must produce the expected output "
                   "for every example input. Any such program is accepted.\n")
    spec = {"slug": slug, "summary": "Synthesize short stack-machine programs that match input/output examples",
            "family": "exploration-probe", "instruction_md": instruction,
            "readme_md": "# Stack machine programs\n\nExploration probe: program synthesis with planted solutions (easy to generate, hard to solve, cheap to verify).\n",
            "reference_files": files, "fixtures": {}, "tests_py": tests,
            "bugs": [{"id": f"b{k}", "file": f"solutions/{name}.txt", "find": "\n".join(p["planted"]),
                      "replace": "# write your program here", "trap": "program must match every example within the limit",
                      "class": "planted program synthesis"} for k, (name, p) in enumerate(problems.items(), start=1)],
            "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield."}
    dest = fa.CANDIDATES / slug
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, slug)
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {b["id"]: res.get(f"bug-{b['id']}", {}).get("failed", []) for b in spec["bugs"]}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": slug, "valid": ok, "ref_failed": res["ref1"]["failed"], "starter_failed": res["starter"]["failed"]}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "v2":
        build(seed=4711, slug="probe-synth-stack-v2", lengths=(10, 11, 12), ops=OPS_V2)
    else:
        build()
