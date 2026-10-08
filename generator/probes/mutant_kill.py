"""Exploration probe (Claude-authored, NOT shipped): write a test suite that kills hidden mutants.

The agent receives a CORRECT implementation and its specification and must write
/app/tests/test_fees.py. Hidden grading: the suite must pass against the correct module and fail
against each of eight hidden mutants (subtly wrong variants). This targets the solver's measured
weakness: it declares success after thin self-checks.
"""
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

SPEC = """# Parking fee rules (authoritative)

`fee(entry, exit)` returns the fee in cents for one stay. `entry` and `exit` are naive `datetime` values
(local time, minute precision) with `exit >= entry`.

1. **Grace period.** Stays of 15 minutes or less (exit - entry <= 15 min) are free (fee 0).
2. **Billable blocks.** Otherwise the stay is billed in 15-minute blocks, rounding UP: a 16-minute stay is
   2 blocks, a 30-minute stay is 2 blocks, a 31-minute stay is 3 blocks. The grace period is NOT deducted.
3. **Block price.** Each block costs 125 cents.
4. **Daily cap.** Split the stay at each midnight into calendar-day segments. Each segment's blocks are counted
   separately (each segment rounds up on its own), priced at 125 cents per block, and each segment is capped at
   2400 cents.
5. **Overnight flat rate.** If a stay starts at or after 22:00 and ends at or before 06:00 the next morning,
   the fee is a flat 900 cents instead of rules 2-4 (still subject to rule 1). A stay starting exactly at 22:00
   qualifies; a stay ending exactly at 06:00 qualifies; ending at 06:01 does not.
6. **Weekend multiplier.** Segments whose calendar day is Saturday or Sunday are multiplied by 1.5 AFTER the
   daily cap is applied (so a weekend segment can cost up to 3600). Multiply then round half up to a whole cent.
   The overnight flat rate (rule 5) is never multiplied.
7. The total fee is the sum of the segment fees.
"""

CORRECT = '''"""Parking fee calculator. See docs/RULES.md (authoritative)."""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

BLOCK = 125
CAP = 2400
OVERNIGHT = 900


def _blocks(minutes):
    return -(-minutes // 15)


def _segments(entry, exit):
    cur = entry
    while cur < exit:
        midnight = datetime(cur.year, cur.month, cur.day) + timedelta(days=1)
        end = min(midnight, exit)
        yield cur, end
        cur = end


def _is_overnight(entry, exit):
    if entry.hour < 22:
        return False
    morning = datetime(entry.year, entry.month, entry.day, 6, 0) + timedelta(days=1)
    return exit <= morning


def fee(entry, exit):
    minutes = int((exit - entry).total_seconds() // 60)
    if minutes <= 15:
        return 0
    if _is_overnight(entry, exit):
        return OVERNIGHT
    total = 0
    for start, end in _segments(entry, exit):
        seg_minutes = int((end - start).total_seconds() // 60)
        amount = min(_blocks(seg_minutes) * BLOCK, CAP)
        if start.weekday() >= 5:
            amount = int((Decimal(amount) * Decimal("1.5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        total += amount
    return total
'''

# Each mutant: (find, replace, description). Applied one at a time to CORRECT.
MUTANTS = [
    ("    if minutes <= 15:\n        return 0", "    if minutes < 15:\n        return 0", "grace boundary exclusive"),
    ("    return -(-minutes // 15)", "    return max(1, minutes // 15)", "blocks round down"),
    ("        amount = min(_blocks(seg_minutes) * BLOCK, CAP)\n        if start.weekday() >= 5:\n            amount = int((Decimal(amount) * Decimal(\"1.5\")).quantize(Decimal(\"1\"), rounding=ROUND_HALF_UP))",
     "        amount = _blocks(seg_minutes) * BLOCK\n        if start.weekday() >= 5:\n            amount = int((Decimal(amount) * Decimal(\"1.5\")).quantize(Decimal(\"1\"), rounding=ROUND_HALF_UP))\n        amount = min(amount, CAP)",
     "cap applied after weekend multiplier"),
    ("    return exit <= morning", "    return exit < morning", "overnight end exclusive"),
    ("    if entry.hour < 22:", "    if entry.hour <= 22:", "overnight start excludes 22:xx"),
    ("        if start.weekday() >= 5:", "        if start.weekday() >= 4:", "Friday treated as weekend"),
    ("rounding=ROUND_HALF_UP))", "rounding=\"ROUND_HALF_EVEN\"))", "half-even rounding on weekends"),
    ("    for start, end in _segments(entry, exit):\n        seg_minutes = int((end - start).total_seconds() // 60)\n        amount = min(_blocks(seg_minutes) * BLOCK, CAP)",
     "    total_blocks = _blocks(minutes)\n    for start, end in _segments(entry, exit):\n        seg_minutes = int((end - start).total_seconds() // 60)\n        amount = min(round(total_blocks * seg_minutes / minutes) * BLOCK, CAP)",
     "blocks rounded over the whole stay, not per segment"),
]

REFERENCE_TESTS = '''from datetime import datetime as D
import fees

def f(a, b):
    return fees.fee(D(*a), D(*b))

# 2026-10-05 is a Monday; 2026-10-09 Friday; 2026-10-10 Saturday; 2026-10-11 Sunday.
def test_grace_exact_15():
    assert f((2026, 10, 5, 9, 0), (2026, 10, 5, 9, 15)) == 0

def test_grace_16():
    assert f((2026, 10, 5, 9, 0), (2026, 10, 5, 9, 16)) == 250

def test_blocks_round_up_31():
    assert f((2026, 10, 5, 9, 0), (2026, 10, 5, 9, 31)) == 375

def test_blocks_exact_30():
    assert f((2026, 10, 5, 9, 0), (2026, 10, 5, 9, 30)) == 250

def test_cap_weekday():
    assert f((2026, 10, 5, 6, 30), (2026, 10, 5, 21, 0)) == 2400

def test_cap_then_weekend_multiplier():
    assert f((2026, 10, 10, 6, 30), (2026, 10, 10, 21, 0)) == 3600

def test_overnight_exact_end():
    assert f((2026, 10, 5, 22, 0), (2026, 10, 6, 6, 0)) == 900

def test_overnight_end_plus_one():
    # not overnight: Monday 22:00-24:00 is 8 blocks (1000); Tuesday 00:00-06:01 is 25 blocks, capped at 2400
    assert f((2026, 10, 5, 22, 0), (2026, 10, 6, 6, 1)) == 3400

def test_overnight_start_2230():
    assert f((2026, 10, 5, 22, 30), (2026, 10, 6, 5, 0)) == 900

def test_friday_not_weekend():
    assert f((2026, 10, 9, 10, 0), (2026, 10, 9, 11, 0)) == 500

def test_weekend_rounding_half_up():
    assert f((2026, 10, 11, 10, 0), (2026, 10, 11, 10, 16)) == 375

def test_weekend_three_blocks_half_up():
    assert f((2026, 10, 11, 10, 0), (2026, 10, 11, 10, 31)) == 563

def test_weekend_rounding_odd_block_count():
    assert f((2026, 10, 11, 10, 0), (2026, 10, 11, 10, 46)) == 750

def test_segments_round_separately():
    # starts before 22:00 so not overnight: Monday 21:50-24:00 is 9 blocks (1125); Tuesday 00:00-00:20 is 2 blocks (250)
    assert f((2026, 10, 5, 21, 50), (2026, 10, 6, 0, 20)) == 1375
'''


def mutant_source(i):
    find, replace, _ = MUTANTS[i]
    assert CORRECT.count(find) == 1, f"mutant {i} find not unique"
    return CORRECT.replace(find, replace, 1)


def build():
    slug = "probe-mutant-kill-parking"
    mutants = {f"m{i + 1}": mutant_source(i) for i in range(len(MUTANTS))}
    grader = textwrap.dedent(f'''
        import os, subprocess, shutil, tempfile
        from pathlib import Path
        MUTANTS = {mutants!r}
        CORRECT_PATH = Path("/app/fees.py")
        SUITE = Path("/app/tests/test_fees.py")

        def _run_suite(module_source):
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                (tmp / "fees.py").write_text(module_source)
                shutil.copy(SUITE, tmp / "test_fees.py")
                r = subprocess.run(["/opt/task-verifier/bin/python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                    str(tmp / "test_fees.py")], cwd=tmp, capture_output=True, text=True, timeout=300,
                                   env={{**os.environ, "PYTHONPATH": str(tmp)}})
                return r.returncode, r.stdout[-2000:]

        def test_suite_exists_and_has_tests():
            assert SUITE.exists(), "write /app/tests/test_fees.py"
            assert "def test_" in SUITE.read_text()

        def test_suite_passes_on_correct_implementation():
            code, out = _run_suite(CORRECT_PATH.read_text())
            assert code == 0, "suite must pass against the correct implementation:\\n" + out
    ''')
    for name in mutants:
        grader += textwrap.dedent(f'''

            def test_suite_detects_hidden_variant_{name}():
                code, out = _run_suite(MUTANTS["{name}"])
                assert code != 0, "the suite does not detect a hidden faulty variant"
        ''')
    instruction = ("# Parking fee test suite\n\n`/app/fees.py` is a correct implementation of the parking fee rules in "
                   "`/app/docs/RULES.md`. We are about to refactor it and need a regression suite that would catch any "
                   "behavioral mistake.\n\nWrite `/app/tests/test_fees.py` (pytest; import the module as `fees`). It must "
                   "pass against the current implementation and fail against any implementation that deviates from the "
                   "rules. Grading runs your suite against the correct module and against several hidden faulty "
                   "variants; it must pass on the first and fail on every faulty variant. Do not modify `fees.py`.\n")
    spec = {"slug": slug, "summary": "Write a regression suite that detects hidden faulty variants of a parking fee calculator",
            "family": "exploration-probe", "instruction_md": instruction,
            "readme_md": "# Parking fee test suite\n\nExploration probe: the agent writes tests that must detect hidden faulty variants of a correct module.\n",
            "reference_files": {"fees.py": CORRECT, "docs/RULES.md": SPEC, "tests/test_fees.py": REFERENCE_TESTS},
            "fixtures": {}, "tests_py": grader,
            "bugs": [{"id": "b1", "file": "tests/test_fees.py", "find": REFERENCE_TESTS.rstrip("\n"),
                      "replace": "# Write the regression suite here.", "trap": "suite must detect every hidden variant",
                      "class": "thin self-checks"}],
            "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield."}
    dest = fa.CANDIDATES / slug
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, slug)
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {"b1": res.get("bug-b1", {}).get("failed", [])}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": slug, "valid": ok, "ref_failed": res["ref1"]["failed"], "ref_passed": res["ref1"]["passed"],
                      "starter_failed": res["starter"]["failed"]}))
    if not ok:
        print(res["ref1"]["output"][-3000:])


if __name__ == "__main__":
    build()
