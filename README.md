# Can a free price feed be trusted to value a fund?

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
times the 0.5% of NAV that the South African industry standard for unit trusts sets as the most a
pricing error can be before it counts as material.

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

All ten wrong prices in finding 1 were caught automatically, and no correct price was flagged as
wrong in the 131,627 checked.

## What this means

The ASISA standard on unit trust pricing asks managers to validate prices by comparing multiple
sources and reviewing each price against the previous one. Those are the two checks this tool runs.

- A fund priced from this feed alone would have breached the materiality tolerance at least three
  times: twice from the 100x errors and once from the late day, before counting any error small
  enough to look plausible.
- Checking a single source against itself caught the 100x errors without a second vendor. Errors
  of a few percent look like normal price moves, and only a second, independent source can catch
  those. That comparison is the next stage of the project.
- A missing price is more dangerous than a wrong one, because the fallback hides it. Nothing looks
  wrong with a fund valued at Friday's prices; the only way to know is to check every expected day
  against an independent calendar.

## How the tool found this

1. **Collect.** An automated job saves every weekday's closing prices exactly as received.
   Nothing stored is ever edited, and each file is fingerprinted, so any later change is detected.
2. **Standardise.** Prices are converted from cents to rands, matched to the right company even
   after a rename, and checked against the JSE trading calendar.
3. **Check.** Prices 100x off their neighbours, missing days, prices on days the market was
   closed, and frozen prices are flagged for review. Nothing is corrected silently.
4. **Reconcile.** Each new snapshot is compared with the previous one, and every difference is
   classified, explained and kept as a record. The same engine will compare two different vendors.

`python -m src.answers` prints the current answer to each of these questions from the stored data,
and the daily job posts the answers in its run summary.

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
- [ ] A second, independent price source
- [ ] Tracking each difference from first appearance until it is resolved
- [ ] Excel report of breaks for an operations team
- [ ] Holdings reconciliation against a brokerage-style export

## Running it

Python 3.10 or newer. From a clone of the repository:

```bash
pip install -r requirements.txt
git worktree add data/landing snapshots
python -m src.rebuild
python -m src.answers
```

This builds a local database from every stored snapshot and prints the answers. The tests run with
`python -m unittest discover -s tests -t .`.

## More detail

- [docs/findings.md](docs/findings.md) is a dated log of each finding, with the evidence
- [docs/plan.md](docs/plan.md) covers the design and the reasoning behind each decision
- [docs/easyequities_export.md](docs/easyequities_export.md) covers the brokerage export format
