"""Exploration probe ladder (Claude-authored, NOT shipped): one original puzzle, five difficulty factors.

Purpose: break the cold start by measuring which single factor makes GLM-5.3-flash fail.
Variants add one factor at a time:
  A  text board, one board, answer = minimum number of moves
  B  + all minimal move sequences
  C  + board only as PNG (full legend)
  D  + partial legend (charge-pad color and conveyor marker inferred from a worked example)
  E  + eight boards, all must be correct
Mechanic (original): a robot moves one cell per move (costs 1 energy); after the move, conveyor tiles
push it repeatedly in their arrow direction; resting on a charge pad restores full energy; the goal
counts only when the robot rests on it after pushes. Candidates are written under output/candidates/
with family "exploration-probe" so their solver trajectories seed the solver profile.
"""
import inspect
import json
import random
import sys
import textwrap
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

DIRS = {"D": (1, 0), "L": (0, -1), "R": (0, 1), "U": (-1, 0)}
ARROWS = {"^": (-1, 0), "v": (1, 0), "<": (0, -1), ">": (0, 1)}


def solve(rows, energy):
    """Return (min_moves, sorted list of all minimal move strings) or (None, [])."""
    grid = [list(r) for r in rows]
    h, w = len(grid), len(grid[0])
    start = next((r, c) for r in range(h) for c in range(w) if grid[r][c] == "S")

    def step(pos, e, d):
        if e <= 0:
            return None
        r, c = pos[0] + DIRS[d][0], pos[1] + DIRS[d][1]
        if not (0 <= r < h and 0 <= c < w) or grid[r][c] == "#":
            return None
        e -= 1
        seen = set()
        while grid[r][c] in ARROWS:
            if (r, c) in seen:
                return None  # endless conveyor loop: the move is illegal
            seen.add((r, c))
            dr, dc = ARROWS[grid[r][c]]
            nr, nc = r + dr, c + dc
            if not (0 <= nr < h and 0 <= nc < w) or grid[nr][nc] == "#":
                break
            r, c = nr, nc
        if grid[r][c] == "+":
            e = energy
        return (r, c), e

    first = (start, energy)
    dist = {first: 0}
    preds = {first: []}
    order = deque([first])
    goal_states = []
    best = None
    while order:
        state = order.popleft()
        if best is not None and dist[state] >= best:
            continue
        for d in DIRS:
            nxt = step(state[0], state[1], d)
            if nxt is None:
                continue
            if nxt not in dist:
                dist[nxt] = dist[state] + 1
                preds[nxt] = [(state, d)]
                order.append(nxt)
                if grid[nxt[0][0]][nxt[0][1]] == "G":
                    best = dist[nxt] if best is None else best
                    goal_states.append(nxt)
            elif dist[nxt] == dist[state] + 1:
                preds[nxt].append((state, d))
    if best is None:
        return None, []
    seqs = set()

    def walk(state, suffix):
        if state == first:
            seqs.add(suffix)
            return
        for prev, d in preds[state]:
            walk(prev, d + suffix)

    for g in goal_states:
        if dist[g] == best:
            walk(g, "")
    return best, sorted(seqs)


def make_board(rng):
    while True:
        h, w = rng.randint(8, 10), rng.randint(8, 11)
        grid = [["." for _ in range(w)] for _ in range(h)]
        for r in range(h):
            for c in range(w):
                x = rng.random()
                grid[r][c] = "#" if x < 0.16 else rng.choice("^v<>") if x < 0.36 else "."
        cells = [(r, c) for r in range(h) for c in range(w)]
        rng.shuffle(cells)
        (sr, sc), (gr, gc), (p1r, p1c), (p2r, p2c) = cells[:4]
        grid[sr][sc], grid[gr][gc], grid[p1r][p1c], grid[p2r][p2c] = "S", "G", "+", "+"
        rows = ["".join(r) for r in grid]
        free_best, _ = solve(rows, 99)
        if free_best is None or not 6 <= free_best <= 11:
            continue
        energy = max(3, free_best - rng.randint(2, 4))  # energy must matter: a pad is needed
        best, seqs = solve(rows, energy)
        if best is None or best == free_best and energy >= free_best or not 7 <= best <= 13 or not 2 <= len(seqs) <= 5:
            continue
        return rows, energy, best, seqs


CELL = 20
COLORS = {".": (238, 238, 238), "#": (45, 45, 45), "S": (30, 70, 220), "G": (20, 160, 60),
          "+": (245, 200, 20), "conv": (150, 170, 255), "bar": (20, 20, 110)}


def render_source(boards, example, marker="bar"):
    """Source of fixtures/build_assets.py that renders every board and the worked example."""
    return textwrap.dedent(f'''
        from PIL import Image, ImageDraw
        CELL = {CELL}
        COLORS = {COLORS!r}
        ARROWS = {{"^": (0, -1), "v": (0, 1), "<": (-1, 0), ">": (1, 0)}}
        BOARDS = {boards!r}
        EXAMPLE = {example!r}
        MARKER = {marker!r}

        def render(rows, path):
            h, w = len(rows), len(rows[0])
            img = Image.new("RGB", (w * CELL, h * CELL), COLORS["."])
            d = ImageDraw.Draw(img)
            for r, row in enumerate(rows):
                for c, ch in enumerate(row):
                    x0, y0 = c * CELL, r * CELL
                    fill = COLORS["conv"] if ch in ARROWS else COLORS[ch]
                    d.rectangle([x0, y0, x0 + CELL - 1, y0 + CELL - 1], fill=fill)
                    if ch in ARROWS:
                        dx, dy = ARROWS[ch]
                        lo, hi = (0, CELL - 1) if MARKER == "bar" else (CELL // 2 - 3, CELL // 2 + 2)
                        t = 4 if MARKER == "bar" else 3
                        if dx == 1: d.rectangle([x0 + CELL - t, y0 + lo, x0 + CELL - 1, y0 + hi], fill=COLORS["bar"])
                        if dx == -1: d.rectangle([x0, y0 + lo, x0 + t - 1, y0 + hi], fill=COLORS["bar"])
                        if dy == 1: d.rectangle([x0 + lo, y0 + CELL - t, x0 + hi, y0 + CELL - 1], fill=COLORS["bar"])
                        if dy == -1: d.rectangle([x0 + lo, y0, x0 + hi, y0 + t - 1], fill=COLORS["bar"])
            img.save(path)

        for name, rows in BOARDS.items():
            render(rows, name + ".png")
        if EXAMPLE:
            import os
            os.makedirs("../docs", exist_ok=True)
            render(EXAMPLE, "../docs/example.png")
    ''').lstrip()


RULES = """# Conveyor robot rules

* The grid contains floor, walls, conveyors, charge pads, one start cell, and one goal cell.
* A move is one of `D` (down), `L` (left), `R` (right), `U` (up). The robot steps exactly one cell in that
  direction. A move into a wall or off the grid is illegal. Each move costs 1 energy; with 0 energy the
  robot cannot move.
* After the step, if the robot is on a conveyor it is pushed one cell in the conveyor's direction, and this
  repeats while it is on a conveyor. If the next cell would be a wall or off the grid, the robot stays on the
  current conveyor and pushing ends. If a push chain would visit the same conveyor twice, the move is illegal.
* After pushing ends, if the robot rests on a charge pad its energy is restored to the board's starting energy.
* The goal is reached only when the robot rests on the goal cell after pushing ends.
* A solution is a sequence of moves that reaches the goal. A minimal solution uses the fewest moves.
* Sequences are compared as strings; sort them in ascending order (`D` < `L` < `R` < `U`).
"""


def variant_spec(variant, boards, slug):
    text_boards = variant in "AB"
    all_seqs = variant != "A"
    partial = variant in "DEF"
    names = list(boards)
    reference_lines, files, fixtures = [], {}, {}
    for name in names:
        rows, energy, best, seqs = boards[name]
        reference_lines.append(f"{name}: {best} " + " ".join(seqs) if all_seqs else f"{name}: {best}")
        if text_boards:
            fixtures[f"fixtures/{name}.txt"] = f"energy {energy}\n" + "\n".join(rows) + "\n"
    files["answer.txt"] = "\n".join(reference_lines) + "\n"
    energy_table = "\n".join(f"| {n} | {boards[n][1]} |" for n in names)
    legend_full = ("| Tile | Image |\n|---|---|\n| floor | light gray (238,238,238) |\n| wall | dark gray (45,45,45) |\n"
                   "| start | blue (30,70,220) |\n| goal | green (20,160,60) |\n| charge pad | yellow (245,200,20) |\n"
                   "| conveyor | light blue (150,170,255) with a 4-pixel dark navy bar (20,20,110) along the edge it pushes toward |\n")
    legend_partial = ("| Tile | Image |\n|---|---|\n| floor | light gray (238,238,238) |\n| wall | dark gray (45,45,45) |\n"
                      "| start | blue (30,70,220) |\n| goal | green (20,160,60) |\n\nThe remaining tile types appear in "
                      "`docs/example.png`; `docs/example.txt` gives the same board in text form (`+` charge pad, `^ v < >` "
                      "conveyors pointing in the push direction).\n")
    if text_boards:
        files["docs/RULES.md"] = RULES + "\n## Board files\n\n`fixtures/boardN.txt`: first line `energy E`, then one row per line using `.` floor, `#` wall, `S` start, `G` goal, `+` charge pad, `^ v < >` conveyors (arrow = push direction).\n"
    else:
        files["docs/RULES.md"] = RULES + f"\n## Board images\n\nEach board is `fixtures/boardN.png`, drawn with {CELL}x{CELL}-pixel cells and no grid lines.\n\n{legend_partial if partial else legend_full}\n## Starting energy\n\n| Board | Energy |\n|---|---|\n{energy_table}\n"
    if partial:
        ex_rows = ["S.>>v.", "#^#.v.", "..+<<G"]
        files["docs/example.txt"] = "\n".join(ex_rows) + "\n"
    what = ("the minimum number of moves" if not all_seqs else
            "the minimum number of moves followed by ALL minimal move sequences (sorted, space separated)")
    instruction = (f"# Conveyor robot\n\nSolve every board in `/app/fixtures` under the rules in `/app/docs/RULES.md` and write "
                   f"`/app/answer.txt` with one line per board, in board order: `boardN: ` then {what}.\n\n"
                   + ("The boards are only available as images.\n" if not text_boards else ""))
    if variant == "A":
        instruction += "\nExample line: `board1: 9`\n"
    else:
        instruction += "\nExample line: `board1: 9 DDRURRULD DDRURRURD`\n"
    readme = (f"# Conveyor robot ({variant})\n\nExploration probe. A robot on a grid with conveyors, energy, and charge pads; "
              f"find minimal solutions for {len(names)} board(s). Variant {variant} of a five-step difficulty ladder.\n")
    solver_src = inspect.getsource(solve)
    test_boards = {n: (boards[n][0], boards[n][1]) for n in names}
    tests = textwrap.dedent(f'''
        from collections import deque
        from pathlib import Path
        DIRS = {DIRS!r}
        ARROWS = {ARROWS!r}
        BOARDS = {test_boards!r}
        ALL_SEQS = {all_seqs!r}
    ''') + "\n" + solver_src + textwrap.dedent('''

        def _lines():
            p = Path("/app/answer.txt")
            assert p.exists(), "answer.txt missing"
            return [l.strip() for l in p.read_text().splitlines() if l.strip()]

        def _expected(name):
            rows, energy = BOARDS[name]
            best, seqs = solve(rows, energy)
            return f"{name}: {best} " + " ".join(seqs) if ALL_SEQS else f"{name}: {best}"

        def test_line_count():
            assert len(_lines()) == len(BOARDS)
    ''')
    for name in names:
        tests += textwrap.dedent(f'''

            def test_{name}():
                lines = [l for l in _lines() if l.split(":")[0].strip() == "{name}"]
                assert len(lines) == 1, "missing line for {name}"
                assert " ".join(lines[0].split()) == _expected("{name}")
        ''')
    bugs = [{"id": f"b{i + 1}", "file": "answer.txt", "find": line, "replace": f"{line.split(':')[0]}: ?",
             "trap": f"{line.split(':')[0]} must be solved exactly", "class": "unsolved board"}
            for i, line in enumerate(reference_lines)]
    if not text_boards:
        fixtures["fixtures/build_assets.py"] = render_source({n: boards[n][0] for n in names},
                                                             ["S.>>v.", "#^#.v.", "..+<<G"] if partial else None,
                                                             marker="tick" if variant == "F" else "bar")
    return {"slug": slug, "summary": f"Conveyor robot probe variant {variant}", "family": "exploration-probe",
            "instruction_md": instruction, "readme_md": readme, "reference_files": files, "fixtures": fixtures,
            "tests_py": tests, "bugs": bugs, "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield."}


def main(variants="ABCDE", launch=False):
    rng = random.Random(20261006)
    pool = [make_board(rng) for _ in range(8)]
    out = {}
    for v in variants:
        names = [f"board{i + 1}" for i in range(8 if v == "E" else 1)]
        boards = {n: pool[i] for i, n in enumerate(names)}
        slug = f"probe-conveyor-{v.lower()}"
        spec = variant_spec(v, boards, slug)
        dest = fa.CANDIDATES / slug
        fa.write_task(spec, spec["bugs"], dest)
        res = fa.validate(spec, spec["bugs"], dest, slug)
        ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
        spec["bug_tests"] = {b["id"]: res.get(f"bug-{b['id']}", {}).get("failed", []) for b in spec["bugs"]}
        fa.write_task(spec, spec["bugs"], dest)
        out[v] = {"slug": slug, "valid": ok, "tests": res.get("ref1", {}).get("passed"), "boards": len(names),
                  "ref_fail": res.get("ref1", {}).get("failed"), "starter_failed": len(res.get("starter", {}).get("failed", []))}
        print(json.dumps(out[v]), flush=True)
    return out


def build_one(variant, seed, slug, n_boards=1):
    """Build a single variant on fresh boards (for consistency checks on new instances)."""
    rng = random.Random(seed)
    boards = {f"board{i + 1}": make_board(rng) for i in range(n_boards)}
    spec = variant_spec(variant, boards, slug)
    dest = fa.CANDIDATES / slug
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, slug)
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {b["id"]: res.get(f"bug-{b['id']}", {}).get("failed", []) for b in spec["bugs"]}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": slug, "valid": ok, "boards": n_boards,
                      "answer": spec["reference_files"]["answer.txt"].strip()[:200]}), flush=True)
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 2:
        build_one(sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 1)
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "ABCDE")
