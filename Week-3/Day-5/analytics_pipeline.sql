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
CREATE OR REPLACE VIEW analytics.customer_analytics AS
SELECT
    c.customerid,
    c.territoryid,
    st.name                                   AS territory_name,
    st."group"                                AS territory_group,
    p.firstname,
    p.lastname,
    p.firstname || ' ' || p.lastname          AS full_name,
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
LEFT JOIN sales.salesterritory st  ON st.territoryid = c.territoryid
LEFT JOIN sales.salesorderheader soh ON soh.customerid = c.customerid
GROUP BY c.customerid, c.territoryid, st.name, st."group", p.firstname, p.lastname;


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
    sod.linetotal                              AS line_revenue,
    (sod.orderqty * pa.standardcost)           AS line_cost,
    (sod.linetotal - (sod.orderqty * pa.standardcost)) AS line_profit,
    pa.category_name,
    pa.subcategory_name,
    pa.product_name
FROM sales.salesorderdetail sod
JOIN sales.salesorderheader soh   ON soh.salesorderid = sod.salesorderid
JOIN analytics.product_analytics pa ON pa.productid = sod.productid
WHERE soh.status <> 6; -- exclude cancelled orders (status 6 = Cancelled in AdventureWorks)


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