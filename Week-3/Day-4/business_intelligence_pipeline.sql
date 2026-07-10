/*******************************************************************************
  MUSIC STORE - ADVANCED SQL BUSINESS INTELLIGENCE PIPELINE
  ------------------------------------------------------------------------------
  This script is built as a PIPELINE. Each stage below is saved as a VIEW.
  A view is just a saved query that you can reuse later, exactly like a table.
  We use views (instead of one giant CTE) because Task 5 needs to look at the
  results of Task 1 to Task 4 several times, in several separate SELECT
  statements. A CTE only lives for one single query, so it cannot be reused
  across many queries. A view can.

*******************************************************************************/


/*******************************************************************************
  STAGE 1 (TASK 1) - CUSTOMER SPENDING PROFILE
  ------------------------------------------------------------------------------
  Goal: one row per customer, with everything we know about how they spend.
  We use CTEs inside the view to break the work into small, readable steps
  instead of one huge tangled query.
*******************************************************************************/
CREATE OR REPLACE VIEW customer_profile AS
WITH invoice_details AS (
    -- Step 1: join invoice_line down to genre and artist so we can count
    -- unique genres / unique artists per customer later.
    SELECT
        i.customer_id,
        i.invoice_id,
        i.invoice_date,
        il.track_id,
        il.unit_price,
        il.quantity,
        (il.unit_price * il.quantity) AS line_revenue,
        t.genre_id,
        al.artist_id
    FROM invoice i
    JOIN invoice_line il ON il.invoice_id = i.invoice_id
    JOIN track t         ON t.track_id = il.track_id
    LEFT JOIN album al   ON al.album_id = t.album_id
),
customer_aggregates AS (
    -- Step 2: one row per customer with every number we need.
    SELECT
        c.customer_id,
        c.first_name,
        c.last_name,
        c.country,
        ROUND(SUM(d.line_revenue), 2)              AS total_spent,
        COUNT(DISTINCT d.invoice_id)                AS total_invoices,
        SUM(d.quantity)                              AS total_tracks_purchased,
        COUNT(DISTINCT d.genre_id)                   AS unique_genres,
        COUNT(DISTINCT d.artist_id)                  AS unique_artists,
        COUNT(DISTINCT DATE_TRUNC('month', d.invoice_date)) AS purchase_months
    FROM customer c
    JOIN invoice_details d ON d.customer_id = c.customer_id
    GROUP BY c.customer_id, c.first_name, c.last_name, c.country
)
-- Step 3: add average invoice value, computed from numbers we already have,
-- so we never repeat the SUM/COUNT work.
SELECT
    customer_id,
    first_name,
    last_name,
    country,
    total_spent,
    total_invoices,
    total_tracks_purchased,
    unique_genres,
    unique_artists,
    purchase_months,
    ROUND(total_spent / NULLIF(total_invoices, 0), 2) AS avg_invoice_value
FROM customer_aggregates;


/*******************************************************************************
  STAGE 2 (TASK 2) - CUSTOMER SEGMENTATION
  ------------------------------------------------------------------------------
  Goal: label every customer as Platinum / Gold / Silver / Bronze.

  SEGMENTATION LOGIC (the criteria we chose and why):
  We do NOT segment on spending alone, because a customer who spends a lot on
  one single big order is not the same as a loyal customer who keeps coming
  back and explores many genres. So we combine THREE signals:
    1. total_spent       -> how much money they bring in
    2. total_invoices    -> how often they come back (loyalty / frequency)
    3. unique_genres      -> how broadly they explore the catalog (engagement)

  We use PERCENTILES instead of fixed numbers (like "spend >= 30"), because a
  fixed number only makes sense for one specific dataset size. On a large,
  real dataset, "spend >= 30" might put almost everyone in Platinum, or
  almost no one, depending on how the business actually spreads out. A
  percentile based rule automatically adjusts itself to whatever data you
  load, always giving you sensible, evenly sized tiers.

  How it works, step by step:
    1. PERCENT_RANK() compares every customer only against OTHER customers
       in the same table, and returns a value from 0 (lowest) to 1 (highest)
       for each of the three metrics separately.
    2. The three percentile ranks are blended into one composite_score,
       weighted by importance: spend 50%, loyalty (invoices) 30%,
       engagement (genres) 20%.
    3. NTILE(4) then splits all customers into four EQUAL SIZED groups
       based on that composite score: the top quarter becomes Platinum,
       the next quarter Gold, then Silver, then Bronze. This keeps the
       segments balanced no matter if you have 20 customers or 2 million.
*******************************************************************************/
CREATE OR REPLACE VIEW customer_segments AS
WITH percentiles AS (
    -- Step 1: rank every customer against every other customer on each
    -- metric individually. 0 = lowest in the table, 1 = highest.
    SELECT
        cp.*,
        PERCENT_RANK() OVER (ORDER BY total_spent)     AS pct_spent,
        PERCENT_RANK() OVER (ORDER BY total_invoices)  AS pct_invoices,
        PERCENT_RANK() OVER (ORDER BY unique_genres)   AS pct_genres
    FROM customer_profile cp
),
scored AS (
    -- Step 2: blend the three percentile ranks into one composite score.
    SELECT
        *,
        (pct_spent * 0.5 + pct_invoices * 0.3 + pct_genres * 0.2) AS composite_score
    FROM percentiles
)
-- Step 3: split customers into four equal sized groups by composite score.
SELECT
    customer_id, first_name, last_name, country,
    total_spent, total_invoices, total_tracks_purchased,
    unique_genres, unique_artists, purchase_months, avg_invoice_value,
    composite_score,
    CASE NTILE(4) OVER (ORDER BY composite_score DESC)
        WHEN 1 THEN 'Platinum'
        WHEN 2 THEN 'Gold'
        WHEN 3 THEN 'Silver'
        ELSE 'Bronze'
    END AS segment
FROM scored;


/*******************************************************************************
  STAGE 3 (TASK 3) - FAVORITE GENRE + MARKETING RECOMMENDATION
  ------------------------------------------------------------------------------
  Goal: find each customer's single favorite genre (the genre they spent the
  most money on), then attach a campaign based on their segment from Stage 2.

  We use ROW_NUMBER() here, not RANK(), because we want EXACTLY ONE favorite
  genre per customer even if two genres happen to tie. ROW_NUMBER() always
  hands out a single unique number (1, 2, 3...) with no ties, so row number 1
  gives us one clean "winner" per customer.
*******************************************************************************/
CREATE OR REPLACE VIEW favorite_genres AS
WITH genre_spend AS (
    -- Step 1: how much did each customer spend on each genre?
    SELECT
        i.customer_id,
        g.name AS genre_name,
        SUM(il.unit_price * il.quantity) AS genre_spent
    FROM invoice i
    JOIN invoice_line il ON il.invoice_id = i.invoice_id
    JOIN track t         ON t.track_id = il.track_id
    JOIN genre g          ON g.genre_id = t.genre_id
    GROUP BY i.customer_id, g.name
),
ranked_genres AS (
    -- Step 2: rank each customer's genres from most spent (1) to least.
    SELECT
        customer_id,
        genre_name,
        genre_spent,
        ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY genre_spent DESC) AS genre_rank
    FROM genre_spend
)
-- Step 3: keep only the number 1 genre per customer.
SELECT customer_id, genre_name AS favorite_genre, genre_spent AS favorite_genre_spend
FROM ranked_genres
WHERE genre_rank = 1;


CREATE OR REPLACE VIEW customer_marketing AS
SELECT
    cs.customer_id,
    cs.first_name,
    cs.last_name,
    cs.country,
    cs.total_spent,
    cs.segment,
    fg.favorite_genre,
    -- Campaign is decided purely by segment, matching the brief's examples.
    CASE cs.segment
        WHEN 'Platinum' THEN 'Early access to new releases'
        WHEN 'Gold'     THEN 'Album bundles'
        WHEN 'Silver'   THEN 'Genre discounts'
        ELSE 'First purchase coupon'
    END AS recommended_campaign
FROM customer_segments cs
LEFT JOIN favorite_genres fg ON fg.customer_id = cs.customer_id;


/*******************************************************************************
  STAGE 4 (TASK 4) - COUNTRY EXPANSION STRATEGY
  ------------------------------------------------------------------------------
  Goal: score every country on several business metrics, then rank them.

  SCORING METHOD (weights chosen and why):
  All raw metrics are converted into a 0-1 score using min-max normalization,
  ( value - min ) / ( max - min ), so that a metric measured in dollars
  (like revenue) does not automatically outweigh a metric measured in a
  small count (like number of genres). Only after normalizing do we apply
  weights, so the weights actually control the outcome, not the raw units.

  Weights used:
    Total revenue             35%  -> the country's overall money value
    Avg revenue per customer  25%  -> quality/value of each customer there
    Avg invoice value         15%  -> how big a typical single order is
    Genres purchased          15%  -> how broad the local taste/catalog fit is
    Customer count (diversity) 10% -> size of the existing customer base

  These weights favor overall revenue first (the main growth driver) but still
  reward markets with high-value, broad-taste customers, not just big markets.
*******************************************************************************/
CREATE OR REPLACE VIEW country_metrics AS
SELECT
    c.country,
    COUNT(DISTINCT c.customer_id)                          AS total_customers,
    ROUND(SUM(il.unit_price * il.quantity), 2)             AS total_revenue,
    ROUND(SUM(il.unit_price * il.quantity)
          / NULLIF(COUNT(DISTINCT c.customer_id), 0), 2)   AS avg_revenue_per_customer,
    ROUND(AVG(i.total), 2)                                  AS avg_invoice_value,
    COUNT(DISTINCT t.genre_id)                              AS genres_purchased
FROM customer c
JOIN invoice i        ON i.customer_id = c.customer_id
JOIN invoice_line il  ON il.invoice_id = i.invoice_id
JOIN track t           ON t.track_id = il.track_id
GROUP BY c.country;


CREATE OR REPLACE VIEW country_ranking AS
WITH bounds AS (
    -- Step 1: find the min and max of each metric, across all countries,
    -- needed to normalize every metric onto the same 0-1 scale.
    SELECT
        MIN(total_revenue) AS min_rev,  MAX(total_revenue) AS max_rev,
        MIN(avg_revenue_per_customer) AS min_arpc, MAX(avg_revenue_per_customer) AS max_arpc,
        MIN(avg_invoice_value) AS min_aiv, MAX(avg_invoice_value) AS max_aiv,
        MIN(genres_purchased) AS min_gen, MAX(genres_purchased) AS max_gen,
        MIN(total_customers) AS min_cust, MAX(total_customers) AS max_cust
    FROM country_metrics
),
normalized AS (
    -- Step 2: turn each raw metric into a 0-1 score.
    -- NULLIF avoids a divide-by-zero if every country tied on a metric.
    SELECT
        cm.country,
        cm.total_revenue,
        cm.avg_revenue_per_customer,
        cm.avg_invoice_value,
        cm.genres_purchased,
        cm.total_customers,
        (cm.total_revenue - b.min_rev)::NUMERIC / NULLIF(b.max_rev - b.min_rev, 0) AS score_revenue,
        (cm.avg_revenue_per_customer - b.min_arpc)::NUMERIC / NULLIF(b.max_arpc - b.min_arpc, 0) AS score_arpc,
        (cm.avg_invoice_value - b.min_aiv)::NUMERIC / NULLIF(b.max_aiv - b.min_aiv, 0) AS score_aiv,
        (cm.genres_purchased - b.min_gen)::NUMERIC / NULLIF(b.max_gen - b.min_gen, 0) AS score_genres,
        (cm.total_customers - b.min_cust)::NUMERIC / NULLIF(b.max_cust - b.min_cust, 0) AS score_customers
    FROM country_metrics cm
    CROSS JOIN bounds b
),
scored AS (
    -- Step 3: apply the weights explained above to get one final score.
    SELECT
        *,
        ROUND(
            COALESCE(score_revenue, 0)   * 0.35 +
            COALESCE(score_arpc, 0)      * 0.25 +
            COALESCE(score_aiv, 0)       * 0.15 +
            COALESCE(score_genres, 0)    * 0.15 +
            COALESCE(score_customers, 0) * 0.10
        , 4) AS performance_score
    FROM normalized
)
-- Step 4: rank countries from best (1) to worst score.
-- RANK() is used (not ROW_NUMBER()) because if two countries genuinely tie
-- on score, they deserve the SAME rank, and the next rank should skip ahead
-- to show that a tie happened.
SELECT
    country,
    total_customers,
    total_revenue,
    avg_revenue_per_customer,
    avg_invoice_value,
    genres_purchased,
    performance_score,
    RANK() OVER (ORDER BY performance_score DESC) AS country_rank
FROM scored;


/*******************************************************************************
  SUPPORTING STAGES for the executive report: revenue by artist, album,
  and employee. These are simple, so they stay as their own small views
  rather than being folded into the bigger ones above.
*******************************************************************************/
CREATE OR REPLACE VIEW artist_revenue AS
SELECT
    ar.artist_id,
    ar.name AS artist_name,
    ROUND(SUM(il.unit_price * il.quantity), 2) AS total_revenue
FROM artist ar
JOIN album al        ON al.artist_id = ar.artist_id
JOIN track t          ON t.album_id = al.album_id
JOIN invoice_line il  ON il.track_id = t.track_id
GROUP BY ar.artist_id, ar.name;

CREATE OR REPLACE VIEW album_revenue AS
SELECT
    al.album_id,
    al.title AS album_title,
    ROUND(SUM(il.unit_price * il.quantity), 2) AS total_revenue
FROM album al
JOIN track t          ON t.album_id = al.album_id
JOIN invoice_line il  ON il.track_id = t.track_id
GROUP BY al.album_id, al.title;

CREATE OR REPLACE VIEW employee_revenue AS
SELECT
    e.employee_id,
    e.first_name || ' ' || e.last_name AS employee_name,
    ROUND(SUM(i.total), 2) AS total_revenue
FROM employee e
JOIN customer c  ON c.support_rep_id = e.employee_id
JOIN invoice i   ON i.customer_id = c.customer_id
GROUP BY e.employee_id, employee_name;


/*******************************************************************************
  STAGE 5 (TASK 5) - EXECUTIVE SQL REPORT
  ------------------------------------------------------------------------------
  Every query below simply SELECTs from the views we already built above.
  Nothing is recalculated from raw tables again. Run each block separately
  and screenshot the result, this is what the deliverable asks for.
*******************************************************************************/

-- 5.1 Customer Segment Summary: how many customers in each segment.
SELECT
    segment,
    COUNT(*) AS number_of_customers
FROM customer_segments
GROUP BY segment
ORDER BY number_of_customers DESC;

-- 5.2 Revenue by Segment
SELECT
    segment,
    ROUND(SUM(total_spent), 2) AS segment_revenue
FROM customer_segments
GROUP BY segment
ORDER BY segment_revenue DESC;

-- 5.3 Top Customer in each Segment (highest spender per segment)
SELECT segment, first_name, last_name, total_spent
FROM (
    SELECT
        segment, first_name, last_name, total_spent,
        ROW_NUMBER() OVER (PARTITION BY segment ORDER BY total_spent DESC) AS rn
    FROM customer_segments
) ranked
WHERE rn = 1
ORDER BY total_spent DESC;

-- 5.4 Top Genre in each Segment (the genre segment members spend the most on, combined)
SELECT segment, favorite_genre, segment_genre_spend
FROM (
    SELECT
        cm.segment,
        cm.favorite_genre,
        SUM(cm.total_spent) AS segment_genre_spend,
        ROW_NUMBER() OVER (PARTITION BY cm.segment ORDER BY SUM(cm.total_spent) DESC) AS rn
    FROM customer_marketing cm
    WHERE cm.favorite_genre IS NOT NULL
    GROUP BY cm.segment, cm.favorite_genre
) ranked
WHERE rn = 1;

-- 5.5 Best Performing Country (rank 1 from Task 4)
SELECT country, total_revenue, performance_score, country_rank
FROM country_ranking
WHERE country_rank = 1;

-- 5.6 Revenue Contribution by Country (% of total company revenue)
SELECT
    country,
    total_revenue,
    ROUND(100 * total_revenue / SUM(total_revenue) OVER (), 2) AS pct_of_total_revenue
FROM country_ranking
ORDER BY total_revenue DESC;

-- 5.7 Top Employee by Revenue
SELECT employee_name, total_revenue
FROM employee_revenue
ORDER BY total_revenue DESC
LIMIT 1;

-- 5.8 Top Artist by Revenue
SELECT artist_name, total_revenue
FROM artist_revenue
ORDER BY total_revenue DESC
LIMIT 1;

-- 5.9 Top Album by Revenue
SELECT album_title, total_revenue
FROM album_revenue
ORDER BY total_revenue DESC
LIMIT 1;

-- Customer segmentation results: every customer with their segment label.
SELECT * FROM customer_segments ORDER BY composite_score DESC LIMIT 20;

-- Country ranking results: every country side by side with its score and rank.
SELECT * FROM country_ranking ORDER BY country_rank;

