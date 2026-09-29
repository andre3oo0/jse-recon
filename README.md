# jse-recon

A price and holdings reconciliation tool for JSE equities, modelled on
the daily reconciliation control fund administrators run. Two
independent records of the same securities are matched in SQL, and every
disagreement is classified, aged and costed in NAV basis points.

**Status:** ingest running, recon engine in progress. See
[docs/plan.md](docs/plan.md).

## What the data showed on day one

Before any matching logic existed, profiling the first Yahoo Finance
snapshot found:

- **Rand prices inside a cents series.** On 2025-01-10 and 2025-04-25,
  ten securities (including Standard Bank, Vodacom and Sanlam) carry a
  single bar about 1/100th of its neighbours. A NAV struck from that feed
  on 2025-01-10 would have been understated by **6.54%**, 654 times a
  1bp restatement threshold.
- **A missing session.** Monday 28 September 2026 is absent from every
  lookback, and one source cannot say whether the market or the vendor
  is at fault.
- **Five dead codes, all for real reasons**: one rename (Transaction
  Capital to Nutun) and four delistings. Alpha codes change; ISINs
  don't.

Details and method: [docs/findings.md](docs/findings.md).

## Quick start

Requires Python 3.10+ and SQLite 3.39+ (bundled with recent Python).

```bash
pip install -r requirements.txt
python -m src.ingest --period 5y     # first run: backfill
python -m src.ingest                 # daily, after 17:30 SAST
python -m unittest discover -s tests -t .
```

Each run lands `data/landing/<source>/<date>/prices.csv` with a
manifest, loads it into `data/warehouse.db`, and prints a coverage
report listing any code that returned no data.

## Layout

```
config/     universe.yaml (111 JSE codes), tolerance_rules.yaml
sql/        schema and, next, the recon SQL
src/        ingest, security master, vendor adapters
tests/      ingest guarantees; seeded break suite to follow
docs/       plan, findings, EasyEquities export notes
```

## Design in brief

- **Landing is immutable.** Re-running a day reloads what was recorded
  rather than fetching again.
- **Raw is faithful.** No deduplication on load, so vendor duplicates
  surface as breaks.
- **Adapters are swappable.** The recon only sees a fixed column
  contract, so adding a vendor never touches the matching SQL.
- **Completed sessions only.** A snapshot taken mid-session drops that
  day's bar instead of preserving an intraday price as a close.

Full reasoning: [docs/plan.md](docs/plan.md#decisions).
