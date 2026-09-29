# Plan

## Goal

A lightweight version of the reconciliation control that fund
administrators run daily: two independent records of the same JSE
prices or holdings, matched in SQL, with every disagreement classified,
aged and costed.

It also builds the foundation for projects 2 (performance attribution)
and 3 (mandate compliance): the security master and the price store are
shared, so those projects are applications on this platform rather than
separate scripts.

## Architecture

```
vendor feeds ──→ landing/      immutable, one folder per source per snapshot date
                    │
                    ▼
                raw_price      faithful copy, no dedupe, partition-replace on reload
                    │
                    ▼
                staging        tickers mapped, units converted, calendar aligned
                    │
security_master ─→ recon (SQL) ←─ tolerance_rules.yaml
trading_calendar ─┘   │
                      ▼
                  break_store  stateful: first_seen, last_seen, status, age
                      │
                      ▼
              summary + Excel break report + write-up
```

## Break taxonomy

| Code | Type | Detection |
|---|---|---|
| `VAL` | Value break | Both sides present, outside tolerance |
| `ONE_A` | Missing in B | Left-only after `FULL OUTER JOIN` |
| `ONE_B` | Missing in A | Right-only after `FULL OUTER JOIN` |
| `DUP` | Duplicate key | More than one row per key per source |
| `STALE` | Stale price | Identical close for N sessions |
| `UNIT` | Unit mismatch | Price ratio near 100 or 0.01 |
| `CAL` | Calendar exception | One-sided on a non-trading day |
| `TIMING` | Timing difference | Opens and clears within N days |

A tolerance breach needs **both** an absolute floor and a relative
percentage, set per source pair and per field in
`config/tolerance_rules.yaml`.

## Phases

| # | Phase | Status |
|---|---|---|
| 0 | Scaffold, SQLite warehouse, config | Done |
| 1 | Source adapter contract, Yahoo adapter, landing, ingest audit, scheduled ingest | Done, first snapshot 2026-09-29 |
| 2 | Security master (done: statuses, renames), trading calendar, staging normalisation | Partly done |
| 3 | Recon engine: full outer join, tolerances, classification | |
| 4 | Break store: lifecycle, idempotent upsert, ageing | |
| 5 | Seeded break suite and completeness assertions | Ingest tests done |
| 6 | Excel break report and recon summary | |
| 7 | Write-up: noise reduction and NAV-bp cost | |
| 8 | Second source or restatement recon; holdings recon (synthetic EasyEquities export) | |

If time runs short, cut phase 8 before phase 5.

## Decisions

| Decision | Why |
|---|---|
| SQLite over DuckDB | No extra dependency, and a reviewer can open the warehouse with any tool. 3.39+ gives `FULL OUTER JOIN`; window functions cover the rest. |
| Wide universe (111 codes, all sectors) | Coverage gaps become findings, and project 3 needs sector spread for sector caps. |
| Landing is immutable | A re-run must not change what a vendor was recorded as saying. Refetch is explicit. |
| Snapshots carry full lookback | Overlapping history between snapshots makes restatement recon possible with a single vendor. |
| Daily lookback is 3 months | Enough overlap for restatement recon, at roughly 140 KB a day compressed. The 5-year backfill was taken once. |
| Scheduled by GitHub Actions, stored on a `snapshots` branch | Runners are discarded after each job, so the snapshot must be committed somewhere durable. A separate orphan branch keeps data commits out of the code history. |
| Landing is gzipped | Git on Windows rewrites text line endings, which would break every manifest hash. A binary file is stored byte for byte. |
| Coverage gate before landing | Cloud runner IPs get rate-limited by Yahoo. A mostly empty fetch must fail the job, not become a permanent snapshot. |
| Warehouse is derived, never committed | `src.rebuild` recreates it from snapshots, and in CI that doubles as a daily hash check of the whole history. |
| Completed sessions only | Before 17:30 SAST on the snapshot date, that day's bar is excluded, so an intraday price is never preserved as a close. |
| No primary key on `raw_price` | Vendor duplicates must reach the recon as `DUP` breaks, not vanish on load. Idempotency is by partition instead. |
| Adapters return unconverted data | Unit and symbol conversion happen in staging, where they are visible and testable. |
| Retire, never delete, securities | Historical breaks reference them. |
