"""Amplify a validated candidate's hidden tests with GLM-5.1, keeping only tests the frozen reference passes.

The new tests target rule combinations, operation sequences, and boundaries. Every added test is run
against the reference in Docker; any test the reference fails is dropped, so oracle validity holds by
construction. The starter (bugs/stubs) is unchanged; the result is a new candidate `<slug>-amp`.
"""
import ast
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

PROMPT = """You strengthen the hidden test suite of a programming task. The application must satisfy the INSTRUCTION; \
the REFERENCE implementation is correct. Write 40-60 NEW pytest test functions (unique names, prefix test_amp_) \
that exercise behavior the instruction states explicitly, focusing on what the existing tests do NOT cover: \
combinations of two or more rules, multi-step sequences of operations, state carried across calls, boundary values, \
and inputs split or interrupted at every position. DO NOT compute expected values yourself: after obtaining each \
observable result (return value, stdout, exit code, file contents, raised error type/message) call \
`amp_check("<unique key>", value)` where value is JSON-serializable. Expected values are recorded automatically from \
the reference. `amp_check` is already defined; do not define it. Use the existing tests' imports/helpers (new helpers \
must be prefixed _amp_). Only exercise inputs whose behavior the instruction fully determines. Reply with ONE block only:
=== FILE tests/amplified.py ===
<python code: helper definitions and test functions only; the existing test module's imports and helpers are in scope>
=== END ==="""


def main(slug):
    src = fa.CANDIDATES / slug
    spec = json.loads((src / ".author.json").read_text())
    new_slug = f"{slug[:44]}-amp"
    message = (f"INSTRUCTION:\n{spec['instruction_md']}\n\nREFERENCE FILES:\n"
               + "\n".join(f"--- {k}\n{v}" for k, v in spec["reference_files"].items())
               + f"\n\nEXISTING TESTS:\n{spec['tests_py']}")
    text = fa.chat([{"role": "system", "content": PROMPT}, {"role": "user", "content": message}],
                   tag="author-amplify", raw_text=True)
    extra = next((body for kind, args, body in fa.parse_blocks(text) if kind == "FILE"), "")
    try:
        ast.parse(spec["tests_py"] + "\n" + extra)
    except SyntaxError as exc:
        raise SystemExit(f"amplified tests do not parse: {exc}")
    helper = (
        "\n\n# --- amplified edge-case tests (expected values recorded from the reference) ---\n"
        "import json as _amp_json, os as _amp_os\n"
        "_AMP_GOLDEN = __AMP_GOLDEN__\n"
        "def amp_check(key, value):\n"
        "    value = _amp_json.loads(_amp_json.dumps(value, default=str))\n"
        "    if _amp_os.environ.get('AMP_RECORD'):\n"
        "        path = '/amp/golden.json'\n"
        "        data = _amp_json.load(open(path)) if _amp_os.path.exists(path) else {}\n"
        "        data[key] = value\n"
        "        _amp_json.dump(data, open(path, 'w'))\n"
        "        return\n"
        "    assert key in _AMP_GOLDEN, f'no recorded expectation for {key}'\n"
        "    assert value == _AMP_GOLDEN[key], f'{key}: expected {_AMP_GOLDEN[key]!r}, got {value!r}'\n\n")
    spec = {**spec, "slug": new_slug, "tests_py": spec["tests_py"].rstrip() + helper + extra}
    active = set(spec.get("active_bugs") or [b["id"] for b in spec["bugs"]])
    bugs = [b for b in spec["bugs"] if b["id"] in active]
    dest = fa.CANDIDATES / new_slug
    if dest.exists():
        shutil.rmtree(dest)
    # Record golden values by running the amplified tests against the reference.
    import subprocess, tempfile
    spec["tests_py"] = spec["tests_py"].replace("__AMP_GOLDEN__", "{}")
    fa.write_task(spec, bugs, dest)
    image = f"tl-fast-{new_slug}"
    subprocess.run(["docker", "build", "-q", "-t", image, str(dest / "environment")], capture_output=True, timeout=900)
    golden = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copytree(dest / "tests", tmp / "tests")
        ref = tmp / "ref"
        for rel, content in spec["reference_files"].items():
            (ref / rel).parent.mkdir(parents=True, exist_ok=True)
            (ref / rel).write_text(content)
        (tmp / "amp").mkdir()
        subprocess.run(["docker", "run", "--rm", "--network", "none", "-e", "AMP_RECORD=1",
                        "-v", f"{tmp / 'tests'}:/tests:ro", "-v", f"{ref}:/overlay:ro", "-v", f"{tmp / 'amp'}:/amp",
                        image, "bash", "-c", "cp -r /overlay/. /app/ && cd /app && timeout 300 /opt/task-verifier/bin/python -m pytest /tests -q -k test_amp_ -p no:cacheprovider >/dev/null 2>&1; chmod 666 /amp/* 2>/dev/null; true"],
                       capture_output=True, timeout=400)
        if (tmp / "amp" / "golden.json").exists():
            golden = json.loads((tmp / "amp" / "golden.json").read_text())
    spec["tests_py"] = spec["tests_py"].replace("_AMP_GOLDEN = {}", "_AMP_GOLDEN = " + repr(golden), 1)
    fa.write_task(spec, bugs, dest)
    # Keep only amplified tests that the reference passes (twice, for determinism).
    for _ in range(2):
        res = fa.docker_variants(dest, {"ref": spec["reference_files"]}, new_slug)["ref"]
        bad = {name for name in res["failed"] if name.startswith("test_amp_")}
        if not bad:
            break
        tree = ast.parse(spec["tests_py"])
        lines = spec["tests_py"].splitlines(keepends=True)
        for node in sorted((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in bad),
                           key=lambda n: -n.lineno):
            start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
            del lines[start:node.end_lineno]
        spec["tests_py"] = "".join(lines)
        fa.write_task(spec, bugs, dest)
    checks = fa.validate(spec, bugs, dest, new_slug)
    ok = all(checks[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not checks["starter"]["ok"]
    spec["bug_tests"] = {b["id"]: checks.get(f"bug-{b['id']}", {}).get("failed", []) for b in bugs}
    fa.write_task(spec, bugs, dest)
    added = sum(1 for n in ast.parse(spec["tests_py"]).body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_amp_"))
    print(json.dumps({"slug": new_slug, "valid": ok, "tests_total": checks["ref1"]["passed"], "amplified_kept": added,
                      "golden_values": len(golden), "starter_fails": len(checks["starter"]["failed"])}))


if __name__ == "__main__":
    main(sys.argv[1])
