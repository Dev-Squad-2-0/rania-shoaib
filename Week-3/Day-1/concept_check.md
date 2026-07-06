# Concept Check — SQL & Databases Fundamentals

### 1. What problem does SQL solve that CSV files cannot?
CSV files are just flat text files ...they have no built-in way to enforce data types, relationships, or rules (like "this column must be unique" or "this value must reference another table"). They also can't efficiently handle large datasets, concurrent access by multiple users, or complex queries joining multiple sources. SQL (via a relational database) solves this by adding structure, enforced rules (constraints), indexing for fast lookups, and a query language to filter, join, and aggregate data reliably — even when the dataset is huge or many people/apps are reading and writing at once.

### 2. What is the difference between a database table and a spreadsheet?
A spreadsheet is a flexible grid where any cell can hold any type of data, formulas live alongside data, and there's little to no enforcement of structure. A database table has a strictly defined schema — every column has a fixed data type, constraints (like NOT NULL or UNIQUE) are enforced automatically, and relationships between tables are formally defined. Tables are also built to scale to millions/billions of rows and support simultaneous access from many users without corrupting data, which spreadsheets aren't designed to do.

### 3. What is a Primary Key?
A Primary Key is a column (or combination of columns) that uniquely identifies each row in a table. It cannot contain NULL values and cannot repeat so no two rows can share the same primary key value. In our `orders` table, `row_id` is the Primary Key, ensuring every order record is unique and identifiable.

### 4. What is a Foreign Key?
A Foreign Key is a column in one table that references the Primary Key of another table, creating a link between the two. It enforces "referential integrity" which means you can't insert a value in the foreign key column unless it already exists in the referenced table. For example, if we had a separate `customers` table, `customer_id` in `orders` could be a Foreign Key pointing to it, ensuring every order belongs to a real, existing customer.

### 5. What is the difference between `WHERE` and `HAVING`?
`WHERE` filters individual rows *before* any grouping or aggregation happens. `HAVING` filters *after* rows have been grouped (with `GROUP BY`) and aggregate functions have been calculated so it filters the groups themselves. For example, `WHERE` could filter to only orders from "California," while `HAVING` could filter to only show states where `SUM(sales) > 10000`.

### 6. What is the difference between `ORDER BY` and `GROUP BY`?
`ORDER BY` sorts the final result set into a specific order (ascending or descending) without changing the number of rows returned. `GROUP BY` collapses multiple rows sharing the same value in a column into a single summarized row, typically used alongside aggregate functions like `SUM()` or `COUNT()`. They solve different problems: one is about sequence, the other is about summarization.

### 7. What does `DISTINCT` do?
`DISTINCT` removes duplicate rows from a query's result, returning only unique values. For example, `SELECT DISTINCT region FROM orders;` would list each region that appears in the table exactly once, no matter how many orders came from it.

### 8. When should you use `LIMIT`?
`LIMIT` restricts the number of rows returned by a query. It's useful when previewing data from a large table (like checking the first 10 rows instead of pulling all 9,994), improving query performance during testing, or building paginated results in an application.

### 9. What are aggregate functions?
Aggregate functions perform a calculation across multiple rows and return a single summary value. Common ones include `COUNT()` (number of rows), `SUM()` (total), `AVG()` (average), `MIN()`, and `MAX()`. They're often paired with `GROUP BY` to get summaries per category, like total sales per region.

### 10. Why do Data Scientists prefer databases over Excel for large datasets?
Excel struggles with performance and stability once datasets grow into hundreds of thousands of rows, and it has no real way to enforce data integrity or manage relationships between different datasets. Databases handle millions of rows efficiently, support fast indexed lookups, allow multiple people/processes to query the same data concurrently without conflicts, and offer a powerful, standardized query language (SQL) for filtering, joining, and aggregating data precisely. This makes databases far more reliable and scalable for real analytical work.

---

## What I Did
- Installed PostgreSQL 18 and pgAdmin 4
- Created a database named `superstore_db`
- Created an `orders` table matching the Superstore dataset's 21 columns
- Imported the Superstore CSV (9,994 rows) using the `COPY` command, resolving a `UTF8` encoding error by re-saving the file as UTF-8
- Verified the import using `SELECT COUNT(*)`, `SELECT * LIMIT 10`, and `information_schema.columns`
