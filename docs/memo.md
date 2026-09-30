# Memo: pricing a JSE equity fund from two data vendors

**To:** Head of Valuations  **From:** Pricing analyst  **Date:** 30 September 2026
**Subject:** Whether Yahoo and EODHD can price the fund, and the controls they need

## Recommendation

1. **Do not release a NAV from either vendor unchecked.** Over five years Yahoo printed ten prices 100
   times too small and published one whole session 17 hours late; in one month EODHD carried eight closes
   forward and reported the wrong instrument for Blu Label. Each would have breached the 0.5% tolerance
   for any holding above 0.51% of the fund.
2. **Compare every share against a second source every day.** The free EODHD plan covers 18 shares a day,
   so most shares have no second price on most days, and an error of a few percent on those days goes
   unseen. A paid end-of-day plan covering the whole universe removes that gap.
3. **Use the price hierarchy the tool already applies** (Approved Prices tab): the primary price when it
   passes every check; the secondary when the primary is missing, 100x off or recorded as the error; the
   previous close only as a last resort, verified as fair and reasonable before use (ASISA s4.2.2).

## Controls and why each threshold is set where it is

| Control | Threshold | Evidence |
|---|---|---|
| Unit error | 80x to 125x off both neighbouring prices | Caught all ten 100x prices in 131,627, with no false alarm; a real share consolidation moves once and is not flagged |
| Unexplained move | 15% from the previous close and 10 points more than the median share | 58 moves in five years, about one a month: a workload an analyst can verify against company news |
| Sources disagree | More than R0.01 and 0.05% | Of 4,482 prices both vendors reported, 4,456 agreed within tolerance; all 26 others were EODHD errors (8 carried-forward closes, 18 Blu Label prices) |
| Scale disagreement | More than 50% apart | Separates a wrong instrument (Blu Label, 99% apart) from a stale price |
| Frozen price | Five sessions unchanged | 20 runs in five years; to be tightened to require zero volume, which marks the Fortress run of 2022 |
| Late or missing session | A whole trading day missing on the JSE calendar | Found 28 September 2026 from the calendar alone, before any vendor comparison |

## Escalation

- An exception that could move NAV by more than 0.5% is resolved or escalated the same day, before the NAV
  is signed off. For a price 100x off, that is any holding above 0.51% of the fund.
- Any other break is resolved or escalated within five trading days.
- Every resolution is recorded with its evidence and author (`config/break_notes.yaml`), so the register
  shows who decided what and why.

## Cost

- EODHD's cheapest plan with global end-of-day prices is US$19.99 a month (US$199 a year), listed as a
  personal plan on its pricing page (eodhd.com/pricing, 30 September 2026). A fund would need its
  commercial licence, whose price is not published: **a quote is needed before deciding**.
- Analyst time: the daily break review, plus about one unexplained move a month to verify.

## What these controls cannot catch

- An error both vendors share, for example both taking the same wrong exchange feed.
- A plausible error, under 15%, on a share with no second-source price that day.
- A corporate action priced before it is announced: Yahoo's adjusted close did not adjust any of the 58
  large moves, so a corporate actions feed or a manual check of SENS is needed.
- Neither vendor is an official source. For a live fund, the JSE's own closing prices, or a licensed feed,
  would be the primary, with these controls unchanged.
