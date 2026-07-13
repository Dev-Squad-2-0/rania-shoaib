# Data Dictionary

Every view and table produced by `analytics_pipeline.sql`, grouped by the stage that creates it. For each object: what it is, what one row represents (its grain), and what it depends on.

All objects live in the `analytics` schema. Nothing outside Stage 1 ever queries the raw `sales` / `production` / `humanresources` / `purchasing` / `person` schemas directly, everything downstream reads from these instead.

---

## Stage 1 — Base Analytics Layer

| Object | Type | Grain (one row per) | Built from | Notes |
|---|---|---|---|---|
| `customer_analytics` | View | Customer | `sales.customer`, `person.person`, `sales.salesterritory`, `sales.salesorderheader` | Rolls up each customer's full order history (total orders, lifetime revenue, avg order value, first/last order date, active/inactive status) so nothing downstream has to re-touch `salesorderheader`. |
| `product_analytics` | View | Product | `production.product`, `productsubcategory`, `productcategory` | Calculates unit margin and margin % once here (`listprice - standardcost`) so it's never recomputed later. |
| `sales_line_analytics` | View | Order line | `sales.salesorderdetail`, `salesorderheader`, `product_analytics` | The foundation view. Nearly every revenue/profit metric in Stages 2–4 aggregates from here instead of re-joining header + detail. Cancelled orders (status 6) are excluded. |
| `employee_analytics` | View | Employee | `humanresources.employee`, `person.person`, `employeedepartmenthistory`, `department` | Current department only (history rows where `enddate IS NULL`). |
| `salesperson_analytics` | View | Salesperson | `sales.salesperson`, `employee_analytics`, `sales_line_analytics` | Adds order count, revenue, and profit generated per salesperson. |
| `territory_analytics` | View | Territory | `sales.salesterritory`, `sales_line_analytics` | Revenue/profit/customer counts per territory. |
| `inventory_analytics` | View | Product + location | `production.productinventory`, `product_analytics`, `production.product`, `location` | Flags each product/location combination as Healthy, Low Stock, or Reorder Needed. |
| `vendor_analytics` | View | Vendor | `purchasing.vendor`, `productvendor` | Product count and average lead time per vendor. |
| `purchase_line_analytics` | View | PO line | `purchasing.purchaseorderdetail`, `purchaseorderheader`, `vendor_analytics`, `product_analytics` | Adds reject rate % per line. |
| `date_dim` | Table | Calendar day | Generated via `GENERATE_SERIES` over the full order date range | Used for consistent month/quarter grouping elsewhere in the pipeline. |

---

## Stage 2 — Business Metrics

| Object | Type | Grain (one row per) | Built from | Notes |
|---|---|---|---|---|
| `monthly_revenue` | View | Month | `sales_line_analytics` | Adds `LAG()`-based month-over-month growth %. |
| `quarterly_revenue` | View | Quarter | `sales_line_analytics` | Same pattern as `monthly_revenue`, one grain up. |
| `product_performance` | View | Product | `sales_line_analytics` | Adds `RANK()` by revenue and by profit, plus profit margin %. |
| `category_performance` | View | Product category | `product_performance` | Rolls product performance up to category, splitting revenue into high-margin (≥40%) vs low-margin buckets. |
| `customer_rfm` | View | Customer (with ≥1 order) | `customer_analytics` | Raw Recency / Frequency / Monetary values, kept separate so they stay inspectable before being bucketed into segments. |
| `salesperson_ranking` | View | Salesperson | `salesperson_analytics` | Adds revenue rank and a quota status label (Quota Met / Near Quota / Below Quota / No Quota Set). |

---

## Stage 3 — Customer Segmentation & Regional Analysis

| Object | Type | Grain (one row per) | Built from | Notes |
|---|---|---|---|---|
| `customer_segments` | View | Customer | `customer_rfm` | Uses `NTILE(4)` to quartile customers on recency and monetary value, then labels each into Champions / Loyal Customers / New-Promising / At Risk / Needs Attention / Lost. |
| `customer_ltv` | View | Customer (with ≥1 order) | `customer_analytics` | Annualized lifetime value based on tenure (chained CTE: tenure first, then value derived from it). |
| `repeat_customers` | View | Single row (company-wide) | `customer_analytics` | One-time vs repeat buyer counts and repeat rate %. |
| `customer_retention` | View | Year | `sales_line_analytics` | Year-over-year retention %, using a self-referencing CTE with `EXISTS` to check prior-year purchases. |
| `regional_performance` | View | Territory + year | `sales_line_analytics`, `territory_analytics` | Year-over-year revenue growth and rank per territory per year. |

---

## Stage 4 — Executive KPI Tables

These are materialized **tables**, not views, snapshots the notebook reads from directly instead of re-running window functions on every load.

| Object | Type | Grain (one row per) | Built from | Notes |
|---|---|---|---|---|
| `kpi_top_bottom_products` | Table | Product (top 10 + bottom 10 only) | `product_performance` | Tagged with `rank_type` ('Top 10' / 'Bottom 10') so the dashboard filters instead of running two separate queries. |
| `kpi_employee_performance` | Table | Salesperson | `salesperson_ranking`, `salesperson_analytics` | Adds revenue contribution % of company total and a direct above/at/below team average comparison. |
| `kpi_territory_rankings` | Table | Territory (latest year only) | `regional_performance` | Adds a performance tier label (Top Performer / Mid-Tier / Lowest Performer). |
| `kpi_inventory_health` | Table | Product | `inventory_analytics` | Rolls per-location stock status up to per-product, using the worst status across any location. |
| `kpi_supplier_performance` | Table | Vendor | `vendor_analytics`, `purchase_line_analytics` | Ranks vendors independently on quality (reject rate) and speed (lead time), rather than one blended score, since a vendor can be fast-but-unreliable or slow-but-flawless. |
| `kpi_purchasing_trends` | Table | Month | `purchase_line_analytics` | Monthly purchasing spend with month-over-month growth %. |
| `kpi_executive_summary` | Table | Single row (company-wide) | All of the above | Built last since it depends on `kpi_purchasing_trends` and `kpi_inventory_health` existing first. One-row rollup: total revenue, total profit, repeat rate, top category, top territory, etc. |

---

## Dependency order

If you ever need to rebuild the pipeline from scratch, objects must be created in this order, since each stage depends on the one before it:

```
Stage 1 (10 base views/tables, no dependencies on each other except product_analytics -> sales_line_analytics)
   ↓
Stage 2 (6 views, depend only on Stage 1)
   ↓
Stage 3 (5 views, depend on Stage 1 + 2)
   ↓
Stage 4 (7 tables, depend on Stage 1 + 2 + 3 — kpi_executive_summary must be built last)
```
