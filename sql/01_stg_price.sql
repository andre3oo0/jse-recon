-- Map symbols to securities, convert to rands, attach the calendar, and flag (never repair) unit anomalies.
INSERT INTO stg_price (
    source, snapshot_date, security_id, vendor_symbol, price_date,
    close_zar, adj_close_zar, volume, reported_unit, unit_assumed, unit_factor,
    is_trading_day, unit_anomaly, ratio_ref1, ratio_ref2, stale_days, run_id
)
WITH unit AS (
    SELECT r.*, COALESCE(r.reported_unit, u.assumed_unit) AS unit, r.reported_unit IS NULL AND u.assumed_unit IS NOT NULL AS unit_assumed
    FROM raw_price r
    LEFT JOIN source_unit u ON u.source = r.source
),
mapped AS (
    SELECT
        r.*,
        x.security_id,
        CASE r.unit WHEN 'ZAc' THEN 0.01 WHEN 'ZAR' THEN 1.0 END AS unit_factor
    FROM unit r
    LEFT JOIN security_xref x
        ON  x.source = r.source
        AND x.vendor_symbol = r.vendor_symbol
        AND r.price_date >= COALESCE(x.valid_from, '0000-01-01')
        AND r.price_date <= COALESCE(x.valid_to, '9999-12-31')
),
neighbours AS (
    SELECT
        m.*,
        LAG(m.close, 1) OVER w AS prev1,
        LAG(m.close, 2) OVER w AS prev2,
        LEAD(m.close, 1) OVER w AS next1,
        LEAD(m.close, 2) OVER w AS next2
    FROM mapped m
    WINDOW w AS (PARTITION BY m.source, m.snapshot_date, m.vendor_symbol ORDER BY m.price_date)
),
-- The two nearest bars: one each side mid-series, or the two nearest on one side at an edge
refs AS (
    SELECT
        n.*,
        n.close / COALESCE(n.prev1, n.next1) AS ratio_ref1,
        n.close / CASE
            WHEN n.prev1 IS NOT NULL AND n.next1 IS NOT NULL THEN n.next1
            WHEN n.prev1 IS NULL THEN n.next2
            ELSE n.prev2
        END AS ratio_ref2
    FROM neighbours n
),
-- Gaps and islands: bars in an unbroken run of the same close share a run_grp
runs AS (
    SELECT
        f.*,
        ROW_NUMBER() OVER (PARTITION BY f.source, f.snapshot_date, f.vendor_symbol ORDER BY f.price_date)
      - ROW_NUMBER() OVER (PARTITION BY f.source, f.snapshot_date, f.vendor_symbol, f.close ORDER BY f.price_date)
        AS run_grp
    FROM refs f
)
SELECT
    f.source,
    f.snapshot_date,
    f.security_id,
    f.vendor_symbol,
    f.price_date,
    f.close * f.unit_factor,
    f.adj_close * f.unit_factor,
    f.volume,
    f.unit,
    f.unit_assumed,
    f.unit_factor,
    c.is_trading_day,
    -- ~100x off both references is a unit glitch; a real consolidation moves once, so one reference clears it
    CASE
        WHEN f.ratio_ref1 BETWEEN 1.0 / :unit_hi AND 1.0 / :unit_lo
         AND f.ratio_ref2 BETWEEN 1.0 / :unit_hi AND 1.0 / :unit_lo THEN 'too_small'
        WHEN f.ratio_ref1 BETWEEN :unit_lo AND :unit_hi
         AND f.ratio_ref2 BETWEEN :unit_lo AND :unit_hi THEN 'too_large'
    END,
    f.ratio_ref1,
    f.ratio_ref2,
    ROW_NUMBER() OVER (
        PARTITION BY f.source, f.snapshot_date, f.vendor_symbol, f.close, f.run_grp ORDER BY f.price_date
    ),
    f.run_id
FROM runs f
LEFT JOIN trading_calendar c ON c.cal_date = f.price_date
