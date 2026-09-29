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

## How the project proceeds

1. **Synthetic export, clearly labelled.** Generate a holdings file in
   the portal's schema from real Yahoo closes, so values are realistic.
   It is fixture data and says so in its filename and header.
2. **Keep the quirks.** The formatting is the valuable part:
   - `R2 000.00` strings need locale-aware parsing, and a naive
     `float()` fails on them.
   - `EQU.ZA.SOL` needs its own security_xref mapping.
   - If `current_price` turns out to be in rands, it meets Yahoo's cents
     head-on, which gives a natural `UNIT` test.
3. **Seed the defects** from the break taxonomy (missing holding,
   duplicate line, quantity mismatch) so the holdings recon has known
   answers.
4. **Swap in a real export later.** The synthetic file's schema is the
   adapter contract. A real export replaces the fixture without
   changing the recon.

## Getting a real export

With an EasyEquities account, export from the platform's Transaction
History page yourself and drop the file in `data/landing/easyequities/`.
The project will never ask for or handle EasyEquities login
credentials.
