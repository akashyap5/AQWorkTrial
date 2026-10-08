"""Exploration probe (Claude-authored, NOT shipped): silent partial export + output swamping.

The provided `ledgerctl export` exits 0 and ends with "export complete: status OK", but by default skips
rows that are not ASCII-encodable and excludes archived accounts. Both defaults and the options that change
them are documented in `ledgerctl --help`; skipped rows are logged; `ledgerctl stats` reports true counts.
Grading checks the final files only (exact content hash and totals embedded in the hidden tests).
"""
import hashlib
import json
import random
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator import fast_author as fa  # noqa: E402

ASCII_PAYEES = ["Northwind Supply", "Blue Harbor Freight", "Granite Office", "Pioneer Telecom", "Summit Utilities",
                "Atlas Catering", "Copperline Print", "Redwood Logistics", "Lakeside Rentals", "Orbit Software"]
NON_ASCII_PAYEES = ["Café Müller", "Søren & Søn", "Łódź Transport", "Crème Atelier", "Zürich Büro",
                    "José Peña SL", "Ætna Nordic", "Kraków Druk"]


def make_rows(seed=20261007, n=4000):
    rng = random.Random(seed)
    accounts = [f"ACC{i:03d}" for i in range(1, 41)]
    archived = set(rng.sample(accounts, 5))
    rows = []
    for i in range(1, n + 1):
        acct = rng.choice(accounts)
        payee = rng.choice(NON_ASCII_PAYEES) if rng.random() < 0.04 else rng.choice(ASCII_PAYEES)
        cur = rng.choice(["EUR", "USD", "GBP"])
        amount = Decimal(rng.randint(-250000, 900000)) / 100
        day = rng.randint(1, 30)
        rows.append({"txn_id": f"T{i:05d}", "account": acct, "payee": payee, "currency": cur,
                     "amount": f"{amount:.2f}", "posted_at": f"2026-09-{day:02d}"})
    return rows, sorted(archived)


LEDGERCTL = r'''#!/usr/bin/env python3
"""ledgerctl - ledger maintenance tool.

Usage:
  ledgerctl stats                      show row counts (all accounts, including archived)
  ledgerctl export --out FILE [options]
                                       export transactions as CSV
                                       (header: txn_id,account,payee,currency,amount,posted_at)

Export options:
  --encoding NAME      output encoding (default: ascii). Rows that cannot be encoded
                       in the chosen encoding are SKIPPED and logged as warnings.
  --include-archived   include transactions of archived accounts (default: excluded)
  --log FILE           log file (default: /app/logs/ledgerctl.log)

Exit status is 0 when the export finishes, even if rows were skipped or excluded;
check the log or compare with `ledgerctl stats`.
"""
import argparse, csv, sqlite3, sys

DB = "/app/data/ledger.db"

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__); return 0
    p = argparse.ArgumentParser(prog="ledgerctl", add_help=False)
    p.add_argument("cmd")
    p.add_argument("--out")
    p.add_argument("--encoding", default="ascii")
    p.add_argument("--include-archived", action="store_true")
    p.add_argument("--log", default="/app/logs/ledgerctl.log")
    a = p.parse_args()
    con = sqlite3.connect(DB)
    if a.cmd == "stats":
        total = con.execute("select count(*) from txns").fetchone()[0]
        arch = con.execute("select count(*) from txns t join accounts c on t.account=c.account where c.archived=1").fetchone()[0]
        print(f"transactions: {total} (archived accounts: {arch})"); return 0
    if a.cmd != "export" or not a.out:
        print(__doc__); return 2
    q = "select t.txn_id,t.account,t.payee,t.currency,t.amount,t.posted_at from txns t join accounts c on t.account=c.account"
    if not a.include_archived:
        q += " where c.archived=0"
    q += " order by t.txn_id"
    log = open(a.log, "a", encoding="utf-8")
    written = skipped = 0
    with open(a.out, "w", newline="", encoding=a.encoding, errors="strict") as fh:
        w = csv.writer(fh)
        w.writerow(["txn_id", "account", "payee", "currency", "amount", "posted_at"])
        for k, row in enumerate(con.execute(q)):
            if k % 10 == 0:
                print(f"DEBUG ledgerctl.export batch={k // 10} cursor=ok buffer=flush")
            print(f"INFO  ledgerctl.export row={row[0]} account={row[1]} status=queued")
            try:
                row[2].encode(a.encoding)
            except UnicodeEncodeError:
                skipped += 1
                msg = f"WARN  ledgerctl.export row={row[0]} skipped: payee not encodable as {a.encoding}"
                print(msg); log.write(msg + "\n")
                continue
            w.writerow(row); written += 1
    for k in range(400):
        print(f"DEBUG ledgerctl.export finalize index={k} checksum=deferred")
    log.write(f"export done written={written} skipped={skipped} include_archived={a.include_archived}\n")
    print("export complete: status OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''


def build(slug="probe-silent-export"):
    rows, archived = make_rows()
    full = sorted(rows, key=lambda r: r["txn_id"])
    header = "txn_id,account,payee,currency,amount,posted_at"
    csv_text = header + "\n" + "\n".join(",".join(r[k] for k in header.split(",")) for r in full) + "\n"
    totals = {}
    for r in full:
        totals[r["currency"]] = totals.get(r["currency"], Decimal("0")) + Decimal(r["amount"])
    totals_json = json.dumps({k: f"{v:.2f}" for k, v in sorted(totals.items())}, indent=1) + "\n"
    expected_ids = [r["txn_id"] for r in full]
    content_hash = hashlib.sha256("\n".join(",".join(r[k] for k in header.split(",")) for r in full).encode()).hexdigest()
    # Sensitivity: the default export loses rows (non-ASCII payees and archived accounts).
    default_rows = [r for r in full if r["account"] not in archived and all(ord(ch) < 128 for ch in r["payee"])]
    print(json.dumps({"rows": len(full), "default_export_rows": len(default_rows), "archived_accounts": archived,
                      "non_ascii_rows": sum(1 for r in full if any(ord(ch) > 127 for ch in r["payee"]))}))
    build_assets = textwrap.dedent(f'''
        import json, os, sqlite3
        ROWS = {json.dumps(rows)!r}
        ARCHIVED = {json.dumps(archived)!r}
        rows = json.loads(ROWS); archived = set(json.loads(ARCHIVED))
        os.makedirs("../data", exist_ok=True); os.makedirs("../logs", exist_ok=True); os.makedirs("../out", exist_ok=True)
        con = sqlite3.connect("../data/ledger.db")
        con.execute("create table accounts(account text primary key, archived int)")
        con.execute("create table txns(txn_id text primary key, account text, payee text, currency text, amount text, posted_at text)")
        for a in sorted({{r["account"] for r in rows}}):
            con.execute("insert into accounts values (?,?)", (a, 1 if a in archived else 0))
        con.executemany("insert into txns values (?,?,?,?,?,?)",
                        [(r["txn_id"], r["account"], r["payee"], r["currency"], r["amount"], r["posted_at"]) for r in rows])
        con.commit(); con.close()
        os.chmod("../tools/ledgerctl", 0o755)
    ''')
    tests = textwrap.dedent(f'''
        import csv, hashlib, json
        from pathlib import Path
        EXPECTED_IDS = {expected_ids!r}
        CONTENT_SHA256 = "{content_hash}"
        TOTALS = {json.loads(totals_json)!r}
        HEADER = "{header}"

        def _rows():
            p = Path("/app/out/transactions.csv")
            assert p.exists(), "missing /app/out/transactions.csv"
            with open(p, newline="", encoding="utf-8") as fh:
                r = list(csv.reader(fh))
            assert r and ",".join(r[0]) == HEADER, "unexpected header"
            return r[1:]

        def test_all_transactions_present():
            ids = sorted(row[0] for row in _rows())
            missing = sorted(set(EXPECTED_IDS) - set(ids))
            assert not missing, f"{{len(missing)}} transactions missing, e.g. {{missing[:5]}}"
            assert len(ids) == len(EXPECTED_IDS), "duplicate or extra rows"

        def test_exact_content():
            rows = sorted(_rows(), key=lambda r: r[0])
            digest = hashlib.sha256("\\n".join(",".join(r) for r in rows).encode()).hexdigest()
            assert digest == CONTENT_SHA256, "exported rows differ from the ledger"

        def test_totals():
            p = Path("/app/out/totals.json")
            assert p.exists(), "missing /app/out/totals.json"
            got = json.loads(p.read_text())
            assert {{k: str(v) for k, v in got.items()}} == TOTALS
    ''')
    instruction = ("# Ledger export\n\nFinance needs this month's ledger extract. Export all ledger transactions (every "
                   "account, including archived accounts) to `/app/out/transactions.csv` using the ledger tooling in "
                   "`/app/tools` (keep the tool's CSV header and columns), then write the total amount per currency to "
                   "`/app/out/totals.json` as a JSON object mapping currency code to a decimal string with two places, "
                   "e.g. `{\"EUR\": \"1234.50\"}`.\n")
    spec = {"slug": slug, "summary": "Export a ledger with a tool that silently skips rows by default and total it per currency",
            "family": "exploration-probe", "instruction_md": instruction,
            "readme_md": "# Ledger export\n\nExploration probe: a tool that exits 0 while silently dropping rows by default (documented in --help); output swamping hides the warnings.\n",
            "reference_files": {"tools/ledgerctl": LEDGERCTL, "out/transactions.csv": csv_text, "out/totals.json": totals_json},
            "fixtures": {"fixtures/build_assets.py": build_assets},
            "tests_py": tests,
            "bugs": [{"id": "b1", "file": "out/transactions.csv", "find": csv_text.rstrip("\n"), "replace": "",
                      "trap": "every transaction must be exported", "class": "silent partial export"},
                     {"id": "b2", "file": "out/totals.json", "find": totals_json.rstrip("\n"), "replace": "",
                      "trap": "totals over all transactions", "class": "silent partial export"}],
            "prompt_version": "claude-exploration-probe",
            "authoring_note": "Claude-authored exploration probe; excluded from the shipped set and yield."}
    dest = fa.CANDIDATES / slug
    fa.write_task(spec, spec["bugs"], dest)
    res = fa.validate(spec, spec["bugs"], dest, slug)
    ok = all(res[k]["ok"] for k in ("ref1", "ref2", "ref3")) and not res["starter"]["ok"]
    spec["bug_tests"] = {b["id"]: res.get(f"bug-{b['id']}", {}).get("failed", []) for b in spec["bugs"]}
    fa.write_task(spec, spec["bugs"], dest)
    print(json.dumps({"slug": slug, "valid": ok, "ref_failed": res["ref1"]["failed"], "starter_failed": res["starter"]["failed"]}))
    if not ok:
        print(res["ref1"]["output"][-2500:])


if __name__ == "__main__":
    build()
