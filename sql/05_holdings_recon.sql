-- Broker statement against the internal book, valued at our own checked close; every position gets one status.
INSERT INTO holding_recon_result (
    file_id, key_id, status, broker_lines, broker_value, broker_price, implied_qty, book_qty, settled_qty,
    our_price, prev_price, book_value, value_diff, explanation
)
WITH broker AS (
    SELECT key_id,
           COUNT(*) AS lines,
           SUM(current_value_zar) AS value,
           MAX(current_price_zar) AS price,
           MAX(parse_errors IS NOT NULL) AS parse_bad,
           group_concat(parse_errors, '; ') AS parse_errors,
           MAX(mapped_by) AS mapped_by,
           MAX(contract_code) AS contract_code
    FROM stg_holding
    WHERE file_id = :file_id
    GROUP BY key_id
),
-- Settlement is the settle_days-th trading day after the trade, on the JSE calendar
txn AS (
    SELECT t.security_id,
           CASE t.side WHEN 'BUY' THEN t.quantity ELSE -t.quantity END AS signed,
           (SELECT c.cal_date FROM trading_calendar c
            WHERE c.is_trading_day = 1 AND c.cal_date > t.trade_date
            ORDER BY c.cal_date LIMIT 1 OFFSET :settle_days - 1) AS settle_date
    FROM ledger_txn t
    WHERE t.trade_date <= :as_of
),
book AS (
    SELECT security_id AS key_id,
           SUM(signed) AS qty,
           SUM(CASE WHEN settle_date <= :as_of THEN signed ELSE 0 END) AS settled_qty
    FROM txn
    GROUP BY security_id
    HAVING ABS(SUM(signed)) > 1e-9
),
-- Our price: the close on the statement date from the latest snapshot that covers it, and the session before
priced AS (
    SELECT security_id, price_date, close_zar FROM (
        SELECT security_id, price_date, close_zar,
               ROW_NUMBER() OVER (PARTITION BY security_id, price_date ORDER BY snapshot_date DESC) AS latest
        FROM stg_price
        WHERE source = :price_source AND price_date IN (:as_of, :prev_day)
    )
    WHERE latest = 1
),
joined AS (
    SELECT
        COALESCE(b.key_id, k.key_id) AS key_id,
        b.lines, b.value AS broker_value, b.price AS broker_price, b.parse_bad, b.parse_errors,
        b.mapped_by, b.contract_code,
        b.value / NULLIF(b.price, 0) AS implied_qty,
        k.qty AS book_qty, k.settled_qty,
        (SELECT close_zar FROM priced WHERE security_id = COALESCE(b.key_id, k.key_id) AND price_date = :as_of) AS our_price,
        (SELECT close_zar FROM priced WHERE security_id = COALESCE(b.key_id, k.key_id) AND price_date = :prev_day) AS prev_price
    FROM broker b
    FULL OUTER JOIN book k ON k.key_id = b.key_id
),
measured AS (
    SELECT
        j.*,
        j.book_qty * j.our_price AS book_value,
        ABS(j.broker_value - j.book_qty * j.broker_price) AS position_gap,
        ABS(j.broker_value - j.settled_qty * j.broker_price) AS settled_gap,
        j.book_qty * j.broker_price AS position_base
    FROM joined j
),
classified AS (
    SELECT
        m.*,
        CASE
            WHEN m.lines > 1 THEN 'DUP'
            WHEN m.book_qty IS NULL THEN 'ONE_B'
            WHEN m.lines IS NULL THEN 'ONE_A'
            WHEN m.parse_bad THEN 'PARSE'
            WHEN m.broker_price / m.our_price BETWEEN :unit_lo AND :unit_hi
              OR m.broker_price / m.our_price BETWEEN 1.0 / :unit_hi AND 1.0 / :unit_lo THEN 'UNIT'
            -- The broker's own price, so a stale or wrong price cannot masquerade as a quantity difference
            WHEN m.position_gap > :pos_floor AND m.position_gap / ABS(m.position_base) * 100 > :pos_pct THEN
                CASE WHEN m.settled_gap <= :pos_floor OR m.settled_gap / ABS(m.position_base) * 100 <= :pos_pct
                     THEN 'SETTLE' ELSE 'QTY' END
            WHEN m.our_price IS NULL THEN 'NOPRICE'
            WHEN ABS(m.broker_price - m.our_price) > :px_floor
             AND ABS(m.broker_price - m.our_price) / m.our_price * 100 > :px_pct THEN
                CASE WHEN ABS(m.broker_price - m.prev_price) <= :px_floor THEN 'STALE' ELSE 'PRICE' END
            ELSE 'MATCH'
        END AS status
    FROM measured m
)
SELECT
    :file_id, key_id, status, lines, broker_value, broker_price, implied_qty, book_qty, settled_qty,
    our_price, prev_price, book_value,
    -- An unreadable amount or a missing price makes the difference unknown, not zero
    CASE WHEN status IN ('PARSE', 'NOPRICE') THEN NULL ELSE COALESCE(broker_value, 0) - COALESCE(book_value, 0) END,
    CASE status
        WHEN 'DUP' THEN printf('%d lines for this security in the statement', lines)
        WHEN 'ONE_B' THEN CASE WHEN key_id LIKE 'UNMAPPED:%' THEN 'At the broker only; contract code not recognised'
                               ELSE 'At the broker only; the book has no position' END
        WHEN 'ONE_A' THEN printf('In the book only: %.4f shares', book_qty)
        WHEN 'PARSE' THEN 'Could not read: ' || parse_errors
        WHEN 'UNIT' THEN printf('Broker price R%.2f is about 100x our close of R%.2f', broker_price, our_price)
        WHEN 'SETTLE' THEN printf('Broker value implies %.4f shares against %.4f in the book; the gap is trades not yet settled (T+%d)',
                                  implied_qty, book_qty, :settle_days)
        WHEN 'QTY' THEN printf('Broker value implies %.4f shares; the book holds %.4f', implied_qty, book_qty)
        WHEN 'NOPRICE' THEN 'No independently checked close for the statement date'
        WHEN 'STALE' THEN printf('Broker price R%.2f is the previous close; the close on the day was R%.2f',
                                 broker_price, our_price)
        WHEN 'PRICE' THEN printf('Broker price R%.2f against our close of R%.2f', broker_price, our_price)
        WHEN 'MATCH' THEN CASE WHEN mapped_by = 'isin' AND contract_code <> 'EQU.ZA.' || key_id
                               THEN printf('Matched by ISIN; the broker still uses %s', contract_code) END
    END
FROM classified
