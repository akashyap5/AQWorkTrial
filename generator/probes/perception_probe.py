"""Exploration probe (Claude-authored, NOT shipped): easy to generate, hard to verify.

Parcel-label codes are rendered in a custom 5x7 bitmap font that exists only in the deleted build script, so the
agent cannot re-render candidates to check its reading. docs/font_sample.png shows the letters A-Z only. Codes mix
digits that resemble letters (0/O, 1/I, 5/S, 8/B, 2/Z) with those letters, side by side: a careful reader matches
the letters exactly and reads the remaining glyphs as digits; a nearest-letter match reads 0 as O.
"""
import json
import random
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

FONT = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "D": ["###..", "#..#.", "#...#", "#...#", "#...#", "#..#.", "###.."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".####"],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "#.#.#", ".#.#."],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "0": [".##..", "#..#.", "#..#.", "#..#.", "#..#.", "#..#.", ".##.."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": ["..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."],
}
PAIRS = [("0", "O"), ("1", "I"), ("5", "S"), ("8", "B"), ("2", "Z")]


def make_codes(seed=31, n=6):
    rng = random.Random(seed)
    letters = [c for c in FONT if c.isalpha()]
    codes = []
    for _ in range(n):
        chosen = rng.sample(PAIRS, 2)
        chars = [d for d, _ in chosen] + [l for _, l in chosen] + [rng.choice(letters + list("3479")) for _ in range(4)]
        rng.shuffle(chars)
        codes.append("".join(chars))
    return codes


BUILD = '''
import os, random
from PIL import Image, ImageDraw
FONT = %FONT%
CODES = %CODES%
CELL, GAP = 6, 2

def render(text, path, seed):
    rng = random.Random(seed)
    w = (len(text) * (5 + GAP) + GAP) * CELL
    h = (7 + 2 * GAP) * CELL
    img = Image.new("L", (w, h), 0)
    px = img.load()
    for x in range(w):
        for y in range(h):
            px[x, y] = rng.randint(205, 250)
    d = ImageDraw.Draw(img)
    for i, ch in enumerate(text):
        x0 = (GAP + i * (5 + GAP)) * CELL
        for r, row in enumerate(FONT[ch]):
            for c, bit in enumerate(row):
                if bit == "#":
                    shade = rng.randint(25, 70)
                    d.rectangle([x0 + c * CELL, (GAP + r) * CELL, x0 + c * CELL + CELL - 1, (GAP + r) * CELL + CELL - 1], fill=shade)
    for _ in range(w * h // 300):  # light speckle
        px[rng.randrange(w), rng.randrange(h)] = rng.randint(90, 160)
    img.save(path)

os.makedirs("../codes", exist_ok=True)
for k, code in enumerate(CODES):
    render(code, f"../codes/label{k + 1}.png", 100 + k)
os.makedirs("../docs", exist_ok=True)
render("ABCDEFGHIJKLM", "../docs/font_sample_1.png", 7)
render("NOPQRSTUVWXYZ", "../docs/font_sample_2.png", 8)
'''


def build(slug="probe-hardverify-label-codes"):
    codes = make_codes()
    answer = "".join(f"label{k + 1}.png: {code}\n" for k, code in enumerate(codes))
    instruction = textwrap.dedent("""
        # Parcel label codes

        `/app/codes/` holds six scanned parcel labels (`label1.png` ... `label6.png`). Each shows one 8-character
        code made of uppercase letters A-Z and digits 0-9, printed in the courier's label font. The courier's style
        sheet shows that font's letters in `/app/docs/font_sample_1.png` (A to M) and `/app/docs/font_sample_2.png`
        (N to Z), left to right.

        Write `/app/codes.txt` with one line per label, in label order, formatted as `label1.png: <code>`.
    """).strip() + "\n"
    tests = textwrap.dedent(f"""
        from pathlib import Path
        EXPECTED = {dict(zip([f"label{k + 1}.png" for k in range(len(codes))], codes))!r}

        def _lines():
            p = Path("/app/codes.txt")
            assert p.exists(), "codes.txt missing"
            out = {{}}
            for line in p.read_text().splitlines():
                if ":" in line:
                    name, code = line.split(":", 1)
                    out[name.strip()] = code.strip()
            return out

        def test_all_labels_present():
            assert sorted(_lines()) == sorted(EXPECTED)
    """)
    for k in range(len(codes)):
        tests += textwrap.dedent(f"""

            def test_label{k + 1}():
                assert _lines().get("label{k + 1}.png") == EXPECTED["label{k + 1}.png"]
        """)
    spec = {"slug": slug, "summary": "Read 8-character codes from scanned labels in an unseen bitmap font (digits resemble letters)",
            "family": "exploration-probe", "instruction_md": instruction,
            "readme_md": "# Parcel label codes\n\nExploration probe: easy to generate, hard to verify (perception without a reference renderer).\n",
            "reference_files": {"codes.txt": answer},
            "fixtures": {"fixtures/build_assets.py": BUILD.replace("%FONT%", json.dumps(FONT)).replace("%CODES%", json.dumps(codes))},
            "tests_py": tests,
            "bugs": [{"id": f"b{k + 1}", "file": "codes.txt", "find": f"label{k + 1}.png: {code}", "replace": f"label{k + 1}.png: ?",
                      "trap": "digits that resemble letters", "class": "unverifiable decoding"} for k, code in enumerate(codes)],
            "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield."}
    dest = fa.CANDIDATES / slug
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, slug)
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {b["id"]: res.get(f"bug-{b['id']}", {}).get("failed", []) for b in spec["bugs"]}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": slug, "valid": ok, "codes": codes}), flush=True)
    if not ok:
        print(res["ref1"]["output"][-1500:])


if __name__ == "__main__":
    build()
