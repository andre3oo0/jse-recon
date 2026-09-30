-- The price the fund should use for each security and trading day, the source it came from, and why.
INSERT INTO approved_price (security_id, price_date, close_zar, source, status, reason)
WITH latest AS (
    SELECT source, security_id, price_date, close_zar, unit_anomaly
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY source, security_id, price_date ORDER BY snapshot_date DESC) AS n
        FROM stg_price
        WHERE security_id IS NOT NULL AND is_trading_day = 1 AND close_zar > 0 AND source IN (:primary, :secondary)
    )
    WHERE n = 1
),
p AS (SELECT * FROM latest WHERE source = :primary),
s AS (SELECT * FROM latest WHERE source = :secondary),
moved AS (SELECT DISTINCT security_id, price_date FROM v_price_move WHERE source = :primary),
noted AS (
    SELECT key_id AS security_id, price_date, resolution FROM break_note WHERE recon_name = :pair
),
open_break AS (
    SELECT key_id AS security_id, price_date FROM break_episode WHERE recon_name = :pair AND state = 'OPEN'
),
-- Every trading day from a security's first primary price to the latest day either source has priced
days AS (
    SELECT f.security_id, c.cal_date AS price_date
    FROM (SELECT security_id, MIN(price_date) AS first_day FROM p GROUP BY security_id) f
    JOIN trading_calendar c
        ON c.is_trading_day = 1
       AND c.cal_date BETWEEN f.first_day AND (SELECT MAX(price_date) FROM latest)
),
judged AS (
    SELECT
        d.security_id,
        d.price_date,
        p.close_zar AS p_close,
        s.close_zar AS s_close,
        p.close_zar IS NOT NULL AND p.unit_anomaly IS NULL
            AND COALESCE(np.resolution, '') <> 'VENDOR_ERROR_A' AS p_usable,
        s.close_zar IS NOT NULL AND s.unit_anomaly IS NULL
            AND COALESCE(np.resolution, '') <> 'VENDOR_ERROR_B' AS s_usable,
        m.security_id IS NOT NULL AS moved,
        o.security_id IS NOT NULL AS in_break,
        np.resolution AS resolution,
        CASE
            WHEN p.close_zar IS NULL THEN 'the primary source had no price'
            WHEN p.unit_anomaly IS NOT NULL THEN 'the primary price is about 100x off'
            ELSE 'the primary price is recorded as a vendor error'
        END AS p_problem
    FROM days d
    LEFT JOIN p ON p.security_id = d.security_id AND p.price_date = d.price_date
    LEFT JOIN s ON s.security_id = d.security_id AND s.price_date = d.price_date
    LEFT JOIN moved m ON m.security_id = d.security_id AND m.price_date = d.price_date
    LEFT JOIN open_break o ON o.security_id = d.security_id AND o.price_date = d.price_date
    LEFT JOIN noted np ON np.security_id = d.security_id AND np.price_date = d.price_date
)
SELECT
    j.security_id,
    j.price_date,
    CASE
        WHEN j.p_usable THEN j.p_close
        WHEN j.s_usable THEN j.s_close
        ELSE (SELECT p2.close_zar FROM p p2
              WHERE p2.security_id = j.security_id AND p2.price_date < j.price_date AND p2.unit_anomaly IS NULL
              ORDER BY p2.price_date DESC LIMIT 1)
    END,
    CASE WHEN j.p_usable THEN :primary WHEN j.s_usable THEN :secondary ELSE 'previous close' END,
    CASE
        WHEN j.p_usable AND j.in_break AND j.resolution IS NULL THEN 'TO_VERIFY'
        WHEN j.p_usable AND j.moved THEN 'TO_VERIFY'
        WHEN j.p_usable THEN 'APPROVED'
        WHEN j.s_usable THEN 'SECONDARY'
        ELSE 'FALLBACK'
    END,
    CASE
        WHEN j.p_usable AND j.in_break AND j.resolution IS NULL
            THEN 'The sources disagree and the break has no resolution yet: confirm before use'
        WHEN j.p_usable AND j.moved
            THEN 'Moved 15% or more when the market did not: verify against company news before use'
        WHEN j.p_usable AND j.resolution = 'VENDOR_ERROR_B'
            THEN 'The sources disagreed; the secondary source is recorded as the error'
        WHEN j.p_usable THEN 'Primary source, passed every check'
        WHEN j.s_usable THEN 'Secondary source used: ' || j.p_problem
        ELSE 'No usable price: ' || j.p_problem ||
             '; the previous close is carried, and must be verified as fair and reasonable (ASISA s4.2.2)'
    END
FROM judged j
