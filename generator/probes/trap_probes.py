"""Exploration probes (Claude-authored, NOT shipped): easy tasks with one or two plainly stated details that the
reflexive implementation gets wrong. Purpose: check whether this task shape can make GLM-5.3-flash fail at all
before asking GLM-5.1 to generate it at scale. Examples never exercise the traps; hidden tests mostly do.
"""
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

STUB = '    """Implement me."""\n    raise NotImplementedError\n'


def task(slug, summary, instruction, module, solution_body, signature, tests, readme):
    solution = f"{signature}\n{solution_body}"
    return {"slug": slug, "summary": summary, "family": "exploration-probe", "instruction_md": textwrap.dedent(instruction).strip() + "\n",
            "readme_md": readme, "reference_files": {module: solution}, "fixtures": {}, "tests_py": textwrap.dedent(tests),
            "bugs": [{"id": "b1", "file": module, "find": solution_body, "replace": STUB, "trap": summary, "class": "stated detail"}],
            "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield."}


INVOICE = task(
    "probe-trap-invoice-rounding", "half-away-from-zero line rounding and truncated tax",
    """
    # Invoice totals

    Implement `invoice_total(lines, tax_rate)` in `/app/invoice.py`.

    Each line is a dict with `qty` (an int; negative for returned items) and `unit_price` (a decimal string such as
    `"4.99"` or `"0.125"`). A line's amount is `qty * unit_price` rounded to whole cents, with halves rounded away
    from zero. The subtotal is the sum of the line amounts. Tax is `subtotal * tax_rate` (`tax_rate` is a decimal
    string such as `"0.0825"`) cut down to whole cents toward zero. The total is subtotal plus tax.

    Return a dict with string values `"subtotal"`, `"tax"`, and `"total"`, each with exactly two decimals.

    Example: `invoice_total([{"qty": 2, "unit_price": "4.95"}, {"qty": 1, "unit_price": "10.00"}], "0.10")` returns
    `{"subtotal": "19.90", "tax": "1.99", "total": "21.89"}`.
    """,
    "invoice.py",
    '''    from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
    cent = Decimal("0.01")
    subtotal = sum(((Decimal(line["qty"]) * Decimal(line["unit_price"])).quantize(cent, rounding=ROUND_HALF_UP)
                    for line in lines), Decimal("0.00"))
    tax = (subtotal * Decimal(tax_rate)).quantize(cent, rounding=ROUND_DOWN)
    return {"subtotal": f"{subtotal:.2f}", "tax": f"{tax:.2f}", "total": f"{subtotal + tax:.2f}"}
''',
    "def invoice_total(lines, tax_rate):",
    '''
    import random
    from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
    from invoice import invoice_total

    def reference(lines, tax_rate):
        cent = Decimal("0.01")
        subtotal = sum(((Decimal(l["qty"]) * Decimal(l["unit_price"])).quantize(cent, rounding=ROUND_HALF_UP) for l in lines), Decimal("0.00"))
        tax = (subtotal * Decimal(tax_rate)).quantize(cent, rounding=ROUND_DOWN)
        return {"subtotal": f"{subtotal:.2f}", "tax": f"{tax:.2f}", "total": f"{subtotal + tax:.2f}"}

    def cases():
        rng = random.Random(4242)
        out = []
        for _ in range(30):
            lines = []
            for _ in range(rng.randint(1, 5)):
                cents = rng.randint(1, 4000)
                price = f"{cents // 1000}.{cents % 1000:03d}" if rng.random() < 0.7 else f"{cents // 100}.{cents % 100:02d}"
                if rng.random() < 0.6:
                    price = price[:-1] + "5" if len(price.split(".")[1]) == 3 else price
                lines.append({"qty": rng.choice([q for q in range(-3, 13) if q]), "unit_price": price})
            out.append((lines, rng.choice(["0.0825", "0.07", "0.0625", "0.0475", "0.10"])))
        return out

    def test_example():
        assert invoice_total([{"qty": 2, "unit_price": "4.95"}, {"qty": 1, "unit_price": "10.00"}], "0.10") == \\
            {"subtotal": "19.90", "tax": "1.99", "total": "21.89"}

    def test_hidden_invoices():
        wrong = [(lines, rate) for lines, rate in cases() if invoice_total(lines, rate) != reference(lines, rate)]
        assert not wrong, f"{len(wrong)} of 30 invoices wrong, e.g. {wrong[0]}"
    ''',
    "# Invoice totals\n\nExploration probe: invoice arithmetic with stated rounding rules.\n")

SIBLINGS = task(
    "probe-trap-sibling-perspective", "perspective counting (a sibling's sisters include the child)",
    """
    # Sibling counts

    Implement `siblings_of_sibling(child)` in `/app/family.py` for a family-survey tool.

    `child` is a dict describing one surveyed child: `"gender"` (`"F"` or `"M"`), `"brothers"` (how many brothers
    the child has), and `"sisters"` (how many sisters the child has). All children in a family share both parents.

    Return a dict with two keys:
    - `"sisters_of_a_brother"`: how many sisters one of the child's brothers has, or `None` if the child has no brothers.
    - `"brothers_of_a_sister"`: how many brothers one of the child's sisters has, or `None` if the child has no sisters.

    Example: `siblings_of_sibling({"gender": "M", "brothers": 0, "sisters": 0})` returns
    `{"sisters_of_a_brother": None, "brothers_of_a_sister": None}`.
    """,
    "family.py",
    '''    girl = child["gender"] == "F"
    return {"sisters_of_a_brother": child["sisters"] + (1 if girl else 0) if child["brothers"] else None,
            "brothers_of_a_sister": child["brothers"] + (0 if girl else 1) if child["sisters"] else None}
''',
    "def siblings_of_sibling(child):",
    '''
    from family import siblings_of_sibling

    def reference(child):
        girl = child["gender"] == "F"
        return {"sisters_of_a_brother": child["sisters"] + (1 if girl else 0) if child["brothers"] else None,
                "brothers_of_a_sister": child["brothers"] + (0 if girl else 1) if child["sisters"] else None}

    CASES = [{"gender": g, "brothers": b, "sisters": s} for g in "FM" for b in range(0, 4) for s in range(0, 4)]

    def test_example():
        assert siblings_of_sibling({"gender": "M", "brothers": 0, "sisters": 0}) == {"sisters_of_a_brother": None, "brothers_of_a_sister": None}

    def test_all_family_shapes():
        wrong = [c for c in CASES if siblings_of_sibling(dict(c)) != reference(c)]
        assert not wrong, f"{len(wrong)} of {len(CASES)} wrong, e.g. {wrong[0]} -> {siblings_of_sibling(dict(wrong[0]))}"
    ''',
    "# Sibling counts\n\nExploration probe: counting from another family member's point of view.\n")

ERRAND_LIST = [
    {"name": "SparkleWash", "purpose": "wash the car", "distance_km": 0.05},
    {"name": "Bakery", "purpose": "buy bread", "distance_km": 0.3},
    {"name": "QuickLube", "purpose": "get the car's oil changed", "distance_km": 0.8},
    {"name": "Library", "purpose": "return two books", "distance_km": 1.2},
    {"name": "TyreTown", "purpose": "have the winter tyres fitted to the car", "distance_km": 1.5},
    {"name": "Post office", "purpose": "post a letter", "distance_km": 2.5},
    {"name": "Glass Pro", "purpose": "repair a chip in the car's windscreen", "distance_km": 0.6},
    {"name": "Pharmacy", "purpose": "collect a prescription", "distance_km": 0.9},
    {"name": "Valet Corner", "purpose": "get the car vacuumed and polished", "distance_km": 0.2},
    {"name": "Gym", "purpose": "attend a yoga class", "distance_km": 1.9},
]
ERRAND_ANSWER = {"SparkleWash": "drive", "Bakery": "walk", "QuickLube": "drive", "Library": "walk", "TyreTown": "drive",
                 "Post office": "drive", "Glass Pro": "drive", "Pharmacy": "walk", "Valet Corner": "drive", "Gym": "walk"}
ERRANDS = {
    "slug": "probe-trap-errand-walk-drive", "summary": "goal-implied constraint (car services need the car)", "family": "exploration-probe",
    "instruction_md": textwrap.dedent("""
        # Errand planner

        `/app/errands.json` lists today's errands. Each has a `name`, a `purpose` (what will be done there), and a
        one-way `distance_km` from home.

        For each errand decide `walk` or `drive`. Walk whenever the errand can be completed on foot and the distance is
        under 2 km; otherwise drive. Write `/app/plan.json` as an object mapping each errand name to `"walk"` or
        `"drive"`.

        For example, an errand to buy bread 0.4 km away is `"walk"`, and one to buy screws 5 km away is `"drive"`.
    """).strip() + "\n",
    "readme_md": "# Errand planner\n\nExploration probe: walk-or-drive decisions where the errand's purpose matters.\n",
    "reference_files": {"plan.json": json.dumps(ERRAND_ANSWER, indent=1) + "\n", "errands.json": json.dumps(ERRAND_LIST, indent=1) + "\n"},
    "fixtures": {},
    "tests_py": textwrap.dedent(f'''
        import json
        from pathlib import Path
        EXPECTED = {ERRAND_ANSWER!r}

        def _plan():
            p = Path("/app/plan.json")
            assert p.exists(), "plan.json missing"
            return json.loads(p.read_text())

        def test_every_errand_decided():
            assert sorted(_plan()) == sorted(EXPECTED)

        def test_decisions():
            plan = _plan()
            wrong = {{k: plan.get(k) for k in EXPECTED if plan.get(k) != EXPECTED[k]}}
            assert not wrong, f"wrong decisions: {{wrong}}"
    '''),
    "bugs": [{"id": "b1", "file": "plan.json", "find": json.dumps(ERRAND_ANSWER, indent=1), "replace": "{}", "trap": "car services need the car", "class": "stated detail"}],
    "prompt_version": "claude-exploration-probe",
    "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield.",
}

LOGS = task(
    "probe-trap-log-fields", "empty fields from consecutive delimiters and natural sort order",
    """
    # Upload log summary

    Implement `summarize(lines)` in `/app/logsum.py`. Each line of an upload log has exactly five fields separated
    by single spaces: `timestamp user file size status`. A field that was not recorded is empty, so its two
    neighbouring separators appear next to each other. `size` is an integer number of bytes when present.

    Return a list of `(file, total_size)` tuples, one per distinct file with status `ok`, where `total_size` sums the
    recorded sizes for that file (missing sizes count as 0). Sort the list by file name in natural order, so
    `img2.png` comes before `img10.png`.

    Example: `summarize(["10:00 ann a.txt 5 ok", "10:01 bob b.txt 7 ok", "10:02 ann a.txt 1 ok"])` returns
    `[("a.txt", 6), ("b.txt", 7)]`.
    """,
    "logsum.py",
    '''    import re
    totals = {}
    for line in lines:
        timestamp, user, name, size, status = line.split(" ")
        if status == "ok":
            totals[name] = totals.get(name, 0) + (int(size) if size else 0)
    key = lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\\d+)", n)]
    return sorted(totals.items(), key=lambda item: key(item[0]))
''',
    "def summarize(lines):",
    '''
    import random, re
    from logsum import summarize

    def reference(lines):
        totals = {}
        for line in lines:
            timestamp, user, name, size, status = line.split(" ")
            if status == "ok":
                totals[name] = totals.get(name, 0) + (int(size) if size else 0)
        key = lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\\d+)", n)]
        return sorted(totals.items(), key=lambda item: key(item[0]))

    def make(seed):
        rng = random.Random(seed)
        lines = []
        for i in range(rng.randint(8, 20)):
            user = rng.choice(["ann", "bob", "", "cy"])
            name = rng.choice(["img2.png", "img10.png", "img1.png", "doc9.txt", "doc12.txt", "a.txt"])
            size = rng.choice([str(rng.randint(1, 900)), ""])
            status = rng.choice(["ok", "ok", "fail"])
            lines.append(f"10:{i:02d} {user} {name} {size} {status}")
        return lines

    def test_example():
        assert summarize(["10:00 ann a.txt 5 ok", "10:01 bob b.txt 7 ok", "10:02 ann a.txt 1 ok"]) == [("a.txt", 6), ("b.txt", 7)]

    def test_hidden_logs():
        for seed in range(25):
            lines = make(seed)
            assert summarize(list(lines)) == reference(lines), f"seed {seed}: {lines[:4]}"
    ''',
    "# Upload log summary\n\nExploration probe: field parsing and ordering conventions.\n")

REFUND = task(
    "probe-trap-refund-split", "truncation toward zero and remainder sign for negative amounts",
    """
    # Splitting adjustments

    Implement `split(amount_cents, people)` in `/app/split.py`. It divides an account adjustment between people.
    `amount_cents` is an int (negative for chargebacks) and `people` is a non-empty list of names.

    Everyone first receives `amount_cents` divided by the number of people, truncated toward zero. The cents left
    over, which carry the sign of `amount_cents`, are then handed out one cent at a time to the people in list
    order. Return a dict mapping each name to their share in cents.

    Example: `split(1000, ["ana", "ben", "cai"])` returns `{"ana": 334, "ben": 333, "cai": 333}`.
    """,
    "split.py",
    '''    n = len(people)
    base = abs(amount_cents) // n
    extra = abs(amount_cents) - base * n
    sign = -1 if amount_cents < 0 else 1
    return {name: sign * (base + (1 if i < extra else 0)) for i, name in enumerate(people)}
''',
    "def split(amount_cents, people):",
    '''
    import random
    from split import split

    def reference(amount, people):
        n = len(people)
        base = abs(amount) // n
        extra = abs(amount) - base * n
        sign = -1 if amount < 0 else 1
        return {name: sign * (base + (1 if i < extra else 0)) for i, name in enumerate(people)}

    def test_example():
        assert split(1000, ["ana", "ben", "cai"]) == {"ana": 334, "ben": 333, "cai": 333}

    def test_hidden_adjustments():
        rng = random.Random(77)
        names = ["ana", "ben", "cai", "dee", "eli", "fay", "gus"]
        for _ in range(40):
            people = names[: rng.randint(2, 7)]
            amount = rng.choice([-1, 1]) * rng.randint(1, 5000)
            if rng.random() < 0.7:
                amount = -abs(amount)
            assert split(amount, list(people)) == reference(amount, people), f"split({amount}, {people})"
    ''',
    "# Splitting adjustments\n\nExploration probe: dividing signed amounts between people.\n")

PROBES = [INVOICE, SIBLINGS, ERRANDS, LOGS, REFUND]


# ---------------------------------------------------------------- silent, data-visible edge cases

def data_task(slug, summary, instruction, module, solution_body, signature, sample_name, sample_text, tests, readme):
    spec = task(slug, summary, instruction, module, solution_body, signature, tests, readme)
    spec["reference_files"][f"data/{sample_name}"] = sample_text
    return spec


RESENT_SAMPLE = """payment_id,payer,amount_eur,booked_at
P-1001,Acme GmbH,1200.00,2026-09-01
P-1002,Brightline Ltd,450.50,2026-09-01
P-1003,Acme GmbH,99.99,2026-09-02
P-1004,Corvo SA,3000.00,2026-09-03
P-1002,Brightline Ltd,450.50,2026-09-01
P-1005,Brightline Ltd,75.25,2026-09-04
P-1006,Corvo SA,120.00,2026-09-04
P-1004,Corvo SA,3000.00,2026-09-03
P-1007,Acme GmbH,640.00,2026-09-05
"""
RESENT = data_task(
    "probe-silent-resent-payments", "re-sent duplicate payments visible only in the data",
    """
    # Payer totals

    Implement `payer_totals(path)` in `/app/payments.py`. It reads a bank export in the same CSV format as
    `/app/data/export_sample.csv` (columns `payment_id,payer,amount_eur,booked_at`); a payment is identified by its
    `payment_id`. Return a dict mapping each payer to the total amount of their payments, rounded to 2 decimals.

    Example: for an export containing only `P-1,Acme,10.00,2026-01-01` and `P-2,Acme,5.50,2026-01-02`, the result is
    `{"Acme": 15.5}`.
    """,
    "payments.py",
    """    import csv
    seen, totals = set(), {}
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            if row["payment_id"] in seen:
                continue
            seen.add(row["payment_id"])
            totals[row["payer"]] = totals.get(row["payer"], 0) + float(row["amount_eur"])
    return {payer: round(total, 2) for payer, total in totals.items()}
""",
    "def payer_totals(path):",
    "export_sample.csv", RESENT_SAMPLE,
    """
    import csv, random, tempfile
    from pathlib import Path
    from payments import payer_totals

    def reference(rows):
        seen, totals = set(), {}
        for pid, payer, amount, _ in rows:
            if pid in seen:
                continue
            seen.add(pid)
            totals[payer] = totals.get(payer, 0) + float(amount)
        return {payer: round(total, 2) for payer, total in totals.items()}

    def make(seed):
        rng = random.Random(seed)
        payers = ["Acme GmbH", "Brightline Ltd", "Corvo SA", "Dunmore Inc"]
        rows = [(f"P-{2000 + i}", rng.choice(payers), f"{rng.randint(100, 500000) / 100:.2f}", f"2026-09-{1 + i % 28:02d}") for i in range(rng.randint(10, 30))]
        for _ in range(rng.randint(2, 5)):  # the bank re-sends some payments
            rows.insert(rng.randint(0, len(rows)), rng.choice(rows))
        return rows

    def test_example(tmp_path):
        f = tmp_path / "e.csv"
        f.write_text("payment_id,payer,amount_eur,booked_at\\nP-1,Acme,10.00,2026-01-01\\nP-2,Acme,5.50,2026-01-02\\n")
        assert payer_totals(str(f)) == {"Acme": 15.5}

    def test_exports(tmp_path):
        for seed in range(15):
            rows = make(seed)
            f = tmp_path / f"x{seed}.csv"
            f.write_text("payment_id,payer,amount_eur,booked_at\\n" + "".join(",".join(r) + "\\n" for r in rows))
            assert payer_totals(str(f)) == reference(rows), f"seed {seed}"
    """,
    "# Payer totals\n\nExploration probe: totals from a bank export.\n")

ZW = "​"
INVISIBLE_SAMPLE = ("user_id,minutes\n" + "u-anna,30\n" + f"u-ben{ZW},45\n" + "u-anna,15\n" + "u-ben,20\n" + f"{ZW}u-cleo,60\n"
                    + "u-cleo,10\n" + f"u-anna{ZW},5\n" + "u-dev,25\n")
INVISIBLE = data_task(
    "probe-silent-invisible-ids", "zero-width characters in copy-pasted ids",
    """
    # Minutes per user

    Implement `minutes_per_user(path)` in `/app/usage.py`. It reads a CSV in the same format as
    `/app/data/usage_sample.csv` (columns `user_id,minutes`). The user ids were copy-pasted from emails and may contain
    invisible characters; two ids name the same user when they are equal once those characters are removed.
    Return a dict mapping each clean user id to that user's total minutes.

    Example: for rows `u-x,10` and `u-x,5` the result is `{"u-x": 15}`.
    """,
    "usage.py",
    """    import csv
    invisible = dict.fromkeys(map(ord, "\\u200b\\u200c\\u200d\\u2060\\ufeff"))
    totals = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            user = row["user_id"].translate(invisible).strip()
            totals[user] = totals.get(user, 0) + int(row["minutes"])
    return totals
""",
    "def minutes_per_user(path):",
    "usage_sample.csv", INVISIBLE_SAMPLE,
    """
    import random
    from usage import minutes_per_user
    INVIS = ["\\u200b", "\\u200c", "\\u200d", "\\u2060", "\\ufeff"]

    def reference(rows):
        totals = {}
        for user, minutes in rows:
            clean = user.translate(dict.fromkeys(map(ord, "".join(INVIS)))).strip()
            totals[clean] = totals.get(clean, 0) + minutes
        return totals

    def make(seed):
        rng = random.Random(seed)
        rows = []
        for _ in range(rng.randint(12, 30)):
            user = rng.choice(["u-anna", "u-ben", "u-cleo", "u-dev", "u-eve"])
            if rng.random() < 0.35:
                pos = rng.choice([0, len(user)])
                user = user[:pos] + rng.choice(INVIS) + user[pos:]
            rows.append((user, rng.randint(1, 120)))
        return rows

    def test_example(tmp_path):
        f = tmp_path / "e.csv"
        f.write_text("user_id,minutes\\nu-x,10\\nu-x,5\\n", encoding="utf-8")
        assert minutes_per_user(str(f)) == {"u-x": 15}

    def test_usage_files(tmp_path):
        for seed in range(15):
            rows = make(seed)
            f = tmp_path / f"u{seed}.csv"
            f.write_text("user_id,minutes\\n" + "".join(f"{u},{m}\\n" for u, m in rows), encoding="utf-8")
            assert minutes_per_user(str(f)) == reference(rows), f"seed {seed}"
    """,
    "# Minutes per user\n\nExploration probe: aggregating a usage export.\n")

KILO_SAMPLE = """region,amount (k€)
North,12.5
South,8.25
North,3.0
East,40.75
South,1.5
"""
KILO = data_task(
    "probe-silent-header-unit", "the amount unit is stated only in the CSV header",
    """
    # Regional revenue

    Implement `revenue_by_region(path)` in `/app/revenue.py`. It reads a finance export in the same format as
    `/app/data/revenue_sample.csv` and returns a dict mapping each region to its total revenue in euros, as an int.

    Example: for an export whose only data row is `West,0.5`, the result is `{"West": 500}`.
    """,
    "revenue.py",
    """    import csv
    totals = {}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        scale = 1000 if "k€" in header[1] else 1
        for region, amount in reader:
            totals[region] = totals.get(region, 0) + float(amount) * scale
    return {region: round(total) for region, total in totals.items()}
""",
    "def revenue_by_region(path):",
    "revenue_sample.csv", KILO_SAMPLE,
    """
    import random
    from revenue import revenue_by_region

    def make(seed):
        rng = random.Random(seed)
        return [(rng.choice(["North", "South", "East", "West"]), rng.randint(1, 90000) / 1000) for _ in range(rng.randint(5, 20))]

    def test_example(tmp_path):
        f = tmp_path / "e.csv"
        f.write_text("region,amount (k€)\\nWest,0.5\\n", encoding="utf-8")
        assert revenue_by_region(str(f)) == {"West": 500}

    def test_exports(tmp_path):
        for seed in range(15):
            rows = make(seed)
            f = tmp_path / f"r{seed}.csv"
            f.write_text("region,amount (k€)\\n" + "".join(f"{r},{a}\\n" for r, a in rows), encoding="utf-8")
            expected = {}
            for r, a in rows:
                expected[r] = expected.get(r, 0) + a * 1000
            assert revenue_by_region(str(f)) == {r: round(v) for r, v in expected.items()}, f"seed {seed}"
    """,
    "# Regional revenue\n\nExploration probe: totals from a finance export.\n")

SILENT = [RESENT, INVISIBLE, KILO]


# ---------------------------------------------------------------- checklist breadth (many trivial stated details)

RECEIPT_BODY = """    from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
    cent = Decimal("0.01")
    money = lambda d: f"{d.quantize(cent, rounding=ROUND_HALF_UP):,.2f}"
    cap = lambda w: w[:1].upper() + w[1:].lower()
    name = " ".join(cap(w) for w in order["customer"].split(" ") if w)
    year, month, day = order["date"].split("-")
    items = []
    for item in order["items"]:
        if item["qty"] == 0:
            continue
        price = Decimal(item["unit_price"])
        items.append((" ".join(item["name"].split()), item["qty"], price,
                      (price * item["qty"]).quantize(cent, rounding=ROUND_HALF_UP)))
    items.sort(key=lambda it: (-it[3], it[0].lower()))
    lines = [f"RECEIPT for {name}", f"Date: {day}/{month}/{year}"]
    lines += [f"{q} x {n} @ {money(p)} = {money(t)}" for n, q, p, t in items]
    count = sum(q for _, q, _, _ in items)
    subtotal = sum((t for *_, t in items), Decimal("0.00"))
    lines += [f"{count} item" + ("" if count == 1 else "s"), f"Subtotal: {money(subtotal)}"]
    discount = Decimal("0.00")
    if order.get("coupon") == "SAVE10":
        discount = (subtotal * Decimal("0.10")).quantize(cent, rounding=ROUND_DOWN)
        lines.append(f"Discount: -{money(discount)}")
    lines += [f"Total: {money(subtotal - discount)}", "Thank you!"]
    return "\\n".join(lines) + "\\n"
"""
RECEIPT = task(
    "probe-checklist-receipt", "eleven trivial stated formatting rules in one receipt",
    """
    # Receipt formatter

    Implement `format_receipt(order)` in `/app/receipt.py`. It returns the receipt text for one order. An order is a
    dict with `customer` (a string), `date` (`YYYY-MM-DD`), `items` (a list of dicts with `name`, `qty` (int), and
    `unit_price` (a decimal string)), and an optional `coupon`.

    The first line is `RECEIPT for <customer>`, where the first letter of each space-separated word of the customer
    name is upper-cased and the rest of the word lower-cased. The second line is `Date: DD/MM/YYYY`. Then comes one
    line per item, `<qty> x <name> @ <unit price> = <line total>`, where the line total is qty times unit price;
    items with quantity 0 are left out, runs of whitespace inside item names become single spaces, and the item
    lines are ordered by line total from largest to smallest, ties by name ignoring case. Next is `<N> items`, where
    N is the total quantity (write `1 item` when N is 1), then `Subtotal: <amount>`. If the coupon is `SAVE10`, a
    line `Discount: -<amount>` follows, the discount being 10% of the subtotal cut down to whole cents; other coupons
    are ignored. The last two lines are `Total: <amount>` and `Thank you!`.

    All money amounts have two decimals, are rounded half-up to cents, and use commas as thousands separators. The
    text ends with a single newline.

    Example: `format_receipt({"customer": "lena park", "date": "2026-03-04", "items": [{"name": "Tea", "qty": 2,
    "unit_price": "3.50"}]})` returns
    `"RECEIPT for Lena Park\\nDate: 04/03/2026\\n2 x Tea @ 3.50 = 7.00\\n2 items\\nSubtotal: 7.00\\nTotal: 7.00\\nThank you!\\n"`.
    """,
    "receipt.py", RECEIPT_BODY, "def format_receipt(order):",
    "import random\nfrom receipt import format_receipt\n\n\ndef reference(order):\n" + RECEIPT_BODY + textwrap.dedent("""

    def make(seed):
        rng = random.Random(seed)
        first = rng.choice(["anna", "MARY-JANE", "o'neil", "Lena", "jean-luc", "SAM"])
        last = rng.choice(["van der berg", "MCDONALD", "o'brien", "park", "smith-jones"])
        items = []
        for _ in range(rng.randint(2, 6)):
            name = rng.choice(["Tea", "green  tea", "Scone", "scone", " Jam  Jar ", "Butter", "Cake slice"])
            cents = rng.randint(5, 250000)
            price = f"{cents // 1000}.{cents % 1000:03d}" if rng.random() < 0.5 else f"{cents // 100}.{cents % 100:02d}"
            if rng.random() < 0.5 and len(price.split(".")[1]) == 3:
                price = price[:-1] + "5"
            items.append({"name": name, "qty": rng.choice([0, 1, 1, 2, 3, 7]), "unit_price": price})
        order = {"customer": f"{first} {last}", "date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(13, 28):02d}", "items": items}
        if rng.random() < 0.6:
            order["coupon"] = rng.choice(["SAVE10", "SAVE10", "WELCOME5"])
        return order

    def test_example():
        assert format_receipt({"customer": "lena park", "date": "2026-03-04", "items": [{"name": "Tea", "qty": 2, "unit_price": "3.50"}]}) == \\
            "RECEIPT for Lena Park\\nDate: 04/03/2026\\n2 x Tea @ 3.50 = 7.00\\n2 items\\nSubtotal: 7.00\\nTotal: 7.00\\nThank you!\\n"

    def test_hidden_orders():
        for seed in range(25):
            order = make(seed)
            got, exp = format_receipt(order), reference(order)
            assert got == exp, f"seed {seed}: order {order}\\nexpected:\\n{exp}\\ngot:\\n{got}"
    """),
    "# Receipt formatter\n\nExploration probe: a receipt with many small stated formatting rules.\n")
CHECKLIST = [RECEIPT]


def build(spec):
    dest = fa.CANDIDATES / spec["slug"]
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, spec["slug"])
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {"b1": res.get("bug-b1", {}).get("failed", [])}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": spec["slug"], "valid": ok, "ref_failed": res["ref1"]["failed"]}), flush=True)
    if not ok:
        print(res["ref1"]["output"][-1500:], flush=True)
    return ok


if __name__ == "__main__":
    import concurrent.futures
    chosen = SILENT if "--silent" in sys.argv else CHECKLIST if "--checklist" in sys.argv else PROBES
    with concurrent.futures.ThreadPoolExecutor(5) as pool:
        list(pool.map(build, chosen))
