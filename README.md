# JSE Share Price Reconciliation

**Can a free source of share prices be trusted to value an investment fund?**

Fund administrators work out what every fund is worth each day from share prices supplied by data
vendors. If one of those prices is wrong, the fund's value is wrong, and investors who buy or sell
that day pay or receive the wrong amount. Administrators guard against this with
**reconciliation**: checking prices against independent records and investigating every
difference.

Free sources such as Yahoo Finance are widely used for research and personal investing, but they
come with no guarantee. I wanted to know whether one could be trusted to value a fund of JSE shares,
so I built a reconciliation tool and pointed it at five years of Yahoo's prices for 111 JSE
securities.

## What I found

**1. Some prices were 100 times too small.** On 10 January 2025, Yahoo recorded seven JSE shares,
including Vodacom and Sanlam, at one hundredth of their real price for a single day. It happened
again on 25 April 2025, to Standard Bank and two others. A fund valued from this data on 10 January
would have reported itself **6.54% smaller** than it was: R65,000 on a R1 million fund. That is 13
times the 0.5% limit that South Africa's fund industry sets for how far out a fund's price can be
before the error must be put right.

**2. A whole trading day was published late, on the worst possible day.** Yahoo's prices for
Monday 28 September 2026 did not appear until more than 17 hours after the market closed. When a
price is missing, the industry standard lets a manager fall back on the most recent one available,
here Friday's. That Monday, gold miners sold off: Gold Fields fell 12% on three times the volume of
the four sessions before. A fund valued with Friday's prices would have been **overstated by 0.61%**, above
the 0.5% tolerance. I could tell it was a vendor gap, not a market holiday, because the tool checks
dates against its own JSE calendar, built from South Africa's public holiday law.

**3. Past prices were not rewritten.** Comparing two fetches of the same three months, every one of
6,678 closing prices matched to the cent. The only differences were the late day above. This is
now checked every day as new snapshots arrive.

**4. The list of securities goes out of date.** Five of the 111 codes had been renamed or delisted,
four of them since March 2025, including Transaction Capital becoming Nutun. Yahoo had quietly moved
Transaction Capital's entire price history under the new code, which would break any comparison
with a source that kept the old one.

**5. Some prices sat frozen.** On 20 occasions a share's price stayed identical for five or more
trading days. The longest was Fortress Real Estate, unchanged for 28 sessions in 2022. A frozen
price can mean a trading suspension or a dead feed, and each needs a person to check which.

**6. The commercial vendor made mistakes too.** Checking Yahoo against EODHD, a commercial data
vendor, on 18 shares: on 27 August 2026 EODHD repeated the previous day's closing price for 8 of them,
while reporting the correct trading volume for that day. Each source fails in its own way, which is
exactly why a fund checks one against another.

All ten wrong prices in finding 1 were caught automatically, and no correct price was flagged as
wrong in the 131,627 checked.

## What this means

The fund industry's own pricing standard (from ASISA, the South African industry body) asks fund
managers to check prices by comparing several sources and by comparing each price with the one
before it. Those are the two checks this tool runs.

- A fund priced from this feed alone would have gone past that 0.5% limit at least three
  times: twice from the 100x errors and once from the late day, before counting any error small
  enough to look plausible.
- Checking a single source against itself caught the 100x errors without a second vendor. Errors
  of a few percent look like normal price moves, and only a second, independent source can catch
  those. So after evaluating nine alternatives, I added a commercial data vendor (EODHD), and
  Yahoo's prices are now reconciled against it every day.
- A missing price is more dangerous than a wrong one, because the fallback hides it. Nothing looks
  wrong with a fund valued at Friday's prices; the only way to know is to check every expected day
  against an independent calendar.

## The same control for holdings

The statement a fund gets from its broker has to agree with the fund's own records of what it
bought and sold, and with an independent valuation. I built that reconciliation too. With no real brokerage export available, I
generated a **synthetic** EasyEquities-style statement from real closing prices and planted eight kinds
of break in it, from a duplicated line to a trade that had not yet settled. The tool finds all eight
and raises no false alarms on the traps, such as a value 40 cents out.

The lesson it demonstrates: **netted, the statement was out by just 0.88%; gross, by 16.95%.** A
duplicated line had hidden most of a missing position. A reconciliation that checks only the total
would have passed it.

## How the tool found this

1. **Collect.** An automated job saves every weekday's closing prices exactly as received.
   Nothing stored is ever edited, and each file is fingerprinted, so any later change is detected.
2. **Standardise.** Prices are converted from cents to rands, matched to the right company even
   after a rename, and checked against the JSE trading calendar.
3. **Check.** Prices 100x off their neighbours, missing days, prices on days the market was
   closed, and frozen prices are flagged for review. Nothing is corrected silently.
4. **Reconcile.** Each new snapshot is compared with the previous one, and with the same prices
   from other vendors. Every difference is classified, explained and kept as a record.
5. **Track.** Each disagreement is followed from the day it first appears until a later
   comparison matches, so the tool can say what is still open, how long it has been open, and
   which differences fixed themselves within two trading days. An analyst can attach a note and a
   resolution code to any break, kept in version control alongside the code.
6. **Report.** Every run produces an Excel break report for an operations team: open breaks by
   age, what is new, what cleared, restatements and data quality, with every definition and
   assumption written into the workbook. Before it is published, the job recalculates it and
   checks each headline figure against the database, so a wrong total fails the run.

`python -m src.answers` prints the current answer to each of these questions from the stored data,
and the daily job posts the answers in its run summary. The Excel report is attached to each run
for 30 days.

## Skills shown

| Area | What it involves here |
|---|---|
| SQL | Joins, window functions and views that do the matching and quality checks |
| Python | The data pipeline, vendor connection and reporting |
| Testing | A suite of planted errors the tool must find, built from real cases in the data |
| Automation | A scheduled GitHub Actions job that collects, checks, reconciles and reports daily |
| Finance | Fund valuation (NAV), reconciliation breaks, tolerances, renames and delistings |
| Data quality | Audit trails, tamper detection, and flagging problems rather than hiding them |

## Progress

- [x] Daily automated price collection with integrity checks
- [x] Company reference data, including renames and delistings
- [x] JSE trading calendar and data quality checks
- [x] Reconciliation engine, running daily on consecutive snapshots
- [x] An independent commercial price source, chosen from nine evaluated
- [x] Tracking each difference from first appearance until it is resolved
- [x] Excel report of breaks for an operations team, checked before it is published
- [x] Holdings reconciliation against a brokerage-style export (synthetic, with planted breaks)

## Running it

Python 3.10 or newer. From a clone of the repository:

```bash
pip install -r requirements.txt
git worktree add data/landing snapshots
python -m src.rebuild
python -m src.answers
python -m src.report
```

This builds a local database from every stored snapshot, prints the answers, and writes the Excel
report to `reports/`. The tests run with
`python -m unittest discover -s tests -t .`.

## More detail

- [docs/findings.md](docs/findings.md) is a dated log of each finding, with the evidence
- [docs/sources.md](docs/sources.md) compares the nine price sources considered, and why EODHD was chosen
- [docs/plan.md](docs/plan.md) covers the design and the reasoning behind each decision
- [docs/easyequities_export.md](docs/easyequities_export.md) covers the brokerage export format
