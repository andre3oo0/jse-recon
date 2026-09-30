-- Match side A against side B on security and date, and classify every key, matched or not.
INSERT INTO recon_result (
    recon_run_id, key_id, price_date, status, close_a, close_b,
    diff_zar, diff_pct, rows_a, rows_b, volume_a, volume_b, explanation
)
-- Only securities both runs asked the vendor for; a universe change is a scope difference, not a break
WITH scope AS (
    SELECT x.security_id
    FROM ingest_symbol_status s
    JOIN ingest_run r ON r.run_id = s.run_id
    JOIN security_xref x ON x.source = r.source AND x.vendor_symbol = s.vendor_symbol
    WHERE r.source = :src_a AND r.snapshot_date = :snap_a
    INTERSECT
    SELECT x.security_id
    FROM ingest_symbol_status s
    JOIN ingest_run r ON r.run_id = s.run_id
    JOIN security_xref x ON x.source = r.source AND x.vendor_symbol = s.vendor_symbol
    WHERE r.source = :src_b AND r.snapshot_date = :snap_b
),
side_a AS (
    SELECT
        COALESCE(security_id, 'UNMAPPED:' || vendor_symbol) AS key_id,
        price_date,
        COUNT(*) AS n,
        MAX(close_zar) AS close_zar,
        MAX(volume) AS volume,
        COUNT(DISTINCT close_zar) AS distinct_closes,
        MAX(unit_anomaly IS NOT NULL OR unit_factor IS NULL) AS unit_flag,
        MAX(stale_days) AS stale_days,
        MAX(is_trading_day) AS is_trading_day
    FROM stg_price
    WHERE source = :src_a AND snapshot_date = :snap_a AND price_date BETWEEN :lo AND :hi
      AND (security_id IS NULL OR security_id IN (SELECT security_id FROM scope))
    GROUP BY 1, 2
),
side_b AS (
    SELECT
        COALESCE(security_id, 'UNMAPPED:' || vendor_symbol) AS key_id,
        price_date,
        COUNT(*) AS n,
        MAX(close_zar) AS close_zar,
        MAX(volume) AS volume,
        COUNT(DISTINCT close_zar) AS distinct_closes,
        MAX(unit_anomaly IS NOT NULL OR unit_factor IS NULL) AS unit_flag,
        MAX(stale_days) AS stale_days,
        MAX(is_trading_day) AS is_trading_day
    FROM stg_price
    WHERE source = :src_b AND snapshot_date = :snap_b AND price_date BETWEEN :lo AND :hi
      AND (security_id IS NULL OR security_id IN (SELECT security_id FROM scope))
    GROUP BY 1, 2
),
-- FULL OUTER, not INNER: an inner join silently drops the one-sided rows a recon exists to catch
joined AS (
    SELECT
        COALESCE(a.key_id, b.key_id) AS key_id,
        COALESCE(a.price_date, b.price_date) AS price_date,
        a.n AS rows_a,
        b.n AS rows_b,
        a.close_zar AS close_a,
        b.close_zar AS close_b,
        a.volume AS volume_a,
        b.volume AS volume_b,
        b.close_zar - a.close_zar AS diff_zar,
        (b.close_zar - a.close_zar) / NULLIF(ABS(a.close_zar), 0) * 100 AS diff_pct,
        b.close_zar / NULLIF(a.close_zar, 0) AS ratio,
        COALESCE(a.distinct_closes, 0) > 1 OR COALESCE(b.distinct_closes, 0) > 1 AS dup_disagrees,
        a.unit_flag AS unit_a,
        b.unit_flag AS unit_b,
        a.stale_days AS stale_a,
        b.stale_days AS stale_b,
        COALESCE(a.is_trading_day, b.is_trading_day) AS is_trading_day
    FROM side_a a
    FULL OUTER JOIN side_b b
        ON  a.key_id = b.key_id
        AND a.price_date = b.price_date
),
classified AS (
    SELECT
        j.*,
        CASE
            WHEN j.rows_a > 1 OR j.rows_b > 1 THEN 'DUP'
            WHEN j.rows_b IS NULL THEN CASE WHEN j.is_trading_day = 0 THEN 'CAL' ELSE 'ONE_A' END
            WHEN j.rows_a IS NULL THEN CASE WHEN j.is_trading_day = 0 THEN 'CAL' ELSE 'ONE_B' END
            WHEN j.close_a IS NULL OR j.close_b IS NULL OR j.unit_a <> j.unit_b THEN 'UNIT'
            WHEN j.ratio BETWEEN :unit_lo AND :unit_hi OR j.ratio BETWEEN 1.0 / :unit_hi AND 1.0 / :unit_lo THEN 'UNIT'
            -- More than 50% apart is a scale disagreement whatever the ratio, so a 99% break never reads as stale
            WHEN ABS(j.diff_pct) > :scale_pct THEN 'SCALE'
            WHEN ABS(j.diff_zar) > :abs_floor AND ABS(j.diff_pct) > :rel_pct THEN
                CASE WHEN MAX(j.stale_a, j.stale_b) >= :stale_days THEN 'STALE' ELSE 'VAL' END
            ELSE 'MATCH'
        END AS status
    FROM joined j
)
SELECT
    :run_id,
    key_id,
    price_date,
    status,
    close_a,
    close_b,
    diff_zar,
    diff_pct,
    rows_a,
    rows_b,
    volume_a,
    volume_b,
    CASE status
        WHEN 'DUP' THEN printf('%d row(s) in A, %d in B%s', COALESCE(rows_a, 0), COALESCE(rows_b, 0),
                               CASE WHEN dup_disagrees THEN ', with different prices' ELSE '' END)
        WHEN 'ONE_A' THEN 'In A only'
        WHEN 'ONE_B' THEN 'In B only'
        WHEN 'CAL' THEN 'One side has a bar on a day the JSE was closed'
        WHEN 'UNIT' THEN
            CASE
                WHEN close_a IS NULL OR close_b IS NULL THEN 'Unrecognised unit on one side'
                WHEN unit_a OR unit_b THEN printf('Unit anomaly flagged in staging; B/A = %.4f', ratio)
                ELSE printf('B/A = %.4f, about 100x apart', ratio)
            END
        WHEN 'SCALE' THEN printf('Differs by R%.2f (%.2f%%): the sources disagree on scale, B/A = %.4f',
                                 diff_zar, diff_pct, ratio)
        WHEN 'STALE' THEN printf('Differs by R%.2f (%.2f%%); one side unchanged for %d sessions',
                                 diff_zar, diff_pct, MAX(stale_a, stale_b))
        WHEN 'VAL' THEN printf('Differs by R%.2f (%.2f%%), over both R%.2f and %.2f%%',
                               diff_zar, diff_pct, :abs_floor, :rel_pct)
        WHEN 'MATCH' THEN CASE WHEN unit_a AND unit_b THEN 'Both sides carry the same unit anomaly' END
    END
FROM classified
