# Concept Check Answers

### 1. Why do relational databases split data into multiple tables?
So the same piece of information isn't repeated over and over. If a customer's city was stored on every single rental row, you'd have thousands of duplicate copies of it, and if that city name ever needed fixing, you'd have to update it everywhere. Splitting data into separate tables keeps each fact stored in one place, which makes updates safer and the whole database smaller and more consistent.

### 2. Difference between INNER JOIN and LEFT JOIN
An INNER JOIN only gives you rows where both tables actually have a match. A LEFT JOIN gives you every row from the left table no matter what, and just fills in NULLs for any columns from the right table when there's no match. So if you want "only customers who paid," use INNER JOIN. If you want "every customer, and show me their payment if they made one," use LEFT JOIN.

### 3. When would you use a FULL OUTER JOIN?
When you need to see everything from both tables, matched up where possible, but you don't want to lose rows from either side just because they don't have a match. It's mostly useful for checking data quality, like finding customers with no payments and payments with no customer at the same time, in one query.

### 4. Why are Primary Keys and Foreign Keys important?
A Primary Key makes sure every row in a table is uniquely identifiable. A Foreign Key points to a Primary Key in another table and makes sure that link is actually valid, so you can't have a rental pointing to a customer that doesn't exist. Without these two things, JOINs wouldn't really work, and the data could end up messy or inconsistent.

### 5. Explain normalization in simple words
It basically means organizing your database so you're not storing the same information in multiple places. Instead of cramming everything into one huge table, you break it into smaller related tables, connected through keys, so each fact lives in exactly one spot.

### 6. What is an ER Diagram?
It's a visual map of the database. It shows the tables, what columns each one has, and how they connect to each other through primary and foreign keys. It's basically the easiest way to understand a database's structure without reading through every table one by one.

### 7. What happens if a JOIN condition is incorrect?
You get wrong results, and it's not always obvious. Sometimes you end up with way more rows than expected because everything is matching with everything (a Cartesian-product type situation), and other times you lose rows because almost nothing matches. This is especially dangerous with SUM or COUNT, since the numbers can look totally reasonable while actually being wrong.
