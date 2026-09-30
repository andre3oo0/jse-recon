# EasyEquities holdings export

Holdings recon (phase 8) needs a custodian-style holdings file. There is
no real export to work from yet, so this records what is known about the
format and how the project proceeds without one.

## What is known

**EasyEquities does not publish its export schema.** Its support article
on transaction history says the full history can be downloaded from the
Transaction History page (menu, top left), but does not name a file
format or any columns.
[Source](https://support.easyequities.co.za/support/solutions/articles/5000642097-how-do-i-get-a-report-of-my-transaction-history-)

**The web portal's holdings fields are known** from the unofficial
`easy-equities-client` Python library, which reads the portal. Each
holding carries:

| Field | Example | Note |
|---|---|---|
| `name` | | Security name |
| `contract_code` | `EQU.ZA.GLODIV` | A fourth symbol convention: `EQU.ZA.<code>` |
| `purchase_value` | `R2 000.00` | Formatted string: `R` prefix, space thousands separator |
| `current_value` | `R3 000.00` | Same formatting |
| `current_price` | | Unit not confirmed |
| `isin` | | Present, which makes ISIN matching possible |
| `img`, `view_url` | | Display only |

[Source](https://pypi.org/project/easy-equities-client/0.2.0)

These are the portal's fields. The downloaded file may differ. Treat
them as a working assumption until a real export confirms them.

**No public sample exists**, which is unsurprising: an export is a
person's financial record. Using someone else's would not be
appropriate, so the project does not look for one.

## What was built

A synthetic statement in the portal's format, generated from real Yahoo closes, with planted breaks
and known answers. Its files, planted outcomes and regeneration are described in
[fixtures/holdings/README.md](../fixtures/holdings/README.md).

Building against the real format forced three design decisions:

- **Quantity is implied, so it is tested in rands.** The export has a value and a price but no
  quantity, and EasyEquities sells fractional shares. Value divided by price carries up to half a
  cent of rounding, so a position breaks when its value is out by more than R1.00 and 0.10%, not when
  its share count differs in the fourth decimal.
- **ISIN before contract code.** A broker can keep a security's old code after a rename, as it would
  have for Transaction Capital becoming Nutun. The ISIN survives the rename, so it is tried first.
- **Amounts are parsed, never assumed.** `R2 000.00` with an ordinary space, a non-breaking space, or
  a narrow one; commas as thousands or decimal separators. An amount that cannot be read is a `PARSE`
  break with an unknown difference, never zero.

A real export would replace the fixture without changing the recon: the file format is the contract.

## Using a real export

To reconcile a real statement, save it as a CSV with the columns above and
the statement date at the end of the file name, and pass it with the
matching ledger of trades:

```bash
python -m src.holdings --export easyequities_holdings_2026-10-30.csv --ledger my_ledger.csv
```

A real file is not labelled synthetic, so the report and the answers treat
it as the real thing. The project never asks for or handles EasyEquities
login details.
