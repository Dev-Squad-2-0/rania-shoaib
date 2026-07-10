/* ============================================================================
   ENTERPRISE ANALYTICS PIPELINE
   Database: customerstore_db (AdventureWorks OLTP)
   Author:   [Your Name]
   Purpose:  Reusable analytics layer sitting on top of raw operational
             tables. Nothing downstream of Stage 1 queries raw schemas
             (sales, production, humanresources, purchasing, person) again.

   PIPELINE FLOW:
     Raw Tables
        -> STAGE 1: Base Analytics Views   (this file, Part 1)
        -> STAGE 2: Business Metrics       (Part 2)
        -> STAGE 3: Segmentation / Regional Analysis (Part 3)
        -> STAGE 4: Executive KPI Tables   (Part 4)
        -> Python / Jupyter reads ONLY from Stage 3 & 4 outputs
   ============================================================================ */


/* ============================================================================
   STAGE 1 — BASE ANALYTICS LAYER
   10 reusable views across 7 business domains: Customer, Product, Sales,
   Employee, Territory, Inventory, Vendor/Purchasing.
   Each view queries raw tables ONE time; every later stage builds on these.
   ============================================================================ */

CREATE SCHEMA IF NOT EXISTS analytics;


-- ----------------------------------------------------------------------------
-- 1. CUSTOMER ANALYTICS
-- One row per customer. Combines customer, person, territory, and a rollup
-- of their own order history (so downstream views never re-touch salesorderheader
-- just to know "how many orders has this customer placed").
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS analytics.customer_analytics;
CREATE OR REPLACE VIEW analytics.customer_analytics AS
SELECT
    c.customerid,
    c.territoryid,
    st.name                                   AS territory_name,
    st."group"                                AS territory_group,
    p.firstname,
    p.lastname,
    CASE
        WHEN p.businessentityid IS NOT NULL THEN p.firstname || ' ' || p.lastname
        WHEN s.businessentityid IS NOT NULL THEN s.name
        ELSE 'Unknown'
    END                                        AS customer_name,
    CASE
        WHEN p.businessentityid IS NOT NULL THEN 'Individual'
        WHEN s.businessentityid IS NOT NULL THEN 'Store'
        ELSE 'Unknown'
    END                                        AS customer_type,
    COUNT(soh.salesorderid)                   AS total_orders,
    COALESCE(SUM(soh.totaldue), 0)            AS lifetime_revenue,
    COALESCE(AVG(soh.totaldue), 0)            AS avg_order_value,
    MIN(soh.orderdate)                        AS first_order_date,
    MAX(soh.orderdate)                        AS last_order_date,
    CASE
        WHEN MAX(soh.orderdate) IS NULL THEN 'Never Purchased'
        WHEN MAX(soh.orderdate) < NOW() - INTERVAL '12 months' THEN 'Inactive'
        ELSE 'Active'
    END                                        AS customer_status
FROM sales.customer c
LEFT JOIN person.person p          ON p.businessentityid = c.personid
LEFT JOIN sales.store s             ON s.businessentityid = c.storeid
LEFT JOIN sales.salesterritory st  ON st.territoryid = c.territoryid
LEFT JOIN sales.salesorderheader soh ON soh.customerid = c.customerid
GROUP BY c.customerid, c.territoryid, st.name, st."group",
         p.businessentityid, p.firstname, p.lastname, s.businessentityid, s.name;


-- ----------------------------------------------------------------------------
-- 2. PRODUCT ANALYTICS
-- One row per product. Combines category/subcategory hierarchy, current
-- pricing, and margin — the cost/price math is calculated once here so no
-- later view has to repeat "listprice - standardcost" ever again.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.product_analytics AS
SELECT
    pr.productid,
    pr.name                                    AS product_name,
    pr.productnumber,
    pr.color,
    pr.size,
    pr.standardcost,
    pr.listprice,
    (pr.listprice - pr.standardcost)           AS unit_margin,
    CASE WHEN pr.listprice > 0
         THEN ROUND(((pr.listprice - pr.standardcost) / pr.listprice) * 100, 2)
         ELSE 0
    END                                         AS margin_pct,
    psc.productsubcategoryid,
    psc.name                                    AS subcategory_name,
    pc.productcategoryid,
    pc.name                                     AS category_name,
    pr.sellstartdate,
    pr.sellenddate,
    pr.discontinueddate,
    CASE WHEN pr.discontinueddate IS NOT NULL THEN 'Discontinued'
         WHEN pr.sellenddate IS NOT NULL AND pr.sellenddate < NOW() THEN 'Not for Sale'
         ELSE 'Active'
    END                                          AS product_status
FROM production.product pr
LEFT JOIN production.productsubcategory psc ON psc.productsubcategoryid = pr.productsubcategoryid
LEFT JOIN production.productcategory pc     ON pc.productcategoryid = psc.productcategoryid;


-- ----------------------------------------------------------------------------
-- 3. SALES LINE ANALYTICS  (grain: one row per order line)
-- This is the most important view in the whole pipeline — nearly every
-- Stage 2/3 metric (revenue, growth, best sellers, territory performance)
-- aggregates from THIS view instead of re-joining header+detail repeatedly.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.sales_line_analytics AS
SELECT
    sod.salesorderdetailid,
    soh.salesorderid,
    soh.orderdate,
    DATE_TRUNC('month', soh.orderdate)::date   AS order_month,
    DATE_TRUNC('quarter', soh.orderdate)::date AS order_quarter,
    EXTRACT(YEAR FROM soh.orderdate)::int      AS order_year,
    soh.customerid,
    soh.salespersonid,
    soh.territoryid,
    sod.productid,
    sod.orderqty,
    sod.unitprice,
    sod.unitpricediscount,
    -- linetotal was dropped from sales.salesorderdetail during CSV import
    -- (it was a SQL Server computed column) -- recalculated here per the
    -- original formula: UnitPrice * (1 - UnitPriceDiscount) * OrderQty
    ROUND(sod.unitprice * (1 - sod.unitpricediscount) * sod.orderqty, 2) AS line_revenue,
    (sod.orderqty * pa.standardcost)           AS line_cost,
    (ROUND(sod.unitprice * (1 - sod.unitpricediscount) * sod.orderqty, 2)
        - (sod.orderqty * pa.standardcost))    AS line_profit,
    pa.category_name,
    pa.subcategory_name,
    pa.product_name
FROM sales.salesorderdetail sod
JOIN sales.salesorderheader soh   ON soh.salesorderid = sod.salesorderid
JOIN analytics.product_analytics pa ON pa.productid = sod.productid
WHERE soh.status <> 6; 


-- ----------------------------------------------------------------------------
-- 4. EMPLOYEE ANALYTICS
-- One row per employee, with current department (from department history).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.employee_analytics AS
SELECT
    e.businessentityid,
    p.firstname || ' ' || p.lastname          AS full_name,
    e.jobtitle,
    e.hiredate,
    e.gender,
    e.salariedflag,
    d.name                                     AS department_name,
    d.groupname                                AS department_group,
    ROUND(EXTRACT(EPOCH FROM (NOW() - e.hiredate)) / (86400 * 365.25), 1) AS years_of_service
FROM humanresources.employee e
JOIN person.person p ON p.businessentityid = e.businessentityid
LEFT JOIN humanresources.employeedepartmenthistory edh
       ON edh.businessentityid = e.businessentityid AND edh.enddate IS NULL
LEFT JOIN humanresources.department d ON d.departmentid = edh.departmentid;


-- ----------------------------------------------------------------------------
-- 5. SALESPERSON ANALYTICS
-- One row per salesperson. Reuses sales_line_analytics instead of
-- re-aggregating salesorderheader/detail again.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.salesperson_analytics AS
SELECT
    sp.businessentityid,
    ea.full_name,
    sp.territoryid,
    st.name                                    AS territory_name,
    sp.salesquota,
    sp.bonus,
    sp.commissionpct,
    sp.salesytd,
    sp.saleslastyear,
    COUNT(DISTINCT sla.salesorderid)           AS total_orders_handled,
    COALESCE(SUM(sla.line_revenue), 0)         AS total_revenue_generated,
    COALESCE(SUM(sla.line_profit), 0)          AS total_profit_generated
FROM sales.salesperson sp
JOIN analytics.employee_analytics ea ON ea.businessentityid = sp.businessentityid
LEFT JOIN sales.salesterritory st    ON st.territoryid = sp.territoryid
LEFT JOIN analytics.sales_line_analytics sla ON sla.salespersonid = sp.businessentityid
GROUP BY sp.businessentityid, ea.full_name, sp.territoryid, st.name,
         sp.salesquota, sp.bonus, sp.commissionpct, sp.salesytd, sp.saleslastyear;


-- ----------------------------------------------------------------------------
-- 6. TERRITORY ANALYTICS
-- One row per sales territory, reusing sales_line_analytics for revenue.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.territory_analytics AS
SELECT
    st.territoryid,
    st.name                                    AS territory_name,
    st."group"                                 AS territory_group,
    st.countryregioncode,
    st.salesytd,
    st.saleslastyear,
    COUNT(DISTINCT sla.customerid)             AS distinct_customers,
    COUNT(DISTINCT sla.salesorderid)           AS total_orders,
    COALESCE(SUM(sla.line_revenue), 0)         AS total_revenue,
    COALESCE(SUM(sla.line_profit), 0)          AS total_profit
FROM sales.salesterritory st
LEFT JOIN analytics.sales_line_analytics sla ON sla.territoryid = st.territoryid
GROUP BY st.territoryid, st.name, st."group", st.countryregioncode,
         st.salesytd, st.saleslastyear;


-- ----------------------------------------------------------------------------
-- 7. INVENTORY ANALYTICS
-- One row per product-location inventory record, joined to product analytics
-- so reorder-point breaches can be flagged without re-touching production.product.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.inventory_analytics AS
SELECT
    pi.productid,
    pa.product_name,
    pa.category_name,
    pi.locationid,
    loc.name                                    AS location_name,
    pi.quantity                                  AS quantity_on_hand,
    pr.safetystocklevel,
    pr.reorderpoint,
    CASE WHEN pi.quantity <= pr.reorderpoint THEN 'Reorder Needed'
         WHEN pi.quantity <= pr.safetystocklevel THEN 'Low Stock'
         ELSE 'Healthy'
    END                                           AS stock_status
FROM production.productinventory pi
JOIN analytics.product_analytics pa ON pa.productid = pi.productid
JOIN production.product pr          ON pr.productid = pi.productid
LEFT JOIN production.location loc   ON loc.locationid = pi.locationid;


-- ----------------------------------------------------------------------------
-- 8. VENDOR ANALYTICS
-- One row per vendor.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.vendor_analytics AS
SELECT
    v.businessentityid,
    v.name                                       AS vendor_name,
    v.creditrating,
    v.preferredvendorstatus,
    v.activeflag,
    COUNT(DISTINCT pv.productid)                 AS products_supplied,
    ROUND(AVG(pv.averageleadtime), 1)             AS avg_lead_time_days
FROM purchasing.vendor v
LEFT JOIN purchasing.productvendor pv ON pv.businessentityid = v.businessentityid
GROUP BY v.businessentityid, v.name, v.creditrating, v.preferredvendorstatus, v.activeflag;


-- ----------------------------------------------------------------------------
-- 9. PURCHASE LINE ANALYTICS  (grain: one row per PO line)
-- Foundation for supplier performance / purchasing trend metrics later.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.purchase_line_analytics AS
SELECT
    pod.purchaseorderdetailid,
    poh.purchaseorderid,
    poh.orderdate,
    DATE_TRUNC('month', poh.orderdate)::date    AS order_month,
    poh.vendorid,
    va.vendor_name,
    pod.productid,
    pa.product_name,
    pod.orderqty,
    pod.unitprice,
    (pod.orderqty * pod.unitprice)              AS line_cost,
    pod.receivedqty,
    pod.rejectedqty,
    CASE WHEN pod.receivedqty > 0
         THEN ROUND((pod.rejectedqty / pod.receivedqty) * 100, 2)
         ELSE 0
    END                                          AS reject_rate_pct,
    poh.status                                   AS po_status
FROM purchasing.purchaseorderdetail pod
JOIN purchasing.purchaseorderheader poh ON poh.purchaseorderid = pod.purchaseorderid
JOIN analytics.vendor_analytics va      ON va.businessentityid = poh.vendorid
JOIN analytics.product_analytics pa     ON pa.productid = pod.productid;


-- ----------------------------------------------------------------------------
-- 10. DATE DIMENSION
-- Generated calendar table spanning the full range of sales activity.
-- Used later for consistent month/quarter grouping and gap-filling.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.date_dim;
CREATE TABLE analytics.date_dim AS
SELECT
    d::date                                      AS date_key,
    EXTRACT(YEAR FROM d)::int                     AS year,
    EXTRACT(QUARTER FROM d)::int                  AS quarter,
    EXTRACT(MONTH FROM d)::int                     AS month,
    TO_CHAR(d, 'Month')                           AS month_name,
    DATE_TRUNC('month', d)::date                  AS month_start,
    DATE_TRUNC('quarter', d)::date                 AS quarter_start
FROM GENERATE_SERIES(
    (SELECT MIN(orderdate) FROM sales.salesorderheader)::date,
    (SELECT MAX(orderdate) FROM sales.salesorderheader)::date,
    INTERVAL '1 day'
) AS d;

ALTER TABLE analytics.date_dim ADD PRIMARY KEY (date_key);


-- ============================================================================
-- END OF STAGE 1
-- Verify before continuing to Stage 2:
--   SELECT * FROM analytics.customer_analytics LIMIT 5;
--   SELECT * FROM analytics.sales_line_analytics LIMIT 5;
--   \dv analytics.*
-- ============================================================================


/* ============================================================================
   STAGE 2 — BUSINESS METRICS
   Everything here builds ONLY on Stage 1 views (never on raw tables again).
   Introduces window functions, ranking, CASE WHEN, conditional aggregation,
   and chained CTEs as required by Task 4.
   ============================================================================ */

-- ----------------------------------------------------------------------------
-- 11. MONTHLY REVENUE (+ month-over-month growth)
-- Built on sales_line_analytics. Uses LAG() window function to compute growth
-- without a self-join.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.monthly_revenue AS
WITH monthly AS (
    SELECT
        order_month,
        SUM(line_revenue)  AS revenue,
        SUM(line_profit)   AS profit,
        COUNT(DISTINCT salesorderid) AS order_count
    FROM analytics.sales_line_analytics
    GROUP BY order_month
)
SELECT
    order_month,
    revenue,
    profit,
    order_count,
    LAG(revenue) OVER (ORDER BY order_month)               AS prev_month_revenue,
    ROUND(
        CASE WHEN LAG(revenue) OVER (ORDER BY order_month) > 0
             THEN ((revenue - LAG(revenue) OVER (ORDER BY order_month))
                   / LAG(revenue) OVER (ORDER BY order_month)) * 100
             ELSE NULL
        END, 2)                                             AS mom_growth_pct
FROM monthly
ORDER BY order_month;


-- ----------------------------------------------------------------------------
-- 12. QUARTERLY REVENUE (+ quarter-over-quarter growth)
-- Same pattern as monthly_revenue, one grain up.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.quarterly_revenue AS
WITH quarterly AS (
    SELECT
        order_quarter,
        EXTRACT(YEAR FROM order_quarter)::int    AS year,
        EXTRACT(QUARTER FROM order_quarter)::int AS quarter,
        SUM(line_revenue) AS revenue,
        SUM(line_profit)  AS profit
    FROM analytics.sales_line_analytics
    GROUP BY order_quarter
)
SELECT
    order_quarter,
    year,
    quarter,
    revenue,
    profit,
    LAG(revenue) OVER (ORDER BY order_quarter)              AS prev_quarter_revenue,
    ROUND(
        CASE WHEN LAG(revenue) OVER (ORDER BY order_quarter) > 0
             THEN ((revenue - LAG(revenue) OVER (ORDER BY order_quarter))
                   / LAG(revenue) OVER (ORDER BY order_quarter)) * 100
             ELSE NULL
        END, 2)                                              AS qoq_growth_pct
FROM quarterly
ORDER BY order_quarter;


-- ----------------------------------------------------------------------------
-- 13. PRODUCT PERFORMANCE (+ ranking)
-- Built on sales_line_analytics + product_analytics. Ranks products by
-- revenue using RANK() so best/worst sellers can be sliced with a simple
-- WHERE clause in Stage 4 instead of recomputing.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.product_performance AS
WITH product_totals AS (
    SELECT
        productid,
        product_name,
        category_name,
        subcategory_name,
        SUM(orderqty)                       AS units_sold,
        SUM(line_revenue)                   AS total_revenue,
        SUM(line_profit)                    AS total_profit,
        COUNT(DISTINCT salesorderid)        AS orders_containing_product
    FROM analytics.sales_line_analytics
    GROUP BY productid, product_name, category_name, subcategory_name
)
SELECT
    *,
    RANK() OVER (ORDER BY total_revenue DESC)  AS revenue_rank,
    RANK() OVER (ORDER BY total_profit DESC)   AS profit_rank,
    CASE
        WHEN total_revenue > 0
        THEN ROUND((total_profit / total_revenue) * 100, 2)
        ELSE 0
    END                                          AS profit_margin_pct
FROM product_totals;


-- ----------------------------------------------------------------------------
-- 14. CATEGORY PERFORMANCE
-- Rolls product_performance up to category level using conditional
-- aggregation to also show what share of category revenue each subcategory
-- contributes, without a second pass over sales_line_analytics.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.category_performance AS
SELECT
    category_name,
    COUNT(DISTINCT productid)                          AS product_count,
    SUM(units_sold)                                     AS total_units_sold,
    SUM(total_revenue)                                  AS total_revenue,
    SUM(total_profit)                                   AS total_profit,
    SUM(CASE WHEN profit_margin_pct >= 40 THEN total_revenue ELSE 0 END) AS high_margin_revenue,
    SUM(CASE WHEN profit_margin_pct < 40  THEN total_revenue ELSE 0 END) AS low_margin_revenue
FROM analytics.product_performance
GROUP BY category_name;


-- ----------------------------------------------------------------------------
-- 15. CUSTOMER RFM (Recency, Frequency, Monetary)
-- Base metrics that Stage 3's customer_segments view will bucket into
-- segments. Kept separate so the raw RFM numbers stay reusable/inspectable
-- on their own.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.customer_rfm AS
SELECT
    customerid,
    customer_name,
    customer_type,
    total_orders                                          AS frequency,
    lifetime_revenue                                       AS monetary,
    last_order_date,
    CASE
        WHEN last_order_date IS NULL THEN NULL
        ELSE EXTRACT(DAY FROM (NOW() - last_order_date))::int
    END                                                     AS recency_days
FROM analytics.customer_analytics
WHERE total_orders > 0;


-- ----------------------------------------------------------------------------
-- 16. SALESPERSON RANKING (+ quota attainment)
-- Builds on salesperson_analytics. CASE WHEN classifies quota attainment;
-- RANK() orders performance without a subquery per salesperson.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.salesperson_ranking AS
SELECT
    businessentityid,
    full_name,
    territory_name,
    salesquota,
    salesytd,
    total_revenue_generated,
    total_profit_generated,
    total_orders_handled,
    RANK() OVER (ORDER BY total_revenue_generated DESC)   AS revenue_rank,
    CASE
        WHEN salesquota IS NULL OR salesquota = 0 THEN 'No Quota Set'
        WHEN salesytd >= salesquota THEN 'Quota Met'
        WHEN salesytd >= salesquota * 0.75 THEN 'Near Quota'
        ELSE 'Below Quota'
    END                                                     AS quota_status
FROM analytics.salesperson_analytics;


-- ============================================================================
-- END OF STAGE 2
-- Verify before continuing to Stage 3:
--   SELECT * FROM analytics.monthly_revenue;
--   SELECT * FROM analytics.product_performance ORDER BY revenue_rank LIMIT 10;
--   SELECT * FROM analytics.salesperson_ranking;
-- ============================================================================


/* ============================================================================
   STAGE 3 — CUSTOMER SEGMENTATION & REGIONAL ANALYSIS
   Builds only on Stage 1/2 views. Adds NTILE(), self-referencing CTEs,
   and more ranking/window function use to round out Task 4's requirements.
   ============================================================================ */

-- ----------------------------------------------------------------------------
-- 17. CUSTOMER SEGMENTS
-- Buckets customers into RFM-based tiers. NTILE(4) splits customers into
-- quartiles on recency and monetary value; CASE WHEN turns the quartile
-- combination into a human-readable segment label.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.customer_segments AS
WITH scored AS (
    SELECT
        customerid,
        customer_name,
        customer_type,
        frequency,
        monetary,
        recency_days,
        NTILE(4) OVER (ORDER BY recency_days ASC)   AS recency_score,   -- 1 = most recent
        NTILE(4) OVER (ORDER BY monetary DESC)       AS monetary_score  -- 1 = highest spend
    FROM analytics.customer_rfm
)
SELECT
    customerid,
    customer_name,
    customer_type,
    frequency,
    monetary,
    recency_days,
    recency_score,
    monetary_score,
    CASE
        WHEN recency_score = 1 AND monetary_score = 1 THEN 'Champions'
        WHEN recency_score <= 2 AND monetary_score <= 2 THEN 'Loyal Customers'
        WHEN recency_score = 1 AND monetary_score >= 3 THEN 'New / Promising'
        WHEN recency_score >= 3 AND monetary_score <= 2 THEN 'At Risk'
        WHEN recency_score = 4 AND monetary_score = 4 THEN 'Lost'
        ELSE 'Needs Attention'
    END                                               AS segment
FROM scored;


-- ----------------------------------------------------------------------------
-- 18. CUSTOMER LIFETIME VALUE
-- Chained CTEs: first compute tenure, then derive an annualized value from it.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.customer_ltv AS
WITH tenure AS (
    SELECT
        customerid,
        customer_name,
        first_order_date,
        last_order_date,
        lifetime_revenue,
        total_orders,
        GREATEST(
            EXTRACT(DAY FROM (last_order_date - first_order_date)) / 365.25,
            0.01  -- avoid divide-by-zero for single-order customers
        )                                              AS tenure_years
    FROM analytics.customer_analytics
    WHERE total_orders > 0
)
SELECT
    customerid,
    customer_name,
    first_order_date,
    last_order_date,
    total_orders,
    lifetime_revenue,
    ROUND(tenure_years, 2)                             AS tenure_years,
    ROUND(lifetime_revenue / tenure_years, 2)           AS annualized_value
FROM tenure;


-- ----------------------------------------------------------------------------
-- 19. REPEAT CUSTOMERS
-- Single-row summary: one-time vs repeat buyers and the repeat rate.
-- Conditional aggregation (CASE WHEN inside SUM/COUNT) avoids two separate
-- queries.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.repeat_customers AS
SELECT
    COUNT(*) FILTER (WHERE total_orders = 1)           AS one_time_customers,
    COUNT(*) FILTER (WHERE total_orders > 1)            AS repeat_customers,
    COUNT(*) FILTER (WHERE total_orders > 0)             AS total_purchasing_customers,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE total_orders > 1)
        / NULLIF(COUNT(*) FILTER (WHERE total_orders > 0), 0), 2
    )                                                     AS repeat_rate_pct
FROM analytics.customer_analytics;


-- ----------------------------------------------------------------------------
-- 20. CUSTOMER RETENTION (year over year)
-- For each year, what % of that year's customers also bought the previous
-- year. Self-referencing CTE using EXISTS against sales_line_analytics.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.customer_retention AS
WITH yearly_customers AS (
    SELECT DISTINCT order_year, customerid
    FROM analytics.sales_line_analytics
)
SELECT
    yc.order_year,
    COUNT(DISTINCT yc.customerid)                        AS total_customers,
    COUNT(DISTINCT yc.customerid) FILTER (
        WHERE EXISTS (
            SELECT 1 FROM yearly_customers prev
            WHERE prev.customerid = yc.customerid
              AND prev.order_year = yc.order_year - 1
        )
    )                                                      AS retained_from_prior_year,
    ROUND(
        100.0 * COUNT(DISTINCT yc.customerid) FILTER (
            WHERE EXISTS (
                SELECT 1 FROM yearly_customers prev
                WHERE prev.customerid = yc.customerid
                  AND prev.order_year = yc.order_year - 1
            )
        ) / NULLIF(COUNT(DISTINCT yc.customerid), 0), 2
    )                                                      AS retention_rate_pct
FROM yearly_customers yc
GROUP BY yc.order_year
ORDER BY yc.order_year;


-- ----------------------------------------------------------------------------
-- 21. REGIONAL PERFORMANCE
-- Territory revenue with year-over-year growth and rank. Builds on
-- territory_analytics + sales_line_analytics.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.regional_performance AS
WITH yearly_territory AS (
    SELECT
        territoryid,
        order_year,
        SUM(line_revenue) AS revenue,
        SUM(line_profit)  AS profit
    FROM analytics.sales_line_analytics
    GROUP BY territoryid, order_year
),
with_growth AS (
    SELECT
        yt.*,
        st.territory_name,
        st.territory_group,
        LAG(revenue) OVER (PARTITION BY yt.territoryid ORDER BY order_year) AS prev_year_revenue
    FROM yearly_territory yt
    JOIN analytics.territory_analytics st ON st.territoryid = yt.territoryid
)
SELECT
    territoryid,
    territory_name,
    territory_group,
    order_year,
    revenue,
    profit,
    prev_year_revenue,
    ROUND(
        CASE WHEN prev_year_revenue > 0
             THEN ((revenue - prev_year_revenue) / prev_year_revenue) * 100
             ELSE NULL
        END, 2)                                            AS yoy_growth_pct,
    RANK() OVER (PARTITION BY order_year ORDER BY revenue DESC) AS territory_rank_that_year
FROM with_growth
ORDER BY order_year, territory_rank_that_year;



/* ============================================================================
   STAGE 4 — EXECUTIVE KPI TABLES
   Builds ONLY on Stage 1/2/3 views (never touches raw schemas again).
   These are materialized TABLES, not views — dashboard-ready snapshots so
   the Python/Jupyter notebook queries pre-computed numbers instead of
   re-running expensive joins/window functions on every load.

   Run this AFTER Stage 1, 2, and 3 have been executed successfully.

   TABLES:
     kpi_top_bottom_products   - Best Selling + Lowest Performing Products
     kpi_employee_performance  - Revenue Contribution % + vs Team Average
     kpi_territory_rankings    - Top/Lowest Performing Territories (latest year)
     kpi_inventory_health      - Inventory Health + Low Stock products
     kpi_supplier_performance  - Lead time, reject rate, spend
     kpi_purchasing_trends     - Monthly purchasing spend trend
     kpi_executive_summary     - One-row top-line summary (built LAST —
                                 depends on the other KPI tables)
   ============================================================================ */


-- ----------------------------------------------------------------------------
-- 22. TOP / BOTTOM PRODUCTS
-- Pulls straight from product_performance's existing revenue_rank — no
-- recomputation. Top 10 by revenue and bottom 10 by revenue, tagged so the
-- dashboard can filter on rank_type instead of two separate queries.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.kpi_top_bottom_products;
CREATE TABLE analytics.kpi_top_bottom_products AS
WITH bounds AS (
    SELECT MAX(revenue_rank) AS max_rank FROM analytics.product_performance
),
top10 AS (
    SELECT pp.*, 'Top 10' AS rank_type
    FROM analytics.product_performance pp
    WHERE revenue_rank <= 10
),
bottom10 AS (
    SELECT pp.*, 'Bottom 10' AS rank_type
    FROM analytics.product_performance pp, bounds b
    WHERE pp.revenue_rank > b.max_rank - 10
)
SELECT * FROM top10
UNION ALL
SELECT * FROM bottom10;


-- ----------------------------------------------------------------------------
-- 23. EMPLOYEE PERFORMANCE
-- Builds on salesperson_ranking + salesperson_analytics. Adds revenue
-- contribution as % of total company revenue, and a direct comparison
-- against the average salesperson (not just a rank number).
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.kpi_employee_performance;
CREATE TABLE analytics.kpi_employee_performance AS
WITH totals AS (
    SELECT
        SUM(total_revenue_generated)  AS company_total_revenue,
        AVG(total_revenue_generated)  AS avg_revenue_per_salesperson
    FROM analytics.salesperson_analytics
)
SELECT
    sr.businessentityid,
    sr.full_name,
    sr.territory_name,
    sr.total_revenue_generated,
    sr.total_profit_generated,
    sr.total_orders_handled,
    sr.revenue_rank,
    sr.quota_status,
    ROUND(100.0 * sr.total_revenue_generated
          / NULLIF(t.company_total_revenue, 0), 2)          AS revenue_contribution_pct,
    ROUND(sr.total_revenue_generated - t.avg_revenue_per_salesperson, 2) AS vs_team_avg_diff,
    CASE
        WHEN sr.total_revenue_generated > t.avg_revenue_per_salesperson THEN 'Above Average'
        WHEN sr.total_revenue_generated = t.avg_revenue_per_salesperson THEN 'At Average'
        ELSE 'Below Average'
    END                                                       AS performance_vs_team
FROM analytics.salesperson_ranking sr
CROSS JOIN totals t;

ALTER TABLE analytics.kpi_employee_performance ADD PRIMARY KEY (businessentityid);


-- ----------------------------------------------------------------------------
-- 24. TERRITORY RANKINGS (latest year only)
-- Filters regional_performance down to the most recent order_year and adds
-- a human-readable performance_tier so top/bottom territories are a simple
-- WHERE clause for the dashboard.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.kpi_territory_rankings;
CREATE TABLE analytics.kpi_territory_rankings AS
WITH latest_year AS (
    SELECT MAX(order_year) AS yr FROM analytics.regional_performance
),
territory_count AS (
    SELECT order_year, COUNT(*) AS n
    FROM analytics.regional_performance
    GROUP BY order_year
)
SELECT
    rp.territoryid,
    rp.territory_name,
    rp.territory_group,
    rp.order_year,
    rp.revenue,
    rp.profit,
    rp.prev_year_revenue,
    rp.yoy_growth_pct,
    rp.territory_rank_that_year,
    CASE
        WHEN rp.territory_rank_that_year <= 3 THEN 'Top Performer'
        WHEN rp.territory_rank_that_year > tc.n - 3 THEN 'Lowest Performer'
        ELSE 'Mid-Tier'
    END                                                        AS performance_tier
FROM analytics.regional_performance rp
JOIN latest_year ly       ON rp.order_year = ly.yr
JOIN territory_count tc   ON tc.order_year = rp.order_year
ORDER BY rp.territory_rank_that_year;

ALTER TABLE analytics.kpi_territory_rankings ADD PRIMARY KEY (territoryid);


-- ----------------------------------------------------------------------------
-- 25. INVENTORY HEALTH
-- Rolls inventory_analytics (per product-location) up to per-product, since
-- a product can sit in multiple locations with different stock statuses.
-- Flags overall status using the worst status across any location.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.kpi_inventory_health;
CREATE TABLE analytics.kpi_inventory_health AS
SELECT
    productid,
    product_name,
    category_name,
    SUM(quantity_on_hand)                                     AS total_quantity_on_hand,
    COUNT(DISTINCT locationid)                                 AS locations_stocked,
    COUNT(*) FILTER (WHERE stock_status = 'Reorder Needed')    AS locations_needing_reorder,
    COUNT(*) FILTER (WHERE stock_status = 'Low Stock')         AS locations_low_stock,
    CASE
        WHEN COUNT(*) FILTER (WHERE stock_status = 'Reorder Needed') > 0 THEN 'Reorder Needed'
        WHEN COUNT(*) FILTER (WHERE stock_status = 'Low Stock') > 0      THEN 'Low Stock'
        ELSE 'Healthy'
    END                                                          AS overall_stock_status
FROM analytics.inventory_analytics
GROUP BY productid, product_name, category_name;

ALTER TABLE analytics.kpi_inventory_health ADD PRIMARY KEY (productid);


-- ----------------------------------------------------------------------------
-- 26. SUPPLIER PERFORMANCE
-- Builds on vendor_analytics + purchase_line_analytics. Ranks vendors on
-- two independent axes (quality = reject rate, speed = lead time) since a
-- vendor can be fast but unreliable or slow but flawless — collapsing that
-- into one score would hide the trade-off.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.kpi_supplier_performance;
CREATE TABLE analytics.kpi_supplier_performance AS
SELECT
    va.businessentityid,
    va.vendor_name,
    va.creditrating,
    va.preferredvendorstatus,
    va.products_supplied,
    va.avg_lead_time_days,
    COALESCE(SUM(pla.line_cost), 0)                            AS total_purchase_spend,
    COUNT(DISTINCT pla.purchaseorderid)                        AS total_pos,
    ROUND(AVG(pla.reject_rate_pct), 2)                          AS avg_reject_rate_pct,
    RANK() OVER (ORDER BY AVG(pla.reject_rate_pct) ASC NULLS LAST)  AS quality_rank,
    RANK() OVER (ORDER BY va.avg_lead_time_days ASC NULLS LAST)     AS speed_rank
FROM analytics.vendor_analytics va
LEFT JOIN analytics.purchase_line_analytics pla ON pla.vendorid = va.businessentityid
GROUP BY va.businessentityid, va.vendor_name, va.creditrating,
         va.preferredvendorstatus, va.products_supplied, va.avg_lead_time_days;

ALTER TABLE analytics.kpi_supplier_performance ADD PRIMARY KEY (businessentityid);


-- ----------------------------------------------------------------------------
-- 27. PURCHASING TRENDS (monthly spend + growth)
-- Same LAG() pattern as monthly_revenue/quarterly_revenue in Stage 2, applied
-- to the purchasing side instead of the sales side.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.kpi_purchasing_trends;
CREATE TABLE analytics.kpi_purchasing_trends AS
WITH monthly AS (
    SELECT
        order_month,
        SUM(line_cost)                          AS total_spend,
        COUNT(DISTINCT purchaseorderid)         AS po_count,
        SUM(receivedqty)                         AS units_received,
        SUM(rejectedqty)                         AS units_rejected
    FROM analytics.purchase_line_analytics
    GROUP BY order_month
)
SELECT
    order_month,
    total_spend,
    po_count,
    units_received,
    units_rejected,
    LAG(total_spend) OVER (ORDER BY order_month)               AS prev_month_spend,
    ROUND(
        CASE WHEN LAG(total_spend) OVER (ORDER BY order_month) > 0
             THEN ((total_spend - LAG(total_spend) OVER (ORDER BY order_month))
                   / LAG(total_spend) OVER (ORDER BY order_month)) * 100
             ELSE NULL
        END, 2)                                                  AS mom_spend_growth_pct
FROM monthly
ORDER BY order_month;

ALTER TABLE analytics.kpi_purchasing_trends ADD PRIMARY KEY (order_month);


-- ----------------------------------------------------------------------------
-- 28. EXECUTIVE SUMMARY (one row)
-- The final rollup. Built LAST because it pulls from kpi_purchasing_trends
-- and kpi_inventory_health, which must already exist. Every number here
-- traces back to a Stage 1-4 object — nothing is computed fresh from raw
-- tables, which is the whole point of the layered pipeline.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.kpi_executive_summary;
CREATE TABLE analytics.kpi_executive_summary AS
SELECT
    (SELECT SUM(revenue) FROM analytics.monthly_revenue)                          AS total_revenue,
    (SELECT SUM(profit)  FROM analytics.monthly_revenue)                          AS total_profit,
    (SELECT ROUND(SUM(revenue) / NULLIF(SUM(order_count), 0), 2)
       FROM analytics.monthly_revenue)                                            AS avg_order_value,
    (SELECT COUNT(*) FROM analytics.customer_analytics WHERE total_orders > 0)    AS total_purchasing_customers,
    (SELECT SUM(total_orders) FROM analytics.customer_analytics)                  AS total_orders,
    (SELECT repeat_rate_pct FROM analytics.repeat_customers)                      AS repeat_rate_pct,
    (SELECT category_name FROM analytics.category_performance
       ORDER BY total_revenue DESC LIMIT 1)                                       AS top_category,
    (SELECT territory_name FROM analytics.territory_analytics
       ORDER BY total_revenue DESC LIMIT 1)                                       AS top_territory,
    (SELECT COUNT(*) FROM analytics.employee_analytics)                          AS total_employees,
    (SELECT SUM(total_spend) FROM analytics.kpi_purchasing_trends)               AS total_purchasing_spend,
    (SELECT COUNT(*) FROM analytics.kpi_inventory_health
       WHERE overall_stock_status <> 'Healthy')                                  AS products_needing_attention,
    NOW()                                                                          AS report_generated_at;


-- ============================================================================
-- END OF STAGE 4
-- Verify all 7 KPI tables exist and look sane:
--   SELECT * FROM analytics.kpi_top_bottom_products ORDER BY rank_type, revenue_rank;
--   SELECT * FROM analytics.kpi_employee_performance ORDER BY revenue_rank;
--   SELECT * FROM analytics.kpi_territory_rankings;
--   SELECT * FROM analytics.kpi_inventory_health WHERE overall_stock_status <> 'Healthy';
--   SELECT * FROM analytics.kpi_supplier_performance ORDER BY quality_rank;
--   SELECT * FROM analytics.kpi_purchasing_trends;
--   SELECT * FROM analytics.kpi_executive_summary;
-- ============================================================================

