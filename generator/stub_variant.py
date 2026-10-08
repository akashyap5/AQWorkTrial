"""Create an implement-from-spec variant of a validated candidate by stubbing its largest functions.

The reference and tests are unchanged; each stub is a find/replace "bug" whose replacement is the
function's docstring plus `raise NotImplementedError`, so the existing calibration loop (including
restoring stubs when a task is too hard) applies unchanged.
"""
import ast
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402


def stub_bugs(spec, count):
    candidates = []
    for rel, source in spec["reference_files"].items():
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        lines = source.splitlines(keepends=True)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("__"):
                continue
            body = node.body
            doc = ast.get_docstring(node)
            start = body[1].lineno if doc and len(body) > 1 else body[0].lineno
            if doc and len(body) == 1:
                continue
            end = node.end_lineno
            text = "".join(lines[start - 1:end])
            if end - start < 4 or source.count(text) != 1:
                continue
            indent = " " * (len(lines[start - 1]) - len(lines[start - 1].lstrip()))
            replace = f"{indent}raise NotImplementedError(\"{node.name}: implement according to the instruction\")\n"
            candidates.append((end - start, {"id": f"s-{node.name}"[:40], "file": rel, "find": text.rstrip("\n"),
                                             "replace": replace.rstrip("\n"), "class": "stubbed function",
                                             "trap": f"{node.name} must be implemented from the instruction"}))
    candidates.sort(key=lambda c: -c[0])
    return [b for _, b in candidates[: count * 3]]


def main(slug, count=3, suffix="impl"):
    src = fa.CANDIDATES / slug
    spec = json.loads((src / ".author.json").read_text())
    new_slug = f"{slug[:40]}-{suffix}"
    spec = {**spec, "slug": new_slug}
    active = set(spec.get("active_bugs") or [b["id"] for b in spec["bugs"]])
    base = [b for b in spec["bugs"] if b["id"] in active]
    dest = fa.CANDIDATES / new_slug
    if dest.exists():
        shutil.rmtree(dest)
    chosen = []
    for bug in stub_bugs(spec, count):
        stubs = chosen + [bug]
        trial = [b for b in base if all(b["file"] != c["file"] or (b["find"] not in c["find"] and c["find"] not in b["find"]
                                                                    and not set(b["find"].splitlines()) & set(c["find"].splitlines()))
                                        for c in stubs)] + stubs
        try:
            fa.apply_bugs(spec["reference_files"], trial)
        except ValueError:
            continue
        fa.write_task(spec, trial, dest)
        check = fa.docker_variants(dest, {"one": fa.apply_bugs(spec["reference_files"], [bug])}, new_slug)
        if check.get("one", {}).get("ok", True) or not check["one"].get("failed"):
            continue
        spec.setdefault("bug_tests", {})[bug["id"]] = check["one"]["failed"]
        chosen.append(bug)
        if len(chosen) >= count:
            break
    bugs = [b for b in base if all(b["file"] != c["file"] or (b["find"] not in c["find"] and c["find"] not in b["find"]
                                                              and not set(b["find"].splitlines()) & set(c["find"].splitlines()))
                                   for c in chosen)] + chosen
    spec["bugs"] = bugs
    spec["instruction_md"] = spec["instruction_md"].rstrip() + (
        "\n\nSome functions in the application are not implemented yet and raise `NotImplementedError`; "
        "implement them so the whole application satisfies this instruction.\n")
    fa.write_task(spec, bugs, dest)
    print(json.dumps({"slug": new_slug, "stubs": [c["id"] for c in chosen], "bugs": len(bugs)}))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3, sys.argv[3] if len(sys.argv) > 3 else "impl")
