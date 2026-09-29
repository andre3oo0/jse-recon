-- Turn daily comparisons into episodes of disagreement; restatement recons are one-off events, so they are left out.
INSERT INTO break_episode (
    recon_name, key_id, price_date, first_seen, last_seen, cleared_on, observations,
    first_status, latest_status, latest_explanation, age_days, state
)
WITH obs AS (
    SELECT r.recon_name, x.key_id, x.price_date, r.snapshot_b AS as_of, x.status, x.explanation,
           x.status <> 'MATCH' AS is_break
    FROM recon_result x
    JOIN recon_run r ON r.recon_run_id = x.recon_run_id
    WHERE r.recon_name NOT LIKE '%\_restatement' ESCAPE '\'
),
-- An unbroken run of breaking comparisons is one episode; a day the key was not compared neither breaks nor clears it
ordered AS (
    SELECT
        o.*,
        ROW_NUMBER() OVER k
      - ROW_NUMBER() OVER (PARTITION BY o.recon_name, o.key_id, o.price_date, o.is_break ORDER BY o.as_of) AS grp,
        LEAD(o.as_of) OVER k AS next_as_of
    FROM obs o
    WINDOW k AS (PARTITION BY o.recon_name, o.key_id, o.price_date ORDER BY o.as_of)
),
island AS (
    SELECT
        d.*,
        FIRST_VALUE(d.status) OVER w AS first_status,
        LAST_VALUE(d.status) OVER w AS latest_status,
        LAST_VALUE(d.explanation) OVER w AS latest_explanation,
        LAST_VALUE(d.next_as_of) OVER w AS cleared_on  -- the comparison after the last break always matched
    FROM ordered d
    WHERE d.is_break
    WINDOW w AS (
        PARTITION BY d.recon_name, d.key_id, d.price_date, d.grp ORDER BY d.as_of
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )
),
episodes AS (
    SELECT
        recon_name, key_id, price_date,
        MIN(as_of) AS first_seen,
        MAX(as_of) AS last_seen,
        MAX(cleared_on) AS cleared_on,
        COUNT(*) AS observations,
        MAX(first_status) AS first_status,
        MAX(latest_status) AS latest_status,
        MAX(latest_explanation) AS latest_explanation,
        (SELECT MAX(snapshot_b) FROM recon_run r WHERE r.recon_name = island.recon_name) AS recon_latest
    FROM island
    GROUP BY recon_name, key_id, price_date, grp
),
aged AS (
    SELECT
        e.*,
        (SELECT COUNT(*) FROM trading_calendar c
         WHERE c.is_trading_day = 1
           AND c.cal_date > e.first_seen
           AND c.cal_date <= COALESCE(e.cleared_on, e.recon_latest)) AS age_days
    FROM episodes e
)
SELECT
    recon_name, key_id, price_date, first_seen, last_seen, cleared_on, observations,
    first_status, latest_status, latest_explanation, age_days,
    CASE
        WHEN cleared_on IS NULL THEN 'OPEN'
        WHEN age_days <= :timing_days THEN 'TIMING'
        ELSE 'CLEARED'
    END
FROM aged
