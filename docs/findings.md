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

| Date | NAV misstatement | vs ASISA's 0.5% materiality tolerance | On R1m |
|---|---|---|---|
| 2025-01-10 | −654bp (−6.54%) | 13.1x | −R65,374 |
| 2025-04-25 | −280bp (−2.80%) | 5.6x | −R28,022 |

The fund is illustrative, not a real holding. The true price is taken
as the midpoint of the neighbouring sessions. The tolerance is from the
ASISA Standard on NAV calculation for CIS portfolios, s10.3.3: "The
suggested maximum tolerance for the materiality of an error is 0,5%
based on the NAV price".
[Source](https://asisa.org.za/media/om5kbuwu/asisa-standard-nav-calculation-for-cis-portfolios-november-2015.pdf)

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

### 8. 28 September 2026: the market traded, Yahoo published late

No special holiday was declared for that date (the next declared closure is 4 November 2026, and
it is already in the calendar), and no JSE outage was reported. The calendar said it was a normal
trading day. It was: the bars have since appeared, with real volume (2.76 million Sasol shares).

| Fetch (29 September, SAST) | Bars for 28 September |
|---|---|
| 10:12, local backfill | Missing for all 106 symbols |
| 10:33, first GitHub Actions dry run | Missing |
| 11:43, second dry run | Present for all 107 symbols |

Yahoo published a whole JSE session more than 17 hours after the close. A fund valued on the
evening of the 28th from this source would have had no closing prices for the day.

**Consequences.**
- An independent calendar is what made this visible. A calendar derived from Yahoo's dates would
  have treated the 28th as a holiday, and the late prices would have looked like new history
  rather than a gap being filled.
- A snapshot taken on the evening of a late day is flagged with a whole-market gap, and the next
  snapshot carries the missing session. The committed snapshot for 29 September (taken at 10:12)
  lacks the 28th, so the next one will show it arriving: the first real restatement for the
  restatement recon to find.
- One late session does not show that Yahoo is routinely late. The daily data quality report
  will show whether it recurs.

## 2026-09-29: reconciliation engine

### 9. Three months of history was not rewritten

A preview of tomorrow's restatement recon: the committed 10:12 snapshot against a fresh fetch at
14:15, both covering 29 June to 28 September. The fresh fetch was held in a scratch warehouse and
never landed.

- **All 6,678 prices both fetches covered matched exactly**, with a largest difference of R0.00.
- **106 breaks, all `ONE_B` on 2026-09-28**: the late session from finding 8, now caught by the
  recon itself rather than only by the calendar check.

The first version reported 170 breaks. The other 64 were NTU, which joined the universe after the
10:12 snapshot and so was never requested on side A. That is a scope difference, not a vendor
error, so the recon now compares only securities both runs requested and lists the rest
separately. Without that rule, every universe change would appear as a wall of breaks.

### 10. The industry standard asks for exactly these checks

Section 4.2.1 of the ASISA NAV standard says prices should be validated "for reasonability through
actions such as: (i) Comparing multiple sources; and (ii) Reviewing the price against the previous"
price. The unit check does the second with one source; the reconciliation engine does the first as
soon as a second source is available.

### 11. The late day would have caused a material error

Section 4.2.2 of the ASISA standard: "Where prices at the most recent valuation point are not
available for any reason the most recent available price may be used subject to verification that
this is fair and reasonable in the circumstances."

So the realistic failure on 28 September was not a fund with no prices, which anyone would notice.
It was a fund quietly valued at Friday's prices. That Monday, gold and platinum miners fell:

| Security | Friday 25 Sep | Monday 28 Sep | Error if Friday's price is used |
|---|---|---|---|
| Gold Fields | R657.52 | R578.40 (6.51m shares, 3.1x the previous four sessions' average) | +13.7% |
| Harmony | R305.64 | R289.26 | +5.7% |
| Valterra Platinum | R1,317.17 | R1,250.47 | +5.3% |

Across an equal-weighted fund of the 107 securities, using Friday's prices would have overstated
NAV by **61bp (0.61%)**, 1.2 times the 0.5% materiality tolerance. The volume on the 28th confirms
these were real trades, not another glitch.

`python -m src.answers` now costs every late session this way, using the day before's prices from
the later snapshot.

## 2026-09-29: a second source, and a third that could not run

### 12. Two vendors agree on the close and disagree on the volume

A first look at AFX against Yahoo, before any snapshot of it was landed:

| Security | Date | Close, AFX / Yahoo | Volume, AFX | Volume, Yahoo |
|---|---|---|---|---|
| Sasol | 2026-09-28 | R230.24 / 23024c | 2,764,940 | 2,764,940 |
| Sasol | 2026-09-25 | R231.00 / 23100c | 1,855,854 | 1,865,854 |
| Sasol | 2026-09-23 | R231.49 / 23149c | 3,699,900 | 4,132,400 |
| Standard Bank | 2026-09-25 | R300.72 / 30072c | 1,645,772 | 1,667,006 |

Closes match to the cent in every case; volumes match on some days and not others, sometimes by a
round 10,000 shares. The likeliest explanation is that one vendor includes trades reported off the
order book and the other does not. Volume is therefore stored in every recon result but is not a
break: the daily answers report how often it differs.

The disagreement is also useful evidence of independence. Two feeds that were copies of each other
would not differ on volume.

### 13. Each source needed a different fix before it could be trusted

- **EODHD reports no unit.** Its prices turned out to be in cents (ABG.JSE 21743.0 against Yahoo's
  21743 ZAc on 2026-09-28). Stamping that into the landed files would have recorded an assumption
  as vendor fact, so the assumption lives in configuration and staged rows say it was assumed.
- **AFX refuses cloud servers.** From GitHub Actions every connection timed out; from a desktop it
  answers at once. With no machine to run it on a schedule, it is parked. Discovering this in a two-security test, rather than in
  the scheduled job, mattered: a 22-security run would have hit the job's time limit and cancelled
  the commit of that day's Yahoo and EODHD snapshots. The AFX step now has its own time limit, and
  gives up after three consecutive failures.

Evaluation of all nine sources considered: [sources.md](sources.md).

## 2026-09-29: holdings reconciliation (synthetic statement)

These come from a synthetic statement with planted breaks, so they are findings about the control,
not about any real portfolio. Details: [fixtures/holdings/README.md](../fixtures/holdings/README.md).

### 14. A net check would have passed a statement that was 17% wrong

| Measure | Amount | Share of the book |
|---|---|---|
| Net difference | −R4,009 | −0.88% |
| Gross difference | R77,096 | 16.95% |

A duplicated MTN line (+R26,055) and an unrecorded CLS transfer (+R10,163) offset most of a missing FSR
position (−R35,243). Checking only the total is a common shortcut, and here it would have hidden three
real errors. The report and the answers show both figures.

### 15. Not every quantity difference is an error

ABG's statement holds 87.4 shares against 110.1 in the book. The difference is a buy on 23 September
that settles T+3 on the 29th, because Heritage Day on the 24th pushes settlement back a day. A naive
recon calls that a quantity break; this one classifies it `SETTLE`, and would call it `QTY` if the
same gap came from a trade that should already have settled.
