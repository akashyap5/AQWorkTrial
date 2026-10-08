"""Exploration probe (Claude-authored, NOT shipped): infer hidden game rules by playing, then solve unseen levels.

A compiled binary `/app/game LEVEL MOVES` plays an original grid game whose tile behaviors are undocumented:
  a = ice (keep sliding in the same direction while on ice), d = switch (toggles every gate),
  g = closed gate (blocks), G = open gate, x = hazard (stepping on it loses), E = exit (win).
The agent gets practice levels it can play freely and must write `/app/solve LEVEL` printing a winning
move string. Hidden tests run the solver on unseen levels and replay the moves in an independent Python
engine (fuzz-checked against the C engine). Levels are kept only if naive rule guesses fail.
"""
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

ENGINE_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MAXN 64
static char g[MAXN][MAXN]; static int H = 0, W = 0;
static int blocked(int r, int c) {
  if (r < 0 || c < 0 || r >= H || c >= W) return 1;
  return g[r][c] == '#' || g[r][c] == 'g';
}
static void toggle(void) {
  for (int r = 0; r < H; r++) for (int c = 0; c < W; c++) {
    if (g[r][c] == 'g') g[r][c] = 'G'; else if (g[r][c] == 'G') g[r][c] = 'g';
  }
}
int main(int argc, char **argv) {
  if (argc < 3) { fprintf(stderr, "usage: game LEVELFILE MOVES\n"); return 2; }
  FILE *f = fopen(argv[1], "r"); if (!f) { perror("level"); return 2; }
  char line[256]; int pr = -1, pc = -1;
  while (fgets(line, sizeof line, f) && H < MAXN) {
    int n = strcspn(line, "\r\n"); if (n == 0) continue; line[n] = 0;
    W = n; for (int c = 0; c < n; c++) { if (line[c] == 'S') { pr = H; pc = c; line[c] = '.'; } g[H][c] = line[c]; }
    H++;
  }
  fclose(f);
  const char *status = "PLAYING"; int steps = 0;
  for (const char *m = argv[2]; *m; m++) {
    int dr = 0, dc = 0;
    if (*m == 'U') dr = -1; else if (*m == 'D') dr = 1; else if (*m == 'L') dc = -1; else if (*m == 'R') dc = 1; else continue;
    steps++;
    if (blocked(pr + dr, pc + dc)) continue;
    pr += dr; pc += dc;
    while (g[pr][pc] == 'a' && !blocked(pr + dr, pc + dc)) { pr += dr; pc += dc; }
    if (g[pr][pc] == 'd') toggle();
    if (g[pr][pc] == 'x') { status = "LOSE"; break; }
    if (g[pr][pc] == 'E') { status = "WIN"; break; }
  }
  for (int r = 0; r < H; r++) { for (int c = 0; c < W; c++) putchar(r == pr && c == pc ? '@' : g[r][c]); putchar('\n'); }
  printf("status: %s after %d moves\n", status, steps);
  return 0;
}
'''


def play(rows, moves, ice=True, switch=True):
    """Reference Python engine (also used, verbatim, inside the hidden tests)."""
    g = [list(r) for r in rows]
    h, w = len(g), len(g[0])
    pr, pc = next((r, c) for r in range(h) for c in range(w) if g[r][c] == "S")
    g[pr][pc] = "."

    def blocked(r, c):
        return not (0 <= r < h and 0 <= c < w) or g[r][c] in "#g"

    for m in moves:
        if m not in "UDLR":
            continue
        dr, dc = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}[m]
        if blocked(pr + dr, pc + dc):
            continue
        pr, pc = pr + dr, pc + dc
        while ice and g[pr][pc] == "a" and not blocked(pr + dr, pc + dc):
            pr, pc = pr + dr, pc + dc
        if switch and g[pr][pc] == "d":
            for r in range(h):
                for c in range(w):
                    if g[r][c] == "g":
                        g[r][c] = "G"
                    elif g[r][c] == "G":
                        g[r][c] = "g"
        if g[pr][pc] == "x":
            return "LOSE"
        if g[pr][pc] == "E":
            return "WIN"
    return "PLAYING"


def bfs(rows, ice=True, switch=True):
    """Shortest winning move string under the given (possibly wrong) rule assumptions; None if unwinnable."""
    h, w = len(rows), len(rows[0])
    start = next((r, c) for r in range(h) for c in range(w) if rows[r][c] == "S")

    def cell(r, c, gates_open):
        ch = rows[r][c]
        if ch == "g" and gates_open:
            return "G"
        if ch == "G" and gates_open:
            return "g"
        return ch

    def blocked(r, c, gates_open):
        return not (0 <= r < h and 0 <= c < w) or cell(r, c, gates_open) in "#g"

    def step(pos, gates_open, m):
        dr, dc = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}[m]
        r, c = pos
        if blocked(r + dr, c + dc, gates_open):
            return (r, c), gates_open, "PLAYING"
        r, c = r + dr, c + dc
        while ice and cell(r, c, gates_open) == "a" and not blocked(r + dr, c + dc, gates_open):
            r, c = r + dr, c + dc
        if switch and cell(r, c, gates_open) == "d":
            gates_open = not gates_open
        here = cell(r, c, gates_open)
        if here == "x":
            return (r, c), gates_open, "LOSE"
        if here == "E":
            return (r, c), gates_open, "WIN"
        return (r, c), gates_open, "PLAYING"

    first = (start, False)
    parent = {first: None}
    q = deque([first])
    while q:
        state = q.popleft()
        for m in "UDLR":
            pos, gates, res = step(state[0], state[1], m)
            if res == "LOSE":
                continue
            nxt = (pos, gates)
            if res == "WIN":
                moves = [m]
                cur = state
                while parent[cur] is not None:
                    cur, mv = parent[cur]
                    moves.append(mv)
                return "".join(reversed(moves))
            if nxt not in parent:
                parent[nxt] = (state, m)
                q.append(nxt)
    return None


def make_level(rng, h=8, w=10):
    while True:
        grid = [["." for _ in range(w)] for _ in range(h)]
        for r in range(h):
            for c in range(w):
                x = rng.random()
                grid[r][c] = "#" if x < 0.12 else "a" if x < 0.30 else "x" if x < 0.36 else "."
        cells = [(r, c) for r in range(h) for c in range(w)]
        rng.shuffle(cells)
        (sr, sc), (er, ec), (dr_, dc_) = cells[:3]
        grid[sr][sc], grid[er][ec], grid[dr_][dc_] = "S", "E", "d"
        for (r, c) in cells[3:3 + rng.randint(2, 4)]:
            grid[r][c] = "g"
        rows = ["".join(r) for r in grid]
        sol = bfs(rows)
        if not sol or not 6 <= len(sol) <= 26:
            continue
        # Naive rule guesses must fail on this level (their plans replayed under the true rules).
        naive = [bfs(rows, ice=False), bfs(rows, switch=False), bfs(rows, ice=False, switch=False)]
        if any(p is not None and play(rows, p) == "WIN" for p in naive):
            continue
        uses_ice = play(rows, sol, ice=False) != "WIN"
        uses_switch = play(rows, sol, switch=False) != "WIN"
        if not (uses_ice and uses_switch):
            continue
        return rows, sol


def fuzz_engines(n=3000):
    """Check the C engine and the Python engine agree on random levels and move strings."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "game.c").write_text(ENGINE_C)
        subprocess.run(["cc", "-O2", "-o", str(tmp / "game"), str(tmp / "game.c")], check=True)
        rng = random.Random(7)
        mismatches = 0
        for i in range(n):
            h, w = rng.randint(3, 8), rng.randint(3, 9)
            grid = [[rng.choice(".....#aaxgGd") for _ in range(w)] for _ in range(h)]
            r0, c0 = rng.randrange(h), rng.randrange(w)
            grid[r0][c0] = "S"
            if rng.random() < 0.7:
                r1, c1 = rng.randrange(h), rng.randrange(w)
                if (r1, c1) != (r0, c0):
                    grid[r1][c1] = "E"
            rows = ["".join(r) for r in grid]
            moves = "".join(rng.choice("UDLR") for _ in range(rng.randint(0, 20)))
            (tmp / "lvl.txt").write_text("\n".join(rows) + "\n")
            out = subprocess.run([str(tmp / "game"), str(tmp / "lvl.txt"), moves], capture_output=True, text=True).stdout
            c_status = out.strip().splitlines()[-1].split()[1]
            py_status = play(rows, moves)
            if c_status != py_status:
                mismatches += 1
                if mismatches < 4:
                    print("MISMATCH", rows, moves, c_status, py_status)
        return mismatches


def build(seed=909, slug="probe-hidden-rules-game", timebox_minutes=5):
    mism = fuzz_engines()
    print(json.dumps({"engine_fuzz_mismatches": mism}), flush=True)
    assert mism == 0
    rng = random.Random(seed)
    practice = {f"practice{i + 1}": make_level(rng)[0] for i in range(3)}
    hidden = {f"hidden{i + 1}": make_level(rng) for i in range(5)}
    play_src = textwrap.dedent(__import__("inspect").getsource(play))
    bfs_src = textwrap.dedent(__import__("inspect").getsource(bfs))
    solver = ("#!/usr/bin/env python3\n\"\"\"Reference solver (knows the rules).\"\"\"\nimport sys\nfrom collections import deque\n"
              f"DIRS = {DIRS!r}\n\n" + bfs_src + "\n\nif __name__ == \"__main__\":\n"
              "    rows = [l.rstrip('\\n') for l in open(sys.argv[1]) if l.strip()]\n    print(bfs(rows) or '')\n")
    build_assets = textwrap.dedent(f'''
        import os, subprocess
        SRC = {ENGINE_C!r}
        open("/tmp/game.c", "w").write(SRC)
        subprocess.run(["gcc", "-O2", "-s", "-o", "/app/game", "/tmp/game.c"], check=True)
        os.remove("/tmp/game.c")
        os.chmod("/app/solve", 0o755)
    ''')
    files = {"solve": solver}
    fixtures = {f"fixtures/levels/{k}.txt": "\n".join(v) + "\n" for k, v in practice.items()}
    fixtures["fixtures/build_assets.py"] = build_assets
    tests = textwrap.dedent(f'''
        import subprocess, tempfile
        from pathlib import Path
        HIDDEN = {{k: v[0] for k, v in {hidden!r}.items()}}
    ''') + "\n" + play_src + textwrap.dedent('''

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
            assert play(rows, moves) == "WIN", f"{name}: moves {moves} do not win"
    ''')
    for k in hidden:
        tests += f"\n\ndef test_{k}():\n    _run(\"{k}\")\n"
    instruction = textwrap.dedent(f"""\
        # Unknown grid game

        `/app/game LEVELFILE MOVES` plays a grid game and prints the final board and status (`WIN`, `LOSE`, or
        `PLAYING`). `MOVES` is a string of `U`, `D`, `L`, `R`. The player starts at `S`; reaching `E` wins.
        `.` is floor and `#` is wall. The other tiles' behaviors are not documented: work them out by playing the
        practice levels in `/app/fixtures/levels/`.

        Write an executable `/app/solve` that takes a level file path and prints a move string that wins that
        level. It will be run on new, unseen levels built from the same tiles and rules.

        Timebox: spend at most about {timebox_minutes} minutes on this (check with `date`). When the time is up,
        or if you are stuck, write your best solver to `/app/solve` and stop.
    """)
    spec = {"slug": slug, "summary": "Infer an undocumented grid game's rules by playing it, then write a solver for unseen levels",
            "family": "exploration-probe", "instruction_md": instruction,
            "readme_md": "# Unknown grid game\n\nExploration probe: interactive rule inference (undocumented tile behaviors) and generalization to hidden levels; time-boxed instruction.\n",
            "reference_files": files, "fixtures": fixtures, "tests_py": tests,
            "bugs": [{"id": "b1", "file": "solve", "find": solver.rstrip("\n"),
                      "replace": "#!/usr/bin/env python3\nimport sys\nsys.exit('not implemented')",
                      "trap": "solver must win unseen levels", "class": "hidden rule inference"}],
            "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield. Time-boxed instruction."}
    dest = fa.CANDIDATES / slug
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, slug)
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {"b1": res.get("bug-b1", {}).get("failed", [])}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": slug, "valid": ok, "hidden_solution_lengths": {k: len(v[1]) for k, v in hidden.items()},
                      "ref_failed": res["ref1"]["failed"], "starter_failed": res["starter"]["failed"]}))
    if not ok:
        print(res["ref1"]["output"][-2500:])


if __name__ == "__main__":
    build()
