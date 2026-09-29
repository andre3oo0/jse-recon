-- Data quality views over stg_price, recreated with every staging build.

-- Every trading day a symbol should have a bar for: from its first bar to the run's last complete session
DROP VIEW IF EXISTS v_expected_bar;
CREATE VIEW v_expected_bar AS
SELECT s.source, s.snapshot_date, s.vendor_symbol, s.security_id, c.cal_date AS price_date
FROM (
    SELECT p.source, p.snapshot_date, p.vendor_symbol, p.security_id,
           MIN(p.price_date) AS first_bar, r.session_cutoff
    FROM stg_price p
    JOIN ingest_run r ON r.run_id = p.run_id
    GROUP BY p.source, p.snapshot_date, p.vendor_symbol, p.security_id, r.session_cutoff
) s
JOIN trading_calendar c
    ON  c.cal_date BETWEEN s.first_bar AND s.session_cutoff
    AND c.is_trading_day = 1;

DROP VIEW IF EXISTS v_calendar_exception;
CREATE VIEW v_calendar_exception AS
SELECT e.source, e.snapshot_date, e.vendor_symbol, e.security_id, e.price_date,
       'MISSING_BAR' AS exception, NULL AS reason, NULL AS volume
FROM v_expected_bar e
WHERE NOT EXISTS (
    SELECT 1 FROM stg_price p
    WHERE p.source = e.source
      AND p.snapshot_date = e.snapshot_date
      AND p.vendor_symbol = e.vendor_symbol
      AND p.price_date = e.price_date
)
UNION ALL
SELECT p.source, p.snapshot_date, p.vendor_symbol, p.security_id, p.price_date,
       'CLOSED_DAY_BAR', c.reason, p.volume
FROM stg_price p
JOIN trading_calendar c ON c.cal_date = p.price_date AND c.is_trading_day = 0;

-- Missing bars per session, so a whole-market gap reads differently from one quiet symbol
DROP VIEW IF EXISTS v_session_gap;
CREATE VIEW v_session_gap AS
SELECT
    e.source,
    e.snapshot_date,
    e.price_date,
    COUNT(DISTINCT e.vendor_symbol) AS symbols_expected,
    COUNT(DISTINCT e.vendor_symbol) - COUNT(DISTINCT p.vendor_symbol) AS symbols_missing
FROM v_expected_bar e
LEFT JOIN stg_price p
    ON  p.source = e.source
    AND p.snapshot_date = e.snapshot_date
    AND p.vendor_symbol = e.vendor_symbol
    AND p.price_date = e.price_date
GROUP BY e.source, e.snapshot_date, e.price_date
HAVING COUNT(DISTINCT e.vendor_symbol) > COUNT(DISTINCT p.vendor_symbol);
