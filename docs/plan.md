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
                staging        tickers mapped, units converted, calendar attached, anomalies flagged
                    │          (data quality views: missing bars, whole-market gaps, closed-day bars)
                    │
security_master ─→ recon (SQL) ←─ tolerance_rules.yaml
trading_calendar ─┘   │
                      ▼
                  break_episode  first_seen, cleared_on, age, state; derived by replaying every snapshot
                      │
                      ▼
              answers + Excel break report (recalculated and cross-checked) + write-up
                      ▲
broker statement ──→ stg_holding ──→ holdings recon ←── ledger (book of record), our checked closes
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

Holdings statuses, for a broker statement against the book:

| Code | Meaning |
|---|---|
| `DUP` | The security appears on more than one statement line |
| `ONE_A` | In the book only |
| `ONE_B` | At the broker only, or a contract code that cannot be mapped |
| `PARSE` | An amount on the line cannot be read; its difference is unknown |
| `UNIT` | The broker's price is about 100x the checked close |
| `SETTLE` | The quantity gap equals trades not yet settled (T+3) |
| `QTY` | The broker's value implies a different quantity from the book |
| `STALE` | The broker's price is the previous session's close |
| `PRICE` | The broker's price differs from the checked close |
| `NOPRICE` | No checked close exists for the statement date |

## Phases

| # | Phase | Status |
|---|---|---|
| 0 | Scaffold, SQLite warehouse, config | Done |
| 1 | Source adapter contract, Yahoo adapter, landing, ingest audit, scheduled ingest | Done, first snapshot 2026-09-29 |
| 2 | Security master (statuses, renames), trading calendar, staging, data quality checks | Done; ISINs still to populate |
| 3 | Recon engine: full outer join, tolerances, scope, classification; answers report | Done; restatement recon runs from the second Yahoo snapshot (30 September 2026) |
| 4 | Break register: episodes, clearing, ageing, TIMING, analyst notes | Done; fills as daily comparisons accumulate |
| 5 | Seeded break suite and completeness assertions | Done: one planted defect per break type |
| 6 | Excel break report: summary, open, new, cleared, restatements, data quality, answers, definitions | Done; attached daily to the private repository's run |
| 7 | Write-up: breaks by type, how fast they clear, and each finding's cost against the 0.5% limit | Once EODHD has checked every share, early October 2026 |
| 8 | Second source (EODHD daily; AFX built but parked); holdings recon (synthetic EasyEquities export) | Done; NTU joins the fixture once its closes are in the warehouse |


## Decisions

| Decision | Why |
|---|---|
| SQLite over DuckDB | No extra dependency, and a reviewer can open the warehouse with any tool. 3.39+ gives `FULL OUTER JOIN`; window functions cover the rest. |
| Wide universe (111 codes, all sectors) | Coverage gaps become findings, and project 3 needs sector spread for sector caps. |
| Landing is immutable | A re-run must not change what a vendor was recorded as saying. Refetch is explicit. |
| Snapshots carry full lookback | Overlapping history between snapshots makes restatement recon possible with a single vendor. |
| Daily lookback is 3 months | Enough overlap for restatement recon, at roughly 140 KB a day compressed. The 5-year backfill was taken once. |
| Scheduled by GitHub Actions, stored in a private repository | Runners are discarded after each job, so the snapshot must be committed somewhere durable. EODHD's and Yahoo's terms restrict republishing their prices, so the data, the run logs and the reports live in the private `jse-price-data` repository, which calls this repository's `ingest` workflow; the workflow refuses to run from a public repository. Until 30 September 2026 they sat on a public `snapshots` branch. |
| Landing is gzipped | Git on Windows rewrites text line endings, which would break every manifest hash. A binary file is stored byte for byte. |
| Coverage gate before landing | Cloud runner IPs get rate-limited by Yahoo. A mostly empty fetch must fail the job, not become a permanent snapshot. |
| Warehouse is derived, never committed | `src.rebuild` recreates it from snapshots, and in CI that doubles as a daily hash check of the whole history. |
| Completed sessions only | Before 17:30 SAST on the snapshot date, that day's bar is excluded, so an intraday price is never preserved as a close. |
| No primary key on `raw_price` | Vendor duplicates must reach the recon as `DUP` breaks, not vanish on load. Idempotency is by partition instead. |
| Adapters return unconverted data | Unit and symbol conversion happen in staging, where they are visible and testable. |
| Retire, never delete, securities | Historical breaks reference them. |
| Calendar from holiday law, not vendor dates | A calendar derived from Yahoo's own dates would mark 28 September 2026 as a holiday and hide the gap it is meant to catch. |
| Flag, never repair | A corrected price hides the break. Staging converts by the reported unit and flags anomalies; the recon decides what they mean. |
| Unit check uses the two nearest bars | A glitch is ~100x off both; a genuine share consolidation moves once and stays, so one reference clears it. At the edge of a series, the two nearest on one side are used, so today's bar is still checked. |
| Movement check is relative to the median share | ASISA s4.2.1 asks for each price to be reviewed against the previous one. A move of 15% or more is flagged only if it beats the median share's move by 10 points, so a market-wide fall does not flood the analyst. It flags for verification; it does not call a price wrong. Unit anomalies are left to the unit check so they are not reported twice. |
| Differences over 50% are `SCALE` breaks | A difference that large is a disagreement about scale or instrument, not a stale price, whatever the exact ratio. It is checked after the 100x unit band, so exact unit errors keep their own type. |
| Breaks carry two ages and a Top 40 cost | Age from first sighting says how long an analyst has had it; age from the price date says how long the books have been exposed. A backfill finds old breaks on day one, so both are needed. Open breaks sort by size, and each shows what it would cost a Top 40 fund at Satrix 40's weights. |
| An approved price per security per day | Detecting problems is half the job; a pricing team must also say which price to use. The hierarchy (primary, secondary, previous close) is in `config/sources.yaml`; a price that passes but moved against the market, or whose break has no resolution, is released only as TO_VERIFY. |
| One view for the latest price, one for frozen runs | "Each price from the latest snapshot holding it" was written three times and the frozen-run query twice; `v_latest_price` and `v_frozen_run` now serve every use, so a fix lands everywhere at once. A frozen run reports the volume traded after its first day: none marks a suspension or a gap-filling feed, some marks a thinly traded share. |
| Report colour marks attention, text says why | Scale and unit breaks and carried-forward prices are shaded red, old breaks and prices to verify amber; the status column beside each carries the meaning, so nothing depends on colour alone. |
| A synthetic demo runs the whole pipeline | The real snapshots are private, so without it nobody else could run the project. Fictional securities and vendors carry every planted error and trap; a test checks each outcome, and CI recalculates the demo report in LibreOffice. The vendors are named `synthetic_a` and `synthetic_b`, so no synthetic error is ever attributed to a real vendor. |
| Errors are costed per unit of weight and for a Top 40 fund | An equal-weighted fund of every share is unrealistic. The per-weight rule (a 1/100 price costs 99% of the holding's weight) holds for any fund; Satrix 40's audited year-end weights give a realistic Top 40 case. The equal-weighted figure is kept, labelled illustrative. |
| Unit band is 80x to 125x | Calibrated on real data: a ±5% band missed CMH on 2025-04-25, which also moved 10% that day. |
| Each snapshot records its session cutoff | The last complete session at fetch time. Without it, a vendor that stops early looks like a short history rather than a missing day. |
| Recon compares only securities both sides requested | A universe change is a scope difference, reported separately. Without this, adding NTU produced 64 false breaks. |
| Both sides carrying the same glitch is a MATCH | The recon measures disagreement between sources; a shared error is a data quality finding, noted on the row. |
| Seeded tests use prices that move | Five identical closes is a stale price, so a flat fixture made every planted break look `STALE`. |
| Costs are measured against ASISA's 0.5% tolerance | s10.3.3 of the ASISA NAV standard, the South African reference for when a pricing error is material. |
| Output is organised as answers to questions | The project exists to answer whether a feed can be trusted to value a fund; `src.answers` states each answer with its evidence. |
| Price errors are judged across every stored price | A daily snapshot covers three months, so answering from it alone would drop older errors. Each price is taken from the latest snapshot holding it (finding 18). |
| Rotated sources take the least recently tried securities | EODHD's 20 free calls and AFX's one page a minute still cover all 107 securities within a week; a failed day is picked up the next day. |
| Units a vendor does not report are assumed in staging | Landing records what the vendor said, including silence. The assumption sits in `sources.yaml` with its evidence, and staged rows are flagged `unit_assumed`. |
| AFX is parked, never proxied | Its servers drop connections from cloud runners, which is the site's choice to make, and every job must run in CI. |
| HTTP uses the operating system's certificate store | Networks that re-sign TLS break certifi's bundle; the OS store trusts their certificate, so verification is never switched off. 429 and 5xx responses are retried after the server's `Retry-After`, and a crawl delay is measured from the previous request. |
| The break register is derived, not stored | Every run replays the recon for every stored snapshot, so first sightings, clearing dates and ages are a function of the history and cannot drift or be lost. |
| A day without a comparison neither breaks nor clears | EODHD compares each security about once a week. A break stays open until a later comparison actually matches, not merely until one is missing. |
| Restatement breaks are events, not episodes | A restatement is a change between two snapshots; by the next day both sides agree on the new value, so it would always look like it fixed itself. |
| TIMING means cleared within 2 trading days | Usually one vendor publishing later than the other; set by `timing_clear_days` in `tolerance_rules.yaml`. |
| Analyst notes live in `config/break_notes.yaml` | The one part of the register that cannot be derived. In version control it has an author, a date and a review trail, and a bad resolution code fails the build. |
| Summary figures are formulas over the detail tabs | An analyst who filters or edits a tab sees the summary follow. Bucket labels read "2-5 days" because Excel reads a bare "2-5" in a criterion as a date. |
| The report is recalculated and cross-checked before publishing | openpyxl writes formulas without results, so CI recalculates in LibreOffice, fails on any formula error, then compares every Summary figure with the same count taken from the database. A formula that evaluates cleanly but counts the wrong column still fails. |
| A test ties each Summary formula to a column header | The cheap local guard for the same failure: if a column moves, the test names the formula that now points at the wrong one. |
| Reports are run artifacts, not commits | They are derived from snapshots and regenerated daily; 30 days of history is enough to compare with yesterday. They are attached to the private repository's runs, since they show vendor prices. |
| Holdings quantity is implied and tested in rands | The EasyEquities export has no quantity column and shares are fractional; a position breaks when its value is out by more than R1.00 and 0.10%. |
| Trade-date book, settlement-date broker | JSE equities settle T+3 on the trading calendar. A gap that equals unsettled trades is `SETTLE`, a timing difference, not `QTY`. |
| Holdings map by ISIN before contract code | A broker can keep an old code after a rename; the ISIN survives it. |
| An unreadable amount has an unknown difference | Treating it as zero would report a misstatement nobody measured. |
| Report net and gross differences | Netting lets a duplicated line hide a missing one: the synthetic statement is 0.88% out net and 15.86% gross in errors. Timing differences and positions that could not be valued are reported apart, so gross is never presented as exact when it is a lower bound. |
| Fixtures are generated, deterministic and self-verifying | Real closes, a fixed seed, and a generator that refuses to write unless every planted outcome holds; CI checks them again daily. |
| Warehouse schema is versioned | A warehouse built by older code is refused with a prompt to rebuild, rather than failing halfway through a load. |
