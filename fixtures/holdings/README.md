# Synthetic holdings fixtures

**These files are synthetic.** They are not anyone's portfolio. They exist to exercise the holdings
reconciliation with known answers, because no real EasyEquities export is available.

| File | What it is |
|---|---|
| `easyequities_holdings_SYNTHETIC_2026-09-25.csv` | A broker statement in the EasyEquities portal's holdings format |
| `internal_ledger_SYNTHETIC.csv` | The internal book of record: every trade, on a trade-date basis |
| `expected.csv` | The status each position must receive |

The prices are real. Every trade is priced at Yahoo's actual close on a real JSE trading day, and the
statement is valued at the actual close on Friday 25 September 2026. Quantities are fractional, as
EasyEquities allows. Fees are an illustrative 0.35%, not EasyEquities' tariff. ISINs appear only where
one was confirmed against a source; the rest are blank rather than invented.

## Planted breaks

| Security | What was planted | Expected |
|---|---|---|
| ABG | A buy on Wednesday 23 September. With Heritage Day on the 24th it settles T+3 on the 29th, so the broker does not hold it yet | `SETTLE` |
| NPN | The broker holds half a share fewer than the book | `QTY` |
| FSR | Sold at the broker, never booked internally | `ONE_A` |
| CLS | Transferred in at the broker, never booked internally | `ONE_B` |
| SHP | The broker shows Wednesday's close instead of Friday's | `STALE` |
| BHG | The price is shown in cents (R69 070.00 for R690.70); the value is right | `UNIT` |
| MTN | The line appears twice | `DUP` |
| GRT | The value reads `R—` | `PARSE` |
| VOD | Thousands separated by a non-breaking space, as South African locales often do | `MATCH` |
| CPI | The value is R0.40 out, inside the R1.00 tolerance | `MATCH` |
| GFI, SBK, SOL | Nothing | `MATCH` |

The last three `MATCH` rows matter as much as the breaks: a recon that flags the non-breaking space or
the 40 cents would be raising false alarms.

NTU, held at the broker under Transaction Capital's old code and matched by ISIN, joins the portfolio
once the warehouse holds NTU closes. The first snapshot to request NTU was taken on the evening of
2026-09-29. The mapping itself is covered by a unit test in the meantime.

## Regenerating

```bash
python -m src.synthetic_holdings
```

Generation is deterministic (fixed seed, fixed prices), so the output is byte-identical each time. The
generator runs the reconciliation on a scratch copy first and refuses to write the fixtures unless every
planted outcome holds. The daily GitHub Actions job checks the same thing again with
`python -m src.holdings --check-expected`.
