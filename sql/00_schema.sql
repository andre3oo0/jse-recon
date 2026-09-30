-- Warehouse schema. Needs SQLite 3.39+ for FULL OUTER JOIN in the recon layer.
PRAGMA user_version = 10;  -- bump on any change so src/db.py asks for a rebuild
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

-- Unit to assume when a vendor reports none; loaded from config/sources.yaml at each staging build.
CREATE TABLE IF NOT EXISTS source_unit (
    source        TEXT PRIMARY KEY,
    assumed_unit  TEXT
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
    isin           TEXT,  -- as published by the vendor, for checking against security_master
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
    reported_unit   TEXT,  -- what the vendor said, or the configured assumption when it said nothing
    unit_assumed    INTEGER NOT NULL DEFAULT 0,
    unit_factor     REAL,  -- NULL when the unit is unrecognised
    is_trading_day  INTEGER,
    unit_anomaly    TEXT CHECK (unit_anomaly IN ('too_small', 'too_large')),
    ratio_ref1      REAL,  -- close divided by the nearest bar
    ratio_ref2      REAL,  -- close divided by the second-nearest reference bar
    stale_days      INTEGER,  -- consecutive sessions with this exact close, ending at this bar
    run_id          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_stg_price_lookup ON stg_price (source, snapshot_date, vendor_symbol, price_date);
CREATE INDEX IF NOT EXISTS ix_stg_price_key ON stg_price (security_id, price_date);

-- One comparison of side A against side B; the id is deterministic, so a rerun replaces it.
CREATE TABLE IF NOT EXISTS recon_run (
    recon_run_id    TEXT PRIMARY KEY,
    recon_name      TEXT NOT NULL,
    source_a        TEXT NOT NULL,
    snapshot_a      TEXT NOT NULL,
    source_b        TEXT NOT NULL,
    snapshot_b      TEXT NOT NULL,
    window_start    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    abs_floor_zar   REAL NOT NULL,
    rel_pct         REAL NOT NULL,
    run_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Every key compared, matched or not, with both values and the rule that decided it.
CREATE TABLE IF NOT EXISTS recon_result (
    recon_run_id  TEXT NOT NULL REFERENCES recon_run (recon_run_id),
    key_id        TEXT NOT NULL,  -- security_id, or UNMAPPED:<vendor symbol>
    price_date    TEXT NOT NULL,
    status        TEXT NOT NULL
                  CHECK (status IN ('MATCH', 'VAL', 'ONE_A', 'ONE_B', 'DUP', 'UNIT', 'CAL', 'STALE', 'SCALE')),
    close_a       REAL,
    close_b       REAL,
    diff_zar      REAL,
    diff_pct      REAL,
    rows_a        INTEGER,
    rows_b        INTEGER,
    volume_a      REAL,  -- compared for information only; vendors count off-book trades differently
    volume_b      REAL,
    explanation   TEXT,
    PRIMARY KEY (recon_run_id, key_id, price_date)
);

CREATE INDEX IF NOT EXISTS ix_recon_result_status ON recon_result (recon_run_id, status);

-- One row per episode of disagreement: from the first sighting until a comparison that matches. Derived; rebuilt.
CREATE TABLE IF NOT EXISTS break_episode (
    recon_name       TEXT NOT NULL,
    key_id           TEXT NOT NULL,
    price_date       TEXT NOT NULL,
    first_seen       TEXT NOT NULL,  -- snapshot date of the first comparison that broke
    last_seen        TEXT NOT NULL,  -- snapshot date of the latest comparison that broke
    cleared_on       TEXT,  -- snapshot date of the first matching comparison after last_seen
    observations     INTEGER NOT NULL,
    first_status     TEXT NOT NULL,
    latest_status    TEXT NOT NULL,
    latest_explanation TEXT,
    age_days         INTEGER NOT NULL,  -- trading days from first_seen to cleared_on, or to the latest comparison
    price_age_days   INTEGER NOT NULL,  -- trading days from the price date itself to the same end
    found_on_first_comparison INTEGER NOT NULL,  -- 1 when the break was in the first comparison this key ever had
    latest_diff_pct  REAL,  -- B against A, percent, in the latest breaking comparison
    state            TEXT NOT NULL CHECK (state IN ('OPEN', 'TIMING', 'CLEARED')),
    PRIMARY KEY (recon_name, key_id, price_date, first_seen)
);

-- The price to use for each security and trading day, from the price hierarchy, with the reason it was chosen.
CREATE TABLE IF NOT EXISTS approved_price (
    security_id  TEXT NOT NULL,
    price_date   TEXT NOT NULL,
    close_zar    REAL,  -- NULL only when no clean price has ever been seen
    source       TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('APPROVED', 'TO_VERIFY', 'SECONDARY', 'FALLBACK')),
    reason       TEXT NOT NULL,
    PRIMARY KEY (security_id, price_date)
);

-- Analyst notes from config/break_notes.yaml: the one part of the register that cannot be derived.
CREATE TABLE IF NOT EXISTS break_note (
    recon_name  TEXT NOT NULL,
    key_id      TEXT NOT NULL,
    price_date  TEXT NOT NULL,
    resolution  TEXT NOT NULL
                CHECK (resolution IN ('VENDOR_ERROR_A', 'VENDOR_ERROR_B', 'TIMING', 'ACCEPTED', 'INVESTIGATING')),
    note        TEXT NOT NULL,
    author      TEXT,
    noted_on    TEXT,
    PRIMARY KEY (recon_name, key_id, price_date)
);

-- A broker holdings statement as received; file_id is its sha256, so reloading the same file is a no-op.
CREATE TABLE IF NOT EXISTS holding_file (
    file_id    TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    path       TEXT NOT NULL,
    lines      INTEGER NOT NULL,
    synthetic  INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    loaded_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Statement lines exactly as written, strings and all; parsing happens in stg_holding.
CREATE TABLE IF NOT EXISTS raw_holding (
    file_id         TEXT NOT NULL REFERENCES holding_file (file_id),
    line_no         INTEGER NOT NULL,
    name            TEXT,
    contract_code   TEXT,
    purchase_value  TEXT,
    current_value   TEXT,
    current_price   TEXT,
    isin            TEXT,
    PRIMARY KEY (file_id, line_no)
);

-- The internal book of record, on a trade-date basis.
CREATE TABLE IF NOT EXISTS ledger_txn (
    reference    TEXT PRIMARY KEY,
    trade_date   TEXT NOT NULL,
    security_id  TEXT NOT NULL,
    side         TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity     REAL NOT NULL CHECK (quantity > 0),
    price_zar    REAL NOT NULL,
    fees_zar     REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stg_holding (
    file_id             TEXT NOT NULL,
    line_no             INTEGER NOT NULL,
    key_id              TEXT NOT NULL,  -- security_id, or UNMAPPED:<contract code>
    mapped_by           TEXT CHECK (mapped_by IN ('isin', 'code')),
    contract_code       TEXT,
    isin                TEXT,
    purchase_value_zar  REAL,
    current_value_zar   REAL,
    current_price_zar   REAL,
    parse_errors        TEXT,  -- NULL when every amount on the line was read
    PRIMARY KEY (file_id, line_no)
);

CREATE TABLE IF NOT EXISTS holding_recon_result (
    file_id         TEXT NOT NULL REFERENCES holding_file (file_id),
    key_id          TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN (
                        'MATCH', 'DUP', 'ONE_A', 'ONE_B', 'PARSE', 'UNIT', 'SETTLE', 'QTY', 'STALE', 'PRICE', 'NOPRICE')),
    broker_lines    INTEGER,
    broker_value    REAL,
    broker_price    REAL,
    implied_qty     REAL,  -- broker value divided by broker price
    book_qty        REAL,  -- trade-date basis
    settled_qty     REAL,  -- trades settled by the statement date
    our_price       REAL,
    prev_price      REAL,
    book_value      REAL,  -- book quantity at our independently checked price
    value_diff      REAL,  -- what the statement over- or understates against that valuation
    explanation     TEXT,
    PRIMARY KEY (file_id, key_id)
);
