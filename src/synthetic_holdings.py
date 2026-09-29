"""Generate the synthetic EasyEquities statement and internal ledger, from real closes, with planted breaks."""

import csv
import random
import sys
import tempfile
from pathlib import Path

from src import db, holdings

AS_OF = "2026-09-25"
PREV = "2026-09-23"  # the 24th was Heritage Day
SEED = 20260925
PORTFOLIO = ["ABG", "BHG", "CPI", "FSR", "GFI", "GRT", "MTN", "NPN", "SBK", "SHP", "SOL", "VOD"]
# NTU (held under the old TCP code at the broker, matched by ISIN) joins once the warehouse has NTU closes
BROKER_ONLY = "CLS"
# Only ISINs confirmed against a source; the rest are left blank rather than invented
ISINS = {
    "SOL": "ZAE000006896",  # AFX page for JSE:SOL
    "SBK": "ZAE000109815",  # AFX page for JSE:SBK
    "GFI": "ZAE000018123",  # AFX page for JSE:GFI
    "HAR": "ZAE000015228",  # AFX page for JSE:HAR
    "NTU": "ZAE000167391",  # JSE market notice on the Transaction Capital rename; AFX agrees
}
FEE_RATE = 0.0035  # illustrative brokerage and costs, not EasyEquities' actual tariff
EXPECTED = {
    "ABG": "SETTLE", "BHG": "UNIT", "CPI": "MATCH", "FSR": "ONE_A", "GFI": "MATCH", "GRT": "PARSE",
    "MTN": "DUP", "NPN": "QTY", "SBK": "MATCH", "SHP": "STALE", "SOL": "MATCH", "VOD": "MATCH",
    BROKER_ONLY: "ONE_B",
}


def money(x: float, thousands: str = " ") -> str:
    return "R" + f"{x:,.2f}".replace(",", thousands)


def build(conn):
    closes = {(s, d): c for s, d, c in conn.execute(
        """
        SELECT security_id, price_date, close_zar FROM (
            SELECT security_id, price_date, close_zar, unit_anomaly,
                   ROW_NUMBER() OVER (PARTITION BY security_id, price_date ORDER BY snapshot_date DESC) AS latest
            FROM stg_price WHERE source = 'yahoo'
        )
        WHERE latest = 1 AND unit_anomaly IS NULL
        """
    )}
    days = sorted({d for (_, d) in closes if "2024-01-02" <= d <= "2026-08-31"})
    names = dict(conn.execute("SELECT security_id, name FROM security_master"))
    rng = random.Random(SEED)
    txns, ref = [], 0

    def trade(day, sec, side, qty):
        nonlocal ref
        ref += 1
        price = closes[(sec, day)]
        txns.append({"reference": f"T{ref:04d}", "trade_date": day, "security_id": sec, "side": side,
                     "quantity": f"{qty:.4f}", "price_zar": f"{price:.2f}", "fees_zar": f"{qty * price * FEE_RATE:.2f}"})

    held, cost = {}, {}
    for sec in PORTFOLIO:
        for day in sorted(rng.sample([d for d in days if (sec, d) in closes], rng.randint(1, 3))):
            qty = round(rng.uniform(2000, 25000) / closes[(sec, day)], 4)  # EasyEquities sells fractional shares
            trade(day, sec, "BUY", qty)
            held[sec] = held.get(sec, 0) + qty
            cost[sec] = cost.get(sec, 0) + qty * closes[(sec, day)] * (1 + FEE_RATE)
    sell = round(held["SBK"] * 0.3, 4)
    trade(max(d for d in days if (d, "SBK") and ("SBK", d) in closes), "SBK", "SELL", sell)
    cost["SBK"] *= (held["SBK"] - sell) / held["SBK"]
    held["SBK"] -= sell
    trade(PREV, "ABG", "BUY", round(5000 / closes[("ABG", PREV)], 4))  # settles T+3 on the 29th, after the statement

    lines = []
    for sec in PORTFOLIO + [BROKER_ONLY]:
        if sec == "FSR":
            continue  # sold at the broker, never booked internally
        if sec == BROKER_ONLY:
            qty = round(rng.uniform(5000, 15000) / closes[(sec, AS_OF)], 4)  # transferred in, never booked
            cost[sec] = qty * closes[(sec, AS_OF)] * 0.9
        else:
            qty = held[sec] - (0.5 if sec == "NPN" else 0)  # NPN: half a share sold at the broker only
        price = closes[(sec, PREV if sec == "SHP" else AS_OF)]
        value = round(qty * price, 2) + (0.40 if sec == "CPI" else 0)  # CPI: inside the R1.00 tolerance
        line = {
            "name": "Transaction Capital" if sec == "NTU" else names[sec],
            "contract_code": "EQU.ZA.TCP" if sec == "NTU" else f"EQU.ZA.{sec}",
            "purchase_value": money(cost[sec]),
            "current_value": money(value, " " if sec == "VOD" else " "),
            "current_price": money(price * 100 if sec == "BHG" else price),
            "isin": ISINS.get(sec, ""),
        }
        if sec == "GRT":
            line["current_value"] = "R—"
        lines.append(line)
        if sec == "MTN":
            lines.append(dict(line))
    lines.sort(key=lambda r: r["name"])
    txns.sort(key=lambda t: (t["trade_date"], t["reference"]))
    return lines, txns


def write(lines, txns, export: Path, ledger: Path, expected: Path) -> None:
    for path, rows in ((export, lines), (ledger, txns)):
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    with expected.open("w", encoding="utf-8", newline="") as f:
        f.write("key_id,status\n" + "".join(f"{k},{v}\n" for k, v in sorted(EXPECTED.items())))


def main() -> int:
    conn = db.connect()
    db.apply_schema(conn)
    lines, txns = build(conn)
    with tempfile.TemporaryDirectory() as tmp:
        # Prove the planted outcomes on a scratch copy before overwriting the committed fixtures
        t = Path(tmp)
        export, ledger, expected = t / holdings.EXPORT.name, t / holdings.LEDGER.name, t / holdings.EXPECTED.name
        write(lines, txns, export, ledger, expected)
        file_id = holdings.load(conn, export, ledger)
        holdings.reconcile(conn, file_id)
        mismatches = holdings.check_expected(conn, file_id, expected)
    if mismatches:
        print("Planted outcomes did not hold; fixtures not written:", *mismatches, sep="\n  ")
        return 1
    holdings.FIXTURES.mkdir(parents=True, exist_ok=True)
    write(lines, txns, holdings.EXPORT, holdings.LEDGER, holdings.EXPECTED)
    print(f"Wrote {len(lines)} statement lines and {len(txns)} ledger trades to {holdings.FIXTURES}; all "
          f"{len(EXPECTED)} planted outcomes hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
