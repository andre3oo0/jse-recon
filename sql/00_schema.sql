-- Warehouse schema. Needs SQLite 3.39+ for FULL OUTER JOIN in the recon layer.
PRAGMA user_version = 3;  -- bump on any change so src/db.py asks for a rebuild
PRAGMA foreign_keys = ON;

-- One row per security, keyed on the current JSE alpha code; ISIN should replace it once populated.
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

-- Vendor symbol to security; validity dates let one security carry several symbols over time.
CREATE TABLE IF NOT EXISTS security_xref (
    source         TEXT NOT NULL,
    vendor_symbol  TEXT NOT NULL,
    security_id    TEXT NOT NULL REFERENCES security_master (security_id),
    valid_from     TEXT,
    valid_to       TEXT,
    PRIMARY KEY (source, vendor_symbol)
);

CREATE TABLE IF NOT EXISTS trading_calendar (
    cal_date        TEXT PRIMARY KEY,
    is_trading_day  INTEGER NOT NULL CHECK (is_trading_day IN (0, 1)),
    reason          TEXT
);

CREATE TABLE IF NOT EXISTS ingest_run (
    run_id             TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    snapshot_date      TEXT NOT NULL,
    fetched_at         TEXT NOT NULL,
    session_cutoff     TEXT NOT NULL,  -- last date whose session was complete when fetched
    lookback_period    TEXT NOT NULL,
    symbols_requested  INTEGER NOT NULL,
    symbols_returned   INTEGER NOT NULL,
    rows_landed        INTEGER NOT NULL,
    landing_path       TEXT NOT NULL,
    landing_sha256     TEXT NOT NULL
);

-- Per-symbol outcome of each run, so a symbol that returns nothing is recorded rather than skipped.
CREATE TABLE IF NOT EXISTS ingest_symbol_status (
    run_id         TEXT NOT NULL REFERENCES ingest_run (run_id),
    vendor_symbol  TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('ok', 'empty', 'error')),
    row_count      INTEGER NOT NULL,
    reported_unit  TEXT,
    error          TEXT,
    PRIMARY KEY (run_id, vendor_symbol)
);

-- No primary key on purpose: vendor duplicates must reach the recon as DUP breaks.
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

CREATE INDEX IF NOT EXISTS ix_raw_price_partition ON raw_price (source, snapshot_date);
CREATE INDEX IF NOT EXISTS ix_raw_price_key ON raw_price (vendor_symbol, price_date);

-- Raw prices mapped to securities, in rands, with calendar and anomaly flags. Derived; rebuilt each run.
CREATE TABLE IF NOT EXISTS stg_price (
    source          TEXT NOT NULL,
    snapshot_date   TEXT NOT NULL,
    security_id     TEXT,  -- NULL when the vendor symbol has no mapping
    vendor_symbol   TEXT NOT NULL,
    price_date      TEXT NOT NULL,
    close_zar       REAL,
    adj_close_zar   REAL,
    volume          REAL,
    reported_unit   TEXT,
    unit_factor     REAL,  -- NULL when the reported unit is unrecognised
    is_trading_day  INTEGER,
    unit_anomaly    TEXT CHECK (unit_anomaly IN ('too_small', 'too_large')),
    ratio_ref1      REAL,  -- close divided by the nearest bar
    ratio_ref2      REAL,  -- close divided by the second-nearest reference bar
    run_id          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_stg_price_lookup ON stg_price (source, snapshot_date, vendor_symbol, price_date);
CREATE INDEX IF NOT EXISTS ix_stg_price_key ON stg_price (security_id, price_date);
