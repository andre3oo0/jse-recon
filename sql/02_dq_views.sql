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

-- Thresholds for the views below, written by staging.build from tolerance_rules.yaml
CREATE TABLE IF NOT EXISTS dq_setting (name TEXT PRIMARY KEY, value REAL NOT NULL);

-- Day-on-day movement exceptions: a large move the median share did not share; unit anomalies are reported separately
DROP VIEW IF EXISTS v_price_move;
CREATE VIEW v_price_move AS
WITH moves AS (
    SELECT p.source, p.snapshot_date, p.security_id, p.vendor_symbol, p.price_date, p.close_zar, p.adj_close_zar,
           p.volume,
           LAG(p.close_zar) OVER w AS prev_close,
           LAG(p.adj_close_zar) OVER w AS prev_adj_close,
           LAG(p.price_date) OVER w AS prev_date
    FROM stg_price p
    WHERE p.is_trading_day = 1 AND p.unit_anomaly IS NULL AND p.unit_factor IS NOT NULL AND p.close_zar > 0
    WINDOW w AS (PARTITION BY p.source, p.snapshot_date, p.vendor_symbol ORDER BY p.price_date)
),
returns AS (
    SELECT m.*, m.close_zar / m.prev_close - 1 AS ret,
           m.adj_close_zar / NULLIF(m.prev_adj_close, 0) - 1 AS adj_ret  -- small when a distribution explains the fall
    FROM moves m WHERE m.prev_close > 0
),
ranked AS (
    SELECT r.*,
           ROW_NUMBER() OVER d AS rn,
           COUNT(*) OVER (PARTITION BY r.source, r.snapshot_date, r.price_date) AS n
    FROM returns r
    WINDOW d AS (PARTITION BY r.source, r.snapshot_date, r.price_date ORDER BY r.ret)
),
-- SQLite has no MEDIAN: average the middle one or two returns of the day
market AS (
    SELECT source, snapshot_date, price_date, AVG(ret) AS market_ret, MAX(n) AS market_symbols
    FROM ranked
    WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
    GROUP BY source, snapshot_date, price_date
)
SELECT r.source, r.snapshot_date, r.security_id, r.vendor_symbol, r.prev_date, r.price_date,
       r.prev_close, r.close_zar, r.volume, r.ret, r.adj_ret, m.market_ret, r.ret - m.market_ret AS excess_ret,
       m.market_symbols
FROM ranked r
JOIN market m USING (source, snapshot_date, price_date)
WHERE ABS(r.ret) >= (SELECT value FROM dq_setting WHERE name = 'move_abs')
  AND ABS(r.ret - m.market_ret) >= (SELECT value FROM dq_setting WHERE name = 'move_excess')
  AND m.market_symbols >= (SELECT value FROM dq_setting WHERE name = 'move_min_market');
