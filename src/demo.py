"""Run the whole pipeline on SYNTHETIC prices with planted errors, so anyone can reproduce it without vendor data."""

import argparse
import contextlib
import json
import random
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pandas as pd

from src import answers, config, db, holdings, ingest, lifecycle, recon, report, security_master, staging
from src import trading_calendar
from src.sources.base import PRICE_COLUMNS, PriceSource

DEMO_DIR = config.DATA_DIR / "demo"
SAMPLE = config.ROOT / "docs" / "sample" / "break_report_SYNTHETIC.xlsx"
SEED = 20260911
FIRST, LAST = date(2026, 8, 3), date(2026, 9, 11)
SNAPSHOTS = {"2026-09-10": "2026-09-10T18:30:00+00:00", "2026-09-11": "2026-09-11T18:30:00+00:00"}
LATE_DAY = "2026-09-10"  # side A publishes this session a day late
HOLIDAY = "2026-08-10"  # National Women's Day, observed on the Monday

NAMES = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa", "Lambda", "Mu"]
CODES = ["SYA", "SYB", "SYC", "SYD", "SYE", "SYF", "SYG", "SYH", "SYI", "SYJ", "SYK", "SYL"]
SECTORS = ["Financials", "Basic Materials", "Technology", "Consumer Staples"]
UNIVERSE = {"securities": [
    {"security_id": c, "name": f"Synthetic {n} Ltd (fictional)", "sector": SECTORS[i % 4]}
    for i, (c, n) in enumerate(zip(CODES, NAMES))
]}
WEIGHTS = [{"as_at": "2025-12-31", "basis": "SYNTHETIC weights", "source": "SYNTHETIC",
            "securities": {c: w for c, w in zip(CODES, [0.20, 0.15, 0.12, 0.10, 0.08, 0.08,
                                                          0.07, 0.06, 0.05, 0.04, 0.03, 0.02])}}]

# What the planted errors must produce, and the traps that must not raise an alarm
PLANTED = {
    "unit_error": ("SYC", "2026-08-20"),  # side A prints 1/100 of the price for one day
    "unexplained_jump": ("SYD", "2026-08-25"),  # +40% on company news, for an analyst to verify
    "frozen": ("SYE", "2026-08-12"),  # side A repeats one close with no volume for six sessions
    "carried_forward": (["SYF", "SYG", "SYH"], "2026-08-27"),  # side B repeats the previous close
    "scale_from": ("SYI", "2026-09-01"),  # side B reports another instrument, 1/160 of the price
    "closed_day": ("SYJ", HOLIDAY),  # side B has a bar on a public holiday
    "consolidation": ("SYK", "2026-08-17"),  # a real 10-to-1 consolidation on both sides
    "restated": ("SYB", "2026-09-01"),  # side A's second snapshot rewrites one close by 2%
    "under_tolerance": ("SYL", "2026-08-28"),  # side B differs by 0.02%, inside tolerance
    "market_fall": "2026-09-03",  # every share falls 10%: not an exception
    "late_day_fall": ("SYA", LATE_DAY),  # the largest holding falls 8% on the day side A publishes late
}


class SyntheticSource(PriceSource):
    def __init__(self, name: str, suffix: str):
        self.name, self.suffix = name, suffix

    def vendor_symbol(self, security_id: str) -> str:
        return f"{security_id}{self.suffix}"

    def fetch(self, symbols, period):
        raise NotImplementedError("synthetic prices are generated, not fetched")


SOURCES = {"synthetic_a": SyntheticSource("synthetic_a", ".A"), "synthetic_b": SyntheticSource("synthetic_b", ".B")}


def settings() -> dict:
    return {"synthetic_a": {"suffix": ".A", "period": "SYNTHETIC"}, "synthetic_b": {"suffix": ".B", "period": "SYNTHETIC"},
            "recon_pairs": [["synthetic_a", "synthetic_b"]]}


def tolerances() -> dict:
    real = config.load_yaml("tolerance_rules.yaml")
    recons = real["price_recon"]
    real["price_recon"] = recons | {"synthetic_a_vs_synthetic_b": recons["yahoo_vs_eodhd"],
                                    "synthetic_a_restatement": recons["yahoo_restatement"],
                                    "synthetic_b_restatement": recons["yahoo_restatement"]}
    return real


@contextlib.contextmanager
def synthetic(directory: Path = DEMO_DIR):
    # The pipeline reads its settings from config; point every setting at the synthetic world for the duration
    with contextlib.ExitStack() as stack:
        for target, attr, value in [
            (config, "ROOT", directory),  # landing paths are recorded relative to this
            (config, "LANDING_DIR", directory / "landing"),
            (config, "DB_PATH", directory / "warehouse.db"),
            (config, "universe", lambda: UNIVERSE),
            (config, "sources", settings),
            (config, "tolerance_rules", tolerances),
            (config, "reference_weights", lambda: WEIGHTS),
            (holdings, "EXPORT", directory / "no_statement.csv"),
            (ingest, "SOURCES", {n: (lambda s=s: s) for n, s in SOURCES.items()}),
        ]:
            stack.enter_context(mock.patch.object(target, attr, value))
        yield


def true_prices() -> dict[str, dict[str, float]]:
    rng = random.Random(SEED)
    days = [d for d, is_open, _ in trading_calendar.build(FIRST, LAST) if is_open]
    prices = {}
    for i, code in enumerate(CODES):
        price, series = 1000.0 * (i + 1), {}
        for day in days:
            price *= 1 + rng.uniform(-0.01, 0.01)
            if day == PLANTED["market_fall"]:
                price *= 0.90
            if code == PLANTED["unexplained_jump"][0] and day == PLANTED["unexplained_jump"][1]:
                price *= 1.40
            if code == PLANTED["consolidation"][0] and day == PLANTED["consolidation"][1]:
                price *= 10
            if (code, day) == PLANTED["late_day_fall"]:
                price *= 0.92
            series[day] = round(price, 2)
        prices[code] = series
    return prices


def bars(side: str, snapshot: str, truth: dict[str, dict[str, float]]) -> pd.DataFrame:
    rows, suffix = [], SOURCES[side].suffix
    for code, series in truth.items():
        days = sorted(series)
        for n, day in enumerate(days):
            if day > snapshot or (side == "synthetic_a" and snapshot == LATE_DAY and day == LATE_DAY):
                continue
            close, volume = series[day], 100000.0 + 1000 * n
            if side == "synthetic_a":
                if (code, day) == PLANTED["unit_error"]:
                    close = round(close / 100, 4)
                frozen_code, frozen_from = PLANTED["frozen"]
                if code == frozen_code and frozen_from <= day and days.index(day) < days.index(frozen_from) + 6:
                    close, volume = series[frozen_from], (volume if day == frozen_from else 0.0)
                if snapshot > LATE_DAY and (code, day) == PLANTED["restated"]:
                    close = round(close * 1.02, 2)
            else:
                codes, carried = PLANTED["carried_forward"]
                if code in codes and day == carried:
                    close = series[days[n - 1]]
                if code == PLANTED["scale_from"][0] and day >= PLANTED["scale_from"][1]:
                    close, volume = round(series[day] / 160, 4), 500.0 + n
                if (code, day) == PLANTED["under_tolerance"]:
                    close = round(close * 1.0002, 4)
            rows.append((f"{code}{suffix}", day, close, close, close, close, close, volume, 0.0, 0.0, "ZAc"))
        if side == "synthetic_b" and code == PLANTED["closed_day"][0]:
            day_before = max(d for d in days if d < HOLIDAY)
            rows.append((f"{code}{suffix}", HOLIDAY, *[series[day_before]] * 5, 50000.0, 0.0, 0.0, "ZAc"))
    return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def land(side: str, snapshot: str, prices: pd.DataFrame) -> None:
    out = ingest.landing_dir(side, snapshot)
    out.mkdir(parents=True, exist_ok=True)
    path = out / ingest.PRICES_FILE
    ingest.write_prices(prices, path)
    fetched = datetime.fromisoformat(SNAPSHOTS[snapshot])
    manifest = {
        "run_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic/{side}/{snapshot}")),
        "source": side,
        "snapshot_date": snapshot,
        "fetched_at": fetched.isoformat(timespec="seconds"),
        "session_cutoff": ingest.session_cutoff(snapshot, fetched),
        "lookback_period": "SYNTHETIC",
        "rows": len(prices),
        "excluded_incomplete_session_rows": 0,
        "sha256": ingest.sha256(path),
        "synthetic": True,
        "symbols": [{"vendor_symbol": s, "status": "ok", "row_count": int(n), "reported_unit": "ZAc", "error": None,
                     "isin": None} for s, n in prices.groupby("vendor_symbol").size().items()],
    }
    (out / ingest.MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")


def build(directory: Path = DEMO_DIR, sample: Path | None = SAMPLE) -> list[str]:
    with synthetic(directory):
        config.DB_PATH.unlink(missing_ok=True)
        truth = true_prices()
        for snapshot in SNAPSHOTS:
            for side in SOURCES:
                land(side, snapshot, bars(side, snapshot, truth))
        conn = db.connect()
        db.apply_schema(conn)
        security_master.sync(conn, list(SOURCES.values()))
        for path in sorted(config.LANDING_DIR.glob(f"*/*/{ingest.MANIFEST_FILE}")):
            ingest.load(conn, path.parent / ingest.PRICES_FILE, json.loads(path.read_text(encoding="utf-8")))
        staging.build(conn)
        recon.replay(conn)
        lifecycle.build(conn)
        lines = []
        for source in answers.feeds_under_test(conn):
            lines += ["SYNTHETIC DATA: fictional securities and vendors, with planted errors.", *answers.answers(conn, source), ""]
        if sample:
            sample.parent.mkdir(parents=True, exist_ok=True)
            report.build(conn, sample)
        conn.close()
        return lines


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true",
                   help="also recalculate the sample report in LibreOffice and check it against the demo warehouse")
    args = p.parse_args(argv)
    print("\n".join(build()))
    print(f"Wrote {SAMPLE.relative_to(config.ROOT)}")
    if args.check:
        from src import report_check  # needs LibreOffice, so only imported when asked for
        with synthetic():
            return report_check.main([str(SAMPLE), "--against-warehouse"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
