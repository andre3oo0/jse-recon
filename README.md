# JSE Price Reconciliation

A tool that checks whether different records of the same share prices agree, and catches the
mistakes before they cost money.

## Why this matters

Fund administrators work out what every fund is worth each day using share prices bought from data
providers. If one of those prices is wrong, the fund's value is wrong. Investors who buy or sell
that day pay or receive the wrong amount, and the administrator has to fix it afterwards.

The control that prevents this is **reconciliation**: comparing independent records of the same
data and investigating every difference. This project builds a small version of that control for
shares listed on the Johannesburg Stock Exchange (JSE).

## What it has found so far

Before any comparison between sources had been built, checking a single well-known source (Yahoo
Finance) turned up real problems in five years of prices:

- **Prices 100 times too small.** On 10 January 2025, Yahoo recorded seven JSE shares, including
  Vodacom and Sanlam, at one hundredth of their real price for a single day. A fund valued from
  that data would have reported itself **6.5% smaller** than it was: a R65,000 error on a
  R1 million fund. It happened again on 25 April 2025, to Standard Bank and two others.
- **A missing trading day.** Yahoo has no prices at all for Monday 28 September 2026, a normal
  trading day with no public holiday and no reported market outage.
- **Shares that changed identity.** Five of the 111 shares tracked had been renamed or delisted.
  Each case was confirmed from official JSE notices, not assumed.

All ten wrong prices are now caught automatically, with no false alarms across 131,000 prices.
The full write-up, with sources, is in [docs/findings.md](docs/findings.md).

## How it works

1. **Collect.** Every weekday evening an automated job downloads closing prices for 107 JSE shares
   and stores them exactly as received. Stored records are never edited, and each is
   fingerprinted, so any later change is detected.
2. **Standardise.** Prices are converted from cents to rands, matched to the right company even
   after a name change, and checked against the JSE trading calendar, built from South Africa's
   public holiday law.
3. **Check quality.** Suspicious prices, missing days and prices on days the market was closed are
   flagged for review. Nothing is corrected silently: a problem stays visible until someone
   decides what it means.
4. **Reconcile** *(in progress)*. Two independent sources will be compared side by side, and every
   disagreement will be classified, tracked until it is resolved, and costed in terms of its
   effect on a fund's value.

## Skills shown

| Area | What it involves here |
|---|---|
| SQL | Joins, window functions and data quality views that do the matching and checking |
| Python | The data pipeline, vendor connection and report generation |
| Testing | Automated tests built around real errors found in the data |
| Automation | A scheduled GitHub Actions job that collects, checks and stores data daily |
| Finance | Fund valuation (NAV), reconciliation breaks, tolerances, renames and delistings |
| Data quality | Audit trails, tamper detection, and a policy of flagging rather than fixing |

## Progress

- [x] Daily automated price collection with integrity checks
- [x] Company reference data, including renames and delistings
- [x] JSE trading calendar and data quality checks
- [ ] Side-by-side reconciliation of two sources
- [ ] Tracking each difference from first appearance until it is resolved
- [ ] Excel report of breaks, and a write-up of their cost
- [ ] Holdings reconciliation against a brokerage-style export

## Running it

Python 3.10 or newer. From a clone of the repository:

```bash
pip install -r requirements.txt
git worktree add data/landing snapshots
python -m src.rebuild
python -m src.staging
```

This builds a local database from every stored snapshot and prints the data quality report. The
tests run with `python -m unittest discover -s tests -t .`.

## More detail

- [docs/plan.md](docs/plan.md) covers the design, the phases and the reasoning behind each decision
- [docs/findings.md](docs/findings.md) is a dated log of what the data has shown
- [docs/easyequities_export.md](docs/easyequities_export.md) covers the brokerage export format
