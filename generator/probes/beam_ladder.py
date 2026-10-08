"""Exploration probe D3 (Claude-authored, NOT shipped): same lever as conveyor D on a new mechanic.

Lever under test: meaning carried by a sub-cell detail that must be inferred from a worked example
(emitter direction = edge bar; mirror orientation = thin diagonal line). A beam leaves each emitter,
reflects on mirrors, stops at walls/edges/emitters, and lights any detector it enters (detectors absorb).
Boards are only PNGs; the legend omits emitters and mirrors (inferred from docs/example.png + example.txt).
Each board is accepted only if misreading mirror orientation OR emitter direction changes its answer.
"""
import inspect
import json
import random
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

EMIT = {"N": (-1, 0), "S": (1, 0), "W": (0, -1), "E": (0, 1)}


def simulate(rows):
    """Return sorted detector numbers (row-major, 1-based) lit by all emitters."""
    h, w = len(rows), len(rows[0])
    detectors = [(r, c) for r in range(h) for c in range(w) if rows[r][c] == "D"]
    number = {pos: i + 1 for i, pos in enumerate(detectors)}
    lit = set()
    for r in range(h):
        for c in range(w):
            ch = rows[r][c]
            if ch not in "NSWE":
                continue
            dr, dc = EMIT[ch]
            cr, cc, seen = r, c, set()
            while True:
                cr, cc = cr + dr, cc + dc
                if not (0 <= cr < h and 0 <= cc < w):
                    break
                cell = rows[cr][cc]
                if (cr, cc, dr, dc) in seen:
                    break
                seen.add((cr, cc, dr, dc))
                if cell == "#" or cell in "NSWE":
                    break
                if cell == "D":
                    lit.add(number[(cr, cc)])
                    break
                if cell == "/":
                    dr, dc = -dc, -dr
                elif cell == "\\":
                    dr, dc = dc, dr
    return sorted(lit)


def flip_mirrors(rows):
    t = str.maketrans({"/": "\\", "\\": "/"})
    return [r.translate(t) for r in rows]


def emitters_east(rows):
    return [r.translate(str.maketrans({"N": "E", "S": "E", "W": "E"})) for r in rows]


def make_board(rng):
    while True:
        h, w = rng.randint(8, 10), rng.randint(8, 10)
        grid = [["." for _ in range(w)] for _ in range(h)]
        for r in range(h):
            for c in range(w):
                x = rng.random()
                grid[r][c] = "#" if x < 0.08 else rng.choice("/\\") if x < 0.30 else "."
        cells = [(r, c) for r in range(h) for c in range(w)]
        rng.shuffle(cells)
        for (r, c) in cells[:rng.randint(2, 3)]:
            grid[r][c] = rng.choice("NSWE")
        for (r, c) in cells[3:3 + rng.randint(5, 7)]:
            grid[r][c] = "D"
        rows = ["".join(r) for r in grid]
        lit = simulate(rows)
        n_det = sum(r.count("D") for r in rows)
        if not 2 <= len(lit) <= n_det - 2:
            continue
        if simulate(flip_mirrors(rows)) == lit or simulate(emitters_east(rows)) == lit:
            continue  # the lever must matter on every board
        return rows, lit


CELL = 24
COL = {"floor": (240, 240, 240), "wall": (50, 50, 50), "det": (30, 170, 70), "emit": (240, 140, 20),
       "bar": (90, 40, 0), "line": (40, 40, 40)}

RENDER = '''
from PIL import Image, ImageDraw
CELL = {cell}
COL = {col!r}
BOARDS = {boards!r}
EXAMPLE = {example!r}

def render(rows, path):
    h, w = len(rows), len(rows[0])
    img = Image.new("RGB", (w * CELL, h * CELL), COL["floor"])
    d = ImageDraw.Draw(img)
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            x0, y0, x1, y1 = c * CELL, r * CELL, c * CELL + CELL - 1, r * CELL + CELL - 1
            if ch == "#":
                d.rectangle([x0, y0, x1, y1], fill=COL["wall"])
            elif ch == "D":
                d.rectangle([x0, y0, x1, y1], fill=COL["det"])
            elif ch in "NSWE":
                d.rectangle([x0, y0, x1, y1], fill=COL["emit"])
                if ch == "N": d.rectangle([x0, y0, x1, y0 + 3], fill=COL["bar"])
                if ch == "S": d.rectangle([x0, y1 - 3, x1, y1], fill=COL["bar"])
                if ch == "W": d.rectangle([x0, y0, x0 + 3, y1], fill=COL["bar"])
                if ch == "E": d.rectangle([x1 - 3, y0, x1, y1], fill=COL["bar"])
            elif ch == "/":
                d.line([x0, y1, x1, y0], fill=COL["line"], width=3)
            elif ch == "\\\\":
                d.line([x0, y0, x1, y1], fill=COL["line"], width=3)
    img.save(path)

for name, rows in BOARDS.items():
    render(rows, name + ".png")
import os
os.makedirs("../docs", exist_ok=True)
render(EXAMPLE, "../docs/example.png")
'''

RULES = f"""# Beam board rules

Each board image `fixtures/boardN.png` is a grid of {CELL}x{CELL}-pixel cells (no grid lines).

* Beams start at every emitter and travel in that emitter's direction, one cell at a time.
* A beam stops when it leaves the grid, enters a wall, or enters another emitter.
* A mirror turns the beam 90 degrees according to its diagonal orientation (a mirror drawn from bottom-left to
  top-right turns a beam moving east to north, north to east, west to south, and south to west; the other
  orientation turns east to south, south to east, west to north, and north to west).
* A detector is lit if any beam enters it; detectors absorb the beam.
* If a beam would repeat the same cell and direction, it stops.
* Detectors are numbered 1, 2, 3, ... in row-major order (top row first, left to right).

## Legend (partial)

| Tile | Image |
|---|---|
| floor | light gray (240,240,240) |
| wall | dark gray (50,50,50) |
| detector | green (30,170,70) |

The remaining tile types (emitters and mirrors) appear in `docs/example.png`; `docs/example.txt` shows the same
board in text form: `N S W E` emitters facing that direction, `/` and `\\` mirrors, `D` detector, `#` wall.
"""

EXAMPLE_ROWS = ["E..\\..", ".#....", "...D.N", "/..D..", "..W..\\"]


def build(seed, slug, n_boards=2):
    rng = random.Random(seed)
    boards = {f"board{i + 1}": make_board(rng) for i in range(n_boards)}
    names = list(boards)
    lines = [f"{n}: " + (" ".join(map(str, boards[n][1])) or "none") for n in names]
    sim_src = inspect.getsource(simulate)
    tests = textwrap.dedent(f'''
        from pathlib import Path
        EMIT = {EMIT!r}
        BOARDS = {{k: v[0] for k, v in {boards!r}.items()}}
    ''') + "\n" + sim_src + textwrap.dedent('''

        def _lines():
            p = Path("/app/answer.txt")
            assert p.exists(), "answer.txt missing"
            return [l.strip() for l in p.read_text().splitlines() if l.strip()]

        def _expected(name):
            lit = simulate(BOARDS[name])
            return f"{name}: " + (" ".join(map(str, lit)) or "none")

        def test_line_count():
            assert len(_lines()) == len(BOARDS)
    ''')
    for n in names:
        tests += textwrap.dedent(f'''

            def test_{n}():
                lines = [l for l in _lines() if l.split(":")[0].strip() == "{n}"]
                assert len(lines) == 1, "missing line for {n}"
                assert " ".join(lines[0].split()) == _expected("{n}")
        ''')
    render = RENDER.format(cell=CELL, col=COL, boards={n: boards[n][0] for n in names}, example=EXAMPLE_ROWS)
    spec = {"slug": slug, "summary": "Decode beam boards from images and report which detectors are lit",
            "family": "exploration-probe",
            "instruction_md": ("# Beam boards\n\nFor every board image in `/app/fixtures`, work out which detectors are lit "
                               "under `/app/docs/RULES.md` and write `/app/answer.txt` with one line per board in board "
                               "order: `boardN: ` followed by the lit detector numbers in ascending order separated by "
                               "spaces, or `none`.\n\nExample line: `board1: 2 5 6`\n\nThe boards are only available as images.\n"),
            "readme_md": "# Beam boards\n\nExploration probe: decode beam puzzles from images (partial legend) and report lit detectors.\n",
            "reference_files": {"answer.txt": "\n".join(lines) + "\n", "docs/RULES.md": RULES,
                                "docs/example.txt": "\n".join(EXAMPLE_ROWS) + "\n"},
            "fixtures": {"fixtures/build_assets.py": render},
            "tests_py": tests,
            "bugs": [{"id": f"b{i + 1}", "file": "answer.txt", "find": line, "replace": f"{line.split(':')[0]}: ?",
                      "trap": "board must be decoded and simulated exactly", "class": "inferred sub-cell encoding"}
                     for i, line in enumerate(lines)],
            "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield."}
    dest = fa.CANDIDATES / slug
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, slug)
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {b["id"]: res.get(f"bug-{b['id']}", {}).get("failed", []) for b in spec["bugs"]}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": slug, "valid": ok, "answers": lines,
                      "sensitivity": {n: {"flip_mirrors": simulate(flip_mirrors(boards[n][0])),
                                          "emitters_east": simulate(emitters_east(boards[n][0]))} for n in names}}))
    if not ok:
        print(res.get("ref1", {}).get("output", "")[-2000:])


if __name__ == "__main__":
    build(int(sys.argv[1]) if len(sys.argv) > 1 else 4242, sys.argv[2] if len(sys.argv) > 2 else "probe-beam-d3")
