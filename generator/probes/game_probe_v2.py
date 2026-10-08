"""Exploration probe (Claude-authored, NOT shipped): hidden game rules WITHOUT a free oracle for new boards.

Difference from game_probe.py: the binary only plays its built-in practice levels (`/app/game N MOVES`), so the
agent cannot differentially test its simulator on arbitrary boards. Hidden levels are kept only if each plausible
half-right rule model (M1-M4) loses on at least one of them, and every such model is *observably* wrong on the
practice levels within a short move sequence (fairness: everything is discoverable by experimenting).
"""
import itertools
import json
import random
import subprocess
import sys
import tempfile
import textwrap
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

DIRS = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}
TRUE = dict(ice=True, slide_switch=True, open_passable=True, toggle=True)
MISTAKES = {
    "M1_ice_is_floor": dict(TRUE, ice=False),
    "M2_switch_only_when_stepped": dict(TRUE, slide_switch=False),
    "M3_open_gate_blocks": dict(TRUE, open_passable=False),
    "M4_switch_only_opens": dict(TRUE, toggle=False),
}


def simulate(rows, moves, ice=True, slide_switch=True, open_passable=True, toggle=True):
    """Return (status, final_rows_with_player). Reference engine; true rules are the defaults."""
    g = [list(r) for r in rows]
    h, w = len(g), len(g[0])
    pr, pc = next((r, c) for r in range(h) for c in range(w) if g[r][c] == "S")
    g[pr][pc] = "."

    def blocked(r, c):
        if not (0 <= r < h and 0 <= c < w):
            return True
        ch = g[r][c]
        return ch == "#" or ch == "g" or (ch == "G" and not open_passable)

    status = "PLAYING"
    for m in moves:
        if m not in "UDLR":
            continue
        dr, dc = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}[m]
        if blocked(pr + dr, pc + dc):
            continue
        pr, pc = pr + dr, pc + dc
        slid = False
        while ice and g[pr][pc] == "a" and not blocked(pr + dr, pc + dc):
            pr, pc = pr + dr, pc + dc
            slid = True
        if g[pr][pc] == "d" and (slide_switch or not slid):
            for r in range(h):
                for c in range(w):
                    if g[r][c] == "g":
                        g[r][c] = "G"
                    elif g[r][c] == "G" and toggle:
                        g[r][c] = "g"
        if g[pr][pc] == "x":
            status = "LOSE"
            break
        if g[pr][pc] == "E":
            status = "WIN"
            break
    out = ["".join(("@" if (r, c) == (pr, pc) else g[r][c]) for c in range(w)) for r in range(h)]
    return status, out


def solve(rows, rules=TRUE, max_len=30, order="UDLR"):
    """BFS over move strings with state dedup (position + gate pattern); returns a winning string or None."""
    q = deque([""])
    seen = set()
    while q:
        moves = q.popleft()
        if len(moves) >= max_len:
            continue
        for m in order:
            nm = moves + m
            status, board = simulate(rows, nm, **rules)
            if status == "WIN":
                return nm
            if status == "LOSE":
                continue
            key = "".join(board)
            if key in seen:
                continue
            seen.add(key)
            q.append(nm)
    return None


ENGINE_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const char *LEVELS[][16] = { %LEVELS% };
static char g[16][32]; static int H, W;
static int blocked(int r, int c) { if (r < 0 || c < 0 || r >= H || c >= W) return 1; return g[r][c] == '#' || g[r][c] == 'g'; }
int main(int argc, char **argv) {
  if (argc < 3) { fprintf(stderr, "usage: game LEVEL_NUMBER MOVES   (levels 1-%NLEVELS%)\n"); return 2; }
  int L = atoi(argv[1]); if (L < 1 || L > %NLEVELS%) { fprintf(stderr, "unknown level\n"); return 2; }
  H = 0; int pr = -1, pc = -1;
  for (; LEVELS[L-1][H]; H++) { W = strlen(LEVELS[L-1][H]); for (int c = 0; c < W; c++) { g[H][c] = LEVELS[L-1][H][c]; if (g[H][c] == 'S') { pr = H; pc = c; g[H][c] = '.'; } } }
  const char *status = "PLAYING"; int steps = 0;
  for (const char *m = argv[2]; *m; m++) {
    int dr = 0, dc = 0;
    if (*m == 'U') dr = -1; else if (*m == 'D') dr = 1; else if (*m == 'L') dc = -1; else if (*m == 'R') dc = 1; else continue;
    steps++;
    if (blocked(pr + dr, pc + dc)) continue;
    pr += dr; pc += dc;
    while (g[pr][pc] == 'a' && !blocked(pr + dr, pc + dc)) { pr += dr; pc += dc; }
    if (g[pr][pc] == 'd') for (int r = 0; r < H; r++) for (int c = 0; c < W; c++) { if (g[r][c] == 'g') g[r][c] = 'G'; else if (g[r][c] == 'G') g[r][c] = 'g'; }
    if (g[pr][pc] == 'x') { status = "LOSE"; break; }
    if (g[pr][pc] == 'E') { status = "WIN"; break; }
  }
  for (int r = 0; r < H; r++) { for (int c = 0; c < W; c++) putchar(r == pr && c == pc ? '@' : g[r][c]); putchar('\n'); }
  printf("status: %s after %d moves\n", status, steps);
  return 0;
}
'''


def engine_source(levels):
    body = ", ".join("{" + ", ".join(json.dumps(r) for r in rows) + ", NULL}" for rows in levels)
    return ENGINE_C.replace("%LEVELS%", body).replace("%NLEVELS%", str(len(levels)))


def random_board(rng, h, w, gates=(2, 4), open_gates=(0, 2)):
    grid = [["." for _ in range(w)] for _ in range(h)]
    for r in range(h):
        for c in range(w):
            x = rng.random()
            grid[r][c] = "#" if x < 0.12 else "a" if x < 0.32 else "x" if x < 0.37 else "."
    cells = [(r, c) for r in range(h) for c in range(w)]
    rng.shuffle(cells)
    (sr, sc), (er, ec) = cells[:2]
    grid[sr][sc], grid[er][ec] = "S", "E"
    k = 2
    for _ in range(rng.randint(1, 2)):
        r, c = cells[k]; grid[r][c] = "d"; k += 1
    for _ in range(rng.randint(*gates)):
        r, c = cells[k]; grid[r][c] = "g"; k += 1
    for _ in range(rng.randint(*open_gates)):
        r, c = cells[k]; grid[r][c] = "G"; k += 1
    return ["".join(r) for r in grid]


def distinguishing_move(rows, rules, max_len=8):
    """Shortest move string whose observable result (status + board) differs from the true rules (joint-state BFS)."""
    q = deque([""])
    seen = set()
    while q:
        moves = q.popleft()
        if len(moves) >= max_len:
            continue
        for m in "UDLR":
            nm = moves + m
            a, b = simulate(rows, nm), simulate(rows, nm, **rules)
            if a != b:
                return nm
            if a[0] != "PLAYING":
                continue
            key = "".join(a[1]) + "|" + "".join(b[1])
            if key not in seen:
                seen.add(key)
                q.append(nm)
    return None


def build(seed=2026, slug="probe-hidden-rules-game-v2", timebox_minutes=10, n_practice=4, n_hidden=6):
    rng = random.Random(seed)
    # Practice levels: together they must expose every mistaken model within a short move string.
    while True:
        practice = []
        while len(practice) < n_practice:
            rows = random_board(rng, rng.randint(6, 8), rng.randint(7, 9))
            if solve(rows):
                practice.append(rows)
        witnesses = {name: next(((i + 1, mv) for i, rows in enumerate(practice)
                                 if (mv := distinguishing_move(rows, rules))), None)
                     for name, rules in MISTAKES.items()}
        if all(witnesses.values()):
            break
    # Hidden levels: every mistaken model must lose (its own plan fails under the true rules) on >= 1 level.
    hidden, beaten = {}, set()
    attempts = 0
    while len(hidden) < n_hidden and attempts < 4000:
        attempts += 1
        rows = random_board(rng, 8, 10)
        sol = solve(rows)
        if not sol or not 8 <= len(sol) <= 24:
            continue
        fails = []
        for name, rules in MISTAKES.items():
            # Beaten only if the model's BFS plan fails under the true rules for EVERY move ordering.
            plans = {solve(rows, rules, order="".join(o)) for o in itertools.permutations("UDLR")}
            if all(pl is None or simulate(rows, pl)[0] != "WIN" for pl in plans):
                fails.append(name)
        if len(fails) < 2:
            continue
        hidden[f"hidden{len(hidden) + 1}"] = (rows, sol, fails)
        beaten |= set(fails)
    assert beaten == set(MISTAKES), f"not every mistaken model is beaten: {beaten}"
    # C/Python agreement on the practice levels for random move strings.
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "game.c").write_text(engine_source(practice))
        subprocess.run(["cc", "-O2", "-o", str(tmp / "game"), str(tmp / "game.c")], check=True)
        frng = random.Random(1); mism = 0
        for _ in range(1500):
            i = frng.randrange(len(practice))
            moves = "".join(frng.choice("UDLR") for _ in range(frng.randint(0, 25)))
            out = subprocess.run([str(tmp / "game"), str(i + 1), moves], capture_output=True, text=True).stdout.splitlines()
            status, board = simulate(practice[i], moves)
            if out[-1].split()[1] != status or out[:-1] != board:
                mism += 1
        assert mism == 0, f"{mism} engine mismatches"
    print(json.dumps({"witnesses_on_practice": witnesses, "hidden": {k: {"len": len(v[1]), "beats": v[2]} for k, v in hidden.items()}}, indent=1), flush=True)

    sim_src = textwrap.dedent(__import__("inspect").getsource(simulate))
    solve_src = textwrap.dedent(__import__("inspect").getsource(solve))
    solver = ("#!/usr/bin/env python3\n\"\"\"Reference solver (knows the rules).\"\"\"\nimport sys\nfrom collections import deque\n"
              f"TRUE = {TRUE!r}\n\n" + sim_src + "\n\n" + solve_src +
              "\n\nif __name__ == \"__main__\":\n    rows = [l.rstrip('\\n') for l in open(sys.argv[1]) if l.strip()]\n    print(solve(rows) or '')\n")
    build_assets = textwrap.dedent(f'''
        import os, subprocess
        open("/tmp/game.c", "w").write({engine_source(practice)!r})
        subprocess.run(["gcc", "-O2", "-s", "-o", "/app/game", "/tmp/game.c"], check=True)
        os.remove("/tmp/game.c")
        os.chmod("/app/solve", 0o755)
    ''')
    fixtures = {f"fixtures/levels/level{i + 1}.txt": "\n".join(rows) + "\n" for i, rows in enumerate(practice)}
    fixtures["fixtures/build_assets.py"] = build_assets
    tests = textwrap.dedent(f'''
        import subprocess, tempfile
        from pathlib import Path
        HIDDEN = {{k: v[0] for k, v in {hidden!r}.items()}}
    ''') + "\n" + sim_src + textwrap.dedent('''

        def _run(name):
            rows = HIDDEN[name]
            with tempfile.TemporaryDirectory() as tmp:
                lvl = Path(tmp) / f"{name}.txt"
                lvl.write_text("\\n".join(rows) + "\\n")
                try:
                    p = subprocess.run(["/app/solve", str(lvl)], capture_output=True, text=True, timeout=60)
                except PermissionError:
                    p = subprocess.run(["python3", "/app/solve", str(lvl)], capture_output=True, text=True, timeout=60)
            moves = "".join(ch for ch in p.stdout if ch in "UDLR")
            assert moves, f"solver printed no moves for {name}: {p.stderr[-300:]}"
            assert simulate(rows, moves)[0] == "WIN", f"{name}: moves {moves} do not win"
    ''')
    for k in hidden:
        tests += f"\n\ndef test_{k}():\n    _run(\"{k}\")\n"
    instruction = textwrap.dedent(f"""\
        # Unknown grid game

        `/app/game N MOVES` plays practice level `N` (1-{n_practice}; the boards are also in
        `/app/fixtures/levels/levelN.txt`) and prints the final board (`@` marks the player) and the status
        (`WIN`, `LOSE`, or `PLAYING`). `MOVES` is a string of `U`, `D`, `L`, `R`. The player starts at `S`;
        reaching `E` wins. `.` is floor and `#` is wall. The other tiles' behaviors are not documented: work them
        out by experimenting with the practice levels.

        Write an executable `/app/solve` that takes a level file path (same format as the practice files) and
        prints a move string that wins that level. It will be run on new, unseen levels that use the same tiles
        and rules (the game binary cannot load them).

        Timebox: spend at most about {timebox_minutes} minutes on this (check with `date`). When the time is up,
        or if you are stuck, save your best solver to `/app/solve` and stop.
    """)
    spec = {"slug": slug, "summary": "Infer an undocumented grid game's rules from practice levels, then solve unseen levels",
            "family": "exploration-probe", "instruction_md": instruction,
            "readme_md": "# Unknown grid game (v2)\n\nExploration probe: interactive rule inference without a free oracle for new boards; hidden levels defeat half-right rule models; time-boxed instruction.\n",
            "reference_files": {"solve": solver}, "fixtures": fixtures, "tests_py": tests,
            "bugs": [{"id": "b1", "file": "solve", "find": solver.rstrip("\n"),
                      "replace": "#!/usr/bin/env python3\nimport sys\nsys.exit('not implemented')",
                      "trap": "solver must win unseen levels", "class": "incomplete inferred rule model"}],
            "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield. Time-boxed instruction."}
    dest = fa.CANDIDATES / slug
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, slug)
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {"b1": res.get("bug-b1", {}).get("failed", [])}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": slug, "valid": ok, "ref_failed": res["ref1"]["failed"], "starter_failed": res["starter"]["failed"]}))
    if not ok:
        print(res["ref1"]["output"][-2500:])


if __name__ == "__main__":
    build()
