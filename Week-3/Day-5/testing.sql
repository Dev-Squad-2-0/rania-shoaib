-------------------------- testing stage 2 ------------------------------------------

SELECT * FROM analytics.monthly_revenue;

-- 1. Ranks should start at 1 and have no gaps/duplicates at the top
SELECT revenue_rank, COUNT(*) FROM analytics.product_performance GROUP BY revenue_rank ORDER BY revenue_rank LIMIT 5;
-- Expect: rank 1 appears exactly once (unless there's an exact revenue tie)

-- 2. No negative revenue, no NULL product names
SELECT COUNT(*) FROM analytics.product_performance WHERE total_revenue < 0 OR product_name IS NULL;
-- Expect: 0

-- 3. Total revenue across all products should roughly match total from monthly_revenue
SELECT SUM(total_revenue) FROM analytics.product_performance;
SELECT SUM(revenue) FROM analytics.monthly_revenue;
-- Expect: these two numbers should match closely (both derive from sales_line_analytics)



-- 1. Row count should equal number of salespeople
SELECT COUNT(*) FROM analytics.salesperson_ranking;
SELECT COUNT(*) FROM sales.salesperson;
-- Expect: these two numbers match exactly

-- 2. quota_status buckets should look sane (not all one value)
SELECT quota_status, COUNT(*) FROM analytics.salesperson_ranking GROUP BY quota_status;

-- 3. rank 1 should have the highest revenue
SELECT * FROM analytics.salesperson_ranking WHERE revenue_rank = 1;


SELECT * FROM analytics.product_performance ORDER BY revenue_rank LIMIT 10;




------------------------- Stage 3 sanity tests ----------------------------------------------------
SELECT segment, COUNT(*) FROM analytics.customer_segments GROUP BY segment;
SELECT * FROM analytics.repeat_customers;
SELECT * FROM analytics.regional_performance WHERE order_year = 2023;



------------------------------- Stage 4 sanity checks ---------------------------------------------------------

SELECT category_name, overall_stock_status, COUNT(*)
FROM analytics.kpi_inventory_health
GROUP BY category_name, overall_stock_status
ORDER BY category_name, overall_stock_status;


SELECT product_name, quantity_on_hand, safetystocklevel, reorderpoint, stock_status
FROM analytics.inventory_analytics
WHERE category_name = 'Bikes'
ORDER BY product_name
LIMIT 15;

SELECT
    (SELECT COUNT(*) FROM sales.customer)             AS customers,
    (SELECT COUNT(*) FROM sales.salesorderheader)      AS sales_orders,
    (SELECT COUNT(*) FROM sales.salesorderdetail)      AS sales_order_lines,
    (SELECT COUNT(*) FROM production.product)          AS products,
    (SELECT COUNT(*) FROM purchasing.vendor)            AS vendors,
    (SELECT COUNT(*) FROM purchasing.purchaseorderheader) AS purchase_orders,
    (SELECT COUNT(*) FROM humanresources.employee)      AS employees;

SELECT MIN(orderdate) AS earliest_order, MAX(orderdate) AS latest_order
FROM sales.salesorderheader;

SELECT pg_size_pretty(pg_database_size('customerstore_db')) AS database_size;


SELECT
    (SELECT COUNT(*) FROM information_schema.views WHERE table_schema = 'analytics') AS view_count,
    (SELECT COUNT(*) FROM information_schema.tables
       WHERE table_schema = 'analytics' AND table_type = 'BASE TABLE') AS table_count;

SELECT schemaname, COUNT(*) AS table_count
FROM pg_tables
WHERE schemaname IN ('sales','production','humanresources','purchasing','person')
GROUP BY schemaname
ORDER BY schemaname;


SELECT table_name
FROM information_schema.views
WHERE table_schema = 'analytics'
ORDER BY table_name;


------------------------------ Screenshot queries ------------------------------------------------------
-- p1 
SELECT * FROM analytics.sales_line_analytics LIMIT 5;

-- p2
SELECT * FROM analytics.product_performance ORDER BY revenue_rank LIMIT 10;

-- p3
SELECT quota_status, COUNT(*) FROM analytics.salesperson_ranking GROUP BY quota_status;

-- p4

SELECT * FROM analytics.salesperson_ranking WHERE revenue_rank = 1;

-- p5
SELECT segment, COUNT(*) FROM analytics.customer_segments GROUP BY segment;

-- p6 
SELECT * FROM analytics.repeat_customers;

--p7

SELECT * FROM analytics.regional_performance WHERE order_year = 2023;

-- p8
SELECT category_name, overall_stock_status, COUNT(*) FROM analytics.kpi_inventory_health GROUP BY category_name, overall_stock_status ORDER BY category_name, overall_stock_status;

-- p9

SELECT product_name, quantity_on_hand, safetystocklevel, reorderpoint, stock_status FROM analytics.inventory_analytics WHERE category_name = 'Bikes' ORDER BY product_name LIMIT 15;

