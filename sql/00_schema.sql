-- Warehouse schema. SQLite 3.39+ is required for FULL OUTER JOIN in the
-- recon layer.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------

-- One row per real-world security. security_id is the current JSE alpha
-- code. Alpha codes do change (TCP became NTU on 2025-03-18, AMS became
-- VAL); the ISIN survives a rename and should become the key once it is
-- populated for the whole universe. A retired security is kept, never
-- deleted, because historical breaks still reference it.
CREATE TABLE IF NOT EXISTS security_master (
    security_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    sector       TEXT,
    isin         TEXT,
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'retired', 'unresolved')),
    status_note  TEXT,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- How each vendor names each security. A vendor symbol maps to exactly
-- one security; a security can have several symbols over time.
CREATE TABLE IF NOT EXISTS security_xref (
    source         TEXT NOT NULL,
    vendor_symbol  TEXT NOT NULL,
    security_id    TEXT NOT NULL REFERENCES security_master (security_id),
    valid_from     TEXT,
    valid_to       TEXT,
    PRIMARY KEY (source, vendor_symbol)
);

-- ---------------------------------------------------------------------
-- Ingest audit
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest_run (
    run_id             TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    snapshot_date      TEXT NOT NULL,
    fetched_at         TEXT NOT NULL,
    lookback_period    TEXT NOT NULL,
    symbols_requested  INTEGER NOT NULL,
    symbols_returned   INTEGER NOT NULL,
    rows_landed        INTEGER NOT NULL,
    landing_path       TEXT NOT NULL,
    landing_sha256     TEXT NOT NULL
);

-- Per-symbol outcome of each run. This is the coverage record: a symbol
-- that returns nothing is logged here, never silently skipped.
CREATE TABLE IF NOT EXISTS ingest_symbol_status (
    run_id         TEXT NOT NULL REFERENCES ingest_run (run_id),
    vendor_symbol  TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('ok', 'empty', 'error')),
    row_count      INTEGER NOT NULL,
    reported_unit  TEXT,
    error          TEXT,
    PRIMARY KEY (run_id, vendor_symbol)
);

-- ---------------------------------------------------------------------
-- Raw prices
-- ---------------------------------------------------------------------

-- Faithful copy of landed files. Deliberately no primary key: a vendor
-- that sends a duplicate row must be caught downstream as a DUP break,
-- not quietly deduplicated on load. Re-runs are idempotent at partition
-- level instead (the loader replaces a whole source + snapshot_date).
CREATE TABLE IF NOT EXISTS raw_price (
    source         TEXT NOT NULL,
    snapshot_date  TEXT NOT NULL,
    vendor_symbol  TEXT NOT NULL,
    price_date     TEXT NOT NULL,
    open           REAL,
    high           REAL,
    low            REAL,
    close          REAL,
    adj_close      REAL,
    volume         REAL,
    dividends      REAL,
    splits         REAL,
    reported_unit  TEXT,
    run_id         TEXT NOT NULL REFERENCES ingest_run (run_id)
);

CREATE INDEX IF NOT EXISTS ix_raw_price_partition
    ON raw_price (source, snapshot_date);

CREATE INDEX IF NOT EXISTS ix_raw_price_key
    ON raw_price (vendor_symbol, price_date);
