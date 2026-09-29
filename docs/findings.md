# Findings log

Dated observations from the data. Each one is either a break type the
engine must detect or a design decision it forced. Numbers are
reproducible from the warehouse; the query is described with each.

## 2026-09-29: first snapshot

Yahoo Finance, 5-year lookback, 111 codes requested.
106 returned data, 131,627 rows, 2021-09-29 to 2026-09-25.

### 1. Yahoo's history contains rand prices inside a cents series

Every symbol reports its unit as `ZAc` (cents). On two dates, ten
securities have a single bar that is roughly 1/100th of its neighbours,
then snaps back the next session:

| Date | Securities | Example |
|---|---|---|
| 2025-01-10 | DTC, PAN, PPH, SLM, SNT, SPG, VOD | VOD: 10,140 → **101** → 9,984 |
| 2025-04-25 | CMH, SBK, TSG | SBK: 22,789 → **229** → 23,336 |

Those bars are in rands while the series is labelled cents. The errors
cluster on two dates across unrelated companies, which points to a feed
incident rather than anything the companies did.

**Cost if uncaught.** On an equal-weighted R1m fund across the 106 names
(0.94%, or R9,434, per name), a NAV struck from these bars would have
been:

| Date | NAV misstatement | vs a 1bp restatement threshold | On R1m |
|---|---|---|---|
| 2025-01-10 | −654bp (−6.54%) | 654x | −R65,374 |
| 2025-04-25 | −280bp (−2.80%) | 280x | −R28,022 |

The fund is illustrative, not a real holding. The true price is taken
as the midpoint of the neighbouring sessions.

**Consequences for the engine.**
- `UNIT` is a real break type, not a hypothetical. The seeded break
  suite should include it, and it must be tested against this history.
- A two-source recon only catches it if the second source is clean on
  that date. A single-source jump check (day-on-day ratio near 100 or
  0.01) catches it with no second source at all, so it belongs in the
  data quality layer as well as the recon.
- The `reported_unit` field is not enough. The metadata said `ZAc` for
  bars that were in rands. Unit has to be inferred from the data too.

### 2. Monday 28 September 2026 is missing from Yahoo

Every lookback period (5d through 5y) jumps from 25 September to 29
September. 24 September is Heritage Day, which explains that gap. No
public holiday explains the 28th.

With one source it is impossible to say whether the JSE did not trade or
Yahoo dropped the session. This is the case for a second feed and an
independent trading calendar: without them, a missing session
reconciles as "nothing to compare" and never flags.

### 3. Five codes returned nothing, all for real reasons

None was a typo or network noise. Each was re-probed with full history
and checked against a primary source.

| Code | Reason | Handling |
|---|---|---|
| TCP | Renamed to Nutun (NTU) 2025-03-18, ISIN ZAE000167391 unchanged | Retired, NTU added |
| MCG | Delisted 2025-12-10 after Canal+ takeover | Retired |
| BAW | Delisted 2026-01-27 after Entsha consortium takeover | Retired |
| MUR | Holding company in final liquidation, left the JSE January 2026 | Retired |
| ARH | Listing reported inactive; date unverified | Retired, flagged |

AMS.JO (Anglo American Platinum) also returns nothing; the universe
already used its new code, VAL (Valterra Platinum).

**Consequences.**
- Alpha codes are not stable identifiers. The ISIN survived the TCP
  rename; the code did not. The security master should key on ISIN
  once it is populated.
- Yahoo files Transaction Capital's entire history under `NTU.JO`, so
  this vendor rewrites history onto the new symbol. A second vendor that
  keeps the old code for pre-rename dates would produce a wall of
  one-sided breaks unless the cross-reference carries validity dates.

### 4. Other observations to follow up

- **Short histories.** CFR starts 2023-04-19, BHG 2022-01-31, FTB
  2022-01-26. Each date plausibly matches a restructuring (Richemont's
  JSE line, BHP unifying its listings, Fairvest's merger). Unverified.
- **Stale candidates.** FFB closed at exactly 954c for 28 sessions from
  2022-09-19. Either a trading suspension or a dead feed; the `STALE`
  rule cannot tell which, so it should surface this for a human, not
  auto-clear it.
- **82 zero-volume bars** across the universe. Thin trading on small
  caps is expected; zero volume on a large cap would be suspicious.

### 5. Environment

The network here intercepts TLS, so Yahoo's cookie and crumb requests
fail certificate verification. Price data still arrives after retries.
The ingest treats every symbol as able to fail independently, and it
logs the outcome in `ingest_symbol_status` rather than crashing or
dropping the symbol quietly.

## 2026-09-29: staging and data quality checks

The first snapshot, run through staging: 131,627 rows, all mapped to a security, all in a
recognised unit, all inside the calendar.

### 6. The unit check catches all ten glitches, with no false alarms

Each bar is compared with its two nearest bars. A bar roughly 100x away from both is flagged. All
ten bars from finding 1 are caught, and none of the other 131,617 bars is flagged.

The band had to be calibrated on the real data. A ±5% band around 100x missed CMH on 2025-04-25:
the glitch bar was R0.30 between R29.59 and R32.50, so the share also moved 10% that day. The band
is now 80x to 125x. A genuine day's move is nowhere near that size, so widening it costs nothing.

Writing the tests exposed an edge case the real data did not contain: with only two bars 100x
apart, either could be the wrong one. The check leaves them unjudged until a third bar arrives,
rather than guessing.

### 7. The holiday-law calendar agrees with Yahoo on 1,247 of 1,248 trading days

The calendar is built from the Public Holidays Act (fixed holidays, Easter, and the rule that a
Sunday holiday is observed on the Monday), plus closures declared under section 2A, each with a
source in `config/jse_calendar.yaml`. It was built without looking at Yahoo's dates.

Over five years:
- **No Yahoo bars fall on a closed day**, including all four declared closures in the period
  (2021-11-01, 2022-12-27, 2023-12-15, 2024-05-29).
- **Every trading day has bars except one: 2026-09-28**, missing for all 106 symbols.
- **No individual stock is missing a single session.** Yahoo fills every bar, even with zero
  volume (the 82 zero-volume bars from finding 4 all fall on trading days). A missing bar for one
  stock will therefore stand out when it happens.

### 8. 28 September 2026 still has no explanation

No special holiday was declared for that date. The next declared closure is 4 November 2026, for
the local government elections, and it is already in the calendar. No JSE outage has been reported
for the 28th either. The likeliest explanation is a Yahoo gap, but that is unconfirmed until a
second source is available.

The next snapshot will show whether Yahoo fills the day in late. If it does, that is a restatement
of history, which is exactly what the restatement recon is designed to detect.
