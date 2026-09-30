# Price sources

A reconciliation needs records that are independent of each other. This page records which JSE price
sources were evaluated, which were chosen, and how each one runs.

## Built

| Source | Role | Access | How it runs |
|---|---|---|---|
| Yahoo Finance | The feed under test | No key | Daily in GitHub Actions, all 107 securities, 3-month lookback |
| EODHD | Independent commercial vendor | Free API key (20 calls a day) | Daily in GitHub Actions, 18 securities a day in rotation, 1-year lookback each |
| AFX (afx.kwayisi.org) | Third source, tie-breaker and ISINs | No key; public web pages | **Parked.** The adapter is built and tested, but the site refuses cloud runners and there is no machine to run it on locally |

Rotation takes the securities least recently tried first. Each EODHD call returns a year of history,
so a security fetched once a week still overlaps Yahoo's lookback completely.

## Evaluated and rejected (29 September 2026)

| Source | Reason |
|---|---|
| Stooq | Returns an empty response for JSE symbols, from any network |
| Twelve Data | JSE only on paid plans, from $29 a month |
| Marketstack | Free plan is 100 requests a month, and each symbol counts as one |
| Financial Modeling Prep | Free plan covers US markets only |
| Alpha Vantage | No JSE coverage |
| JSE MarketPlace | The official source, licensed to institutions |

## Evidence

- **EODHD covers the JSE on the free plan.** A two-security test returned a full year for each (500
  rows). The public demo key is refused for JSE tickers, so this needed a real key to establish.
- **EODHD prices are in cents but do not say so.** Its end-of-day response has no currency field.
  ABG.JSE closed at 21743.0 on 2026-09-28, the same as Yahoo's 21743 ZAc, so staging assumes ZAc. The
  landed files keep the vendor's silence, and staged rows are marked `unit_assumed`, so the assumption
  can be corrected without rewriting history.
- **EODHD is not error-free either.** On 27 August 2026 it repeated the previous day's close for 8 of
  the 18 securities compared with Yahoo, while carrying the correct volume; Yahoo was right. See finding
  16 in [findings.md](findings.md).
- **AFX publishes rands and ISINs.** Its pages state that prices are in rand, and its ISIN for NTU,
  ZAE000167391, matches the JSE's own notice of the Transaction Capital rename.
- **AFX refuses cloud runners.** From GitHub Actions every IPv4 address timed out on connect; from a
  local machine the same pages load at once. The site is entitled to that choice, so AFX is never
  routed through proxies. With no machine available to run it on a schedule, it is parked.

## Running AFX, if a machine becomes available

The workflow skips AFX unless asked for by name. From a machine that can reach the site, with the
snapshots worktree in place:

```bash
git -C data/landing pull --rebase
python -m src.ingest --source afx
git -C data/landing add -A
git -C data/landing commit -m "afx snapshot"
git -C data/landing push
```

A run of 22 securities takes about 22 minutes. To reconcile it, add `[yahoo, afx]` and `[eodhd, afx]`
back to `recon_pairs` in `config/sources.yaml`; the next GitHub Actions run then compares it with Yahoo
and EODHD.
