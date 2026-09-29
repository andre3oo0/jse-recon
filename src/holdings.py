"""Reconcile a broker holdings statement against the internal book, valued at independently checked prices."""

import argparse
import csv
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

from src import config, db

FIXTURES = config.ROOT / "fixtures" / "holdings"
EXPORT = FIXTURES / "easyequities_holdings_SYNTHETIC_2026-09-25.csv"
LEDGER = FIXTURES / "internal_ledger_SYNTHETIC.csv"
EXPECTED = FIXTURES / "expected.csv"
PRICE_SOURCE = "yahoo"
SPACES = "    "  # South African statements separate thousands with spaces, often non-breaking ones


def parse_money(text: str) -> float:
    s = (text or "").strip()
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("-").removeprefix("R")
    for ch in SPACES:
        s = s.replace(ch, "")
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) == 2 else s.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise ValueError(f"not an amount: {text!r}")
    return sign * float(s)


def as_of_from(path: Path) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})\.csv$", path.name)
    if not match:
        raise ValueError(f"{path.name} does not end in its statement date, e.g. _2026-09-25.csv")
    return match.group(1)


def resolve(conn: sqlite3.Connection, contract_code: str, isin: str) -> tuple[str, str | None]:
    # ISIN first: it survives renames that change the contract code (TCP became NTU)
    if isin:
        row = conn.execute(
            "SELECT security_id FROM security_master WHERE isin = ? ORDER BY status = 'active' DESC LIMIT 1", (isin,)
        ).fetchone()
        if row:
            return row[0], "isin"
    code = (contract_code or "").removeprefix("EQU.ZA.")
    if code and conn.execute("SELECT 1 FROM security_master WHERE security_id = ?", (code,)).fetchone():
        return code, "code"
    return f"UNMAPPED:{contract_code}", None


def load(conn: sqlite3.Connection, export: Path, ledger: Path, synthetic: bool = True) -> str:
    file_id = hashlib.sha256(export.read_bytes()).hexdigest()
    where = export.resolve()
    shown = where.relative_to(config.ROOT).as_posix() if where.is_relative_to(config.ROOT) else where.as_posix()
    with export.open(encoding="utf-8", newline="") as f:
        lines = list(csv.DictReader(f))
    with ledger.open(encoding="utf-8", newline="") as f:
        txns = list(csv.DictReader(f))

    with conn:
        for table in ("holding_recon_result", "stg_holding", "raw_holding"):
            conn.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM holding_file WHERE file_id = ?", (file_id,))
        conn.execute(
            "INSERT INTO holding_file (file_id, source, as_of, path, lines, synthetic) VALUES (?, 'easyequities', ?, ?, ?, ?)",
            (file_id, as_of_from(export), shown, len(lines), int(synthetic)),
        )
        conn.executemany(
            "INSERT INTO raw_holding VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(file_id, n, r["name"], r["contract_code"], r["purchase_value"], r["current_value"],
              r["current_price"], r["isin"]) for n, r in enumerate(lines, start=1)],
        )
        conn.execute("DELETE FROM ledger_txn")
        conn.executemany(
            "INSERT INTO ledger_txn VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(t["reference"], t["trade_date"], t["security_id"], t["side"], float(t["quantity"]),
              float(t["price_zar"]), float(t["fees_zar"])) for t in txns],
        )
    stage(conn, file_id)
    return file_id


def stage(conn: sqlite3.Connection, file_id: str) -> None:
    rows = []
    for line_no, code, isin, *amounts in conn.execute(
        "SELECT line_no, contract_code, isin, purchase_value, current_value, current_price FROM raw_holding "
        "WHERE file_id = ? ORDER BY line_no",
        (file_id,),
    ).fetchall():
        parsed, errors = [], []
        for field, text in zip(("purchase_value", "current_value", "current_price"), amounts):
            try:
                parsed.append(parse_money(text))
            except ValueError:
                parsed.append(None)
                errors.append(f"{field} {text!r}")
        key_id, mapped_by = resolve(conn, code, isin)
        rows.append((file_id, line_no, key_id, mapped_by, code, isin or None, *parsed, "; ".join(errors) or None))
    with conn:
        conn.execute("DELETE FROM stg_holding WHERE file_id = ?", (file_id,))
        conn.executemany("INSERT INTO stg_holding VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def previous_trading_day(conn: sqlite3.Connection, day: str) -> str:
    return conn.execute(
        "SELECT MAX(cal_date) FROM trading_calendar WHERE is_trading_day = 1 AND cal_date < ?", (day,)
    ).fetchone()[0]


def reconcile(conn: sqlite3.Connection, file_id: str) -> None:
    rules = config.tolerance_rules()
    h = rules["holdings_recon"]["easyequities_vs_internal"]
    band = rules["dq"]["unit_ratio_band"]
    as_of = conn.execute("SELECT as_of FROM holding_file WHERE file_id = ?", (file_id,)).fetchone()[0]
    with conn:
        conn.execute("DELETE FROM holding_recon_result WHERE file_id = ?", (file_id,))
        conn.execute(
            (config.SQL_DIR / "05_holdings_recon.sql").read_text(encoding="utf-8"),
            {
                "file_id": file_id, "as_of": as_of, "prev_day": previous_trading_day(conn, as_of),
                "price_source": PRICE_SOURCE, "settle_days": h["settlement_days"],
                "pos_floor": h["position_value"]["abs_floor_zar"], "pos_pct": h["position_value"]["rel_pct"],
                "px_floor": h["price"]["abs_floor_zar"], "px_pct": h["price"]["rel_pct"],
                "unit_lo": band[0], "unit_hi": band[1],
            },
        )


def latest_file(conn: sqlite3.Connection) -> tuple[str, str, int] | None:
    return conn.execute(
        "SELECT file_id, as_of, synthetic FROM holding_file ORDER BY as_of DESC, loaded_at DESC LIMIT 1"
    ).fetchone()


def results(conn: sqlite3.Connection, file_id: str) -> dict[str, str]:
    return dict(conn.execute(
        "SELECT key_id, status FROM holding_recon_result WHERE file_id = ? ORDER BY key_id", (file_id,)
    ).fetchall())


def misstatement(conn: sqlite3.Connection, file_id: str) -> tuple[float, float]:
    net, gross = conn.execute(
        "SELECT COALESCE(SUM(value_diff), 0), COALESCE(SUM(ABS(value_diff)), 0) FROM holding_recon_result "
        "WHERE file_id = ?",
        (file_id,),
    ).fetchone()
    return net, gross


def report(conn: sqlite3.Connection, file_id: str) -> None:
    as_of, synthetic, lines = conn.execute(
        "SELECT as_of, synthetic, lines FROM holding_file WHERE file_id = ?", (file_id,)
    ).fetchone()
    rows = conn.execute(
        "SELECT key_id, status, value_diff, explanation FROM holding_recon_result WHERE file_id = ? "
        "ORDER BY status = 'MATCH', ABS(value_diff) DESC",
        (file_id,),
    ).fetchall()
    breaks = [r for r in rows if r[1] != "MATCH"]
    book = conn.execute(
        "SELECT SUM(book_value) FROM holding_recon_result WHERE file_id = ?", (file_id,)
    ).fetchone()[0] or 0
    net, gross = misstatement(conn, file_id)
    unvalued = [r[0] for r in rows if r[2] is None]
    label = "SYNTHETIC statement" if synthetic else "Statement"
    print(f"Holdings: {label} as of {as_of}, {lines} lines, {len(rows)} positions: "
          f"{len(rows) - len(breaks)} agree, {len(breaks)} breaks")
    if book:
        # Netting lets a duplicate hide a missing position, so the gross figure is the one to act on
        print(f"  Against the book at our prices the statement is out by R{net:+,.2f} net ({net / book:+.2%}), "
              f"R{gross:,.2f} gross ({gross / book:.2%})"
              + (f"; could not value {', '.join(unvalued)}" if unvalued else ""))
    for key, status, diff, why in breaks:
        amount = "unknown" if diff is None else f"R{diff:+,.2f}"
        print(f"    {key:<10} {status:<7} {amount:>13}  {why}")


def check_expected(conn: sqlite3.Connection, file_id: str, expected: Path) -> list[str]:
    with expected.open(encoding="utf-8", newline="") as f:
        want = {r["key_id"]: r["status"] for r in csv.DictReader(f)}
    got = results(conn, file_id)
    return [f"{k}: expected {want.get(k)}, got {got.get(k)}" for k in sorted(set(want) | set(got)) if want.get(k) != got.get(k)]


def run(conn: sqlite3.Connection, export: Path = EXPORT, ledger: Path = LEDGER) -> str:
    file_id = load(conn, export, ledger, synthetic="SYNTHETIC" in export.name)
    reconcile(conn, file_id)
    return file_id


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--export", type=Path, default=EXPORT)
    p.add_argument("--ledger", type=Path, default=LEDGER)
    p.add_argument("--check-expected", type=Path, nargs="?", const=EXPECTED,
                   help="fail unless every position gets the status in this CSV (defaults to the fixture's)")
    args = p.parse_args(argv)

    conn = db.connect()
    db.apply_schema(conn)
    file_id = run(conn, args.export, args.ledger)
    report(conn, file_id)
    if args.check_expected:
        mismatches = check_expected(conn, file_id, args.check_expected)
        print(f"  Checked against {args.check_expected.name}: {len(mismatches)} mismatches")
        for m in mismatches:
            print(f"    {m}")
        return 1 if mismatches else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
