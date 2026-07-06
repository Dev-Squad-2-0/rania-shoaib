# Superstore SQL Database Project

This project sets up a PostgreSQL database, imports the Sample Superstore dataset, and runs basic verification queries as part of a SQL fundamentals exercise.

## Dataset
- **Name:** Sample Superstore
- **Source:** Kaggle — [Sample Superstore Dataset](https://www.kaggle.com/datasets/vivek468/superstore-dataset-final)
- **Rows:** 9,994
- **Columns:** 21 (Row ID, Order ID, Order Date, Ship Date, Ship Mode, Customer ID, Customer Name, Segment, Country, City, State, Postal Code, Region, Product ID, Category, Sub-Category, Product Name, Sales, Quantity, Discount, Profit)

## Tools Used
- PostgreSQL 18
- pgAdmin 4

## Setup Steps

### 1. Install PostgreSQL 18
Downloaded and installed PostgreSQL 18 from [postgresql.org](https://www.postgresql.org/download/), choosing a custom install directory on drive D. pgAdmin 4 was included as part of the installation.

### 2. Install pgAdmin 4
Included automatically with the PostgreSQL installer (Stack Builder can also install it separately if skipped).

### 3. Create a Database
In pgAdmin: right-click **Databases** → **Create** → **Database**, named `superstore_db`.

### 4. Create the Table
Ran the `CREATE TABLE` statement (see `setup.sql`) in the Query Tool to create the `orders` table with 21 columns matching the CSV structure.

### 5. Import the CSV
- Original CSV import failed with: `ERROR: invalid byte sequence for encoding "UTF8": 0xa0` — caused by the file being saved in Windows-1252 encoding (non-breaking space characters).
- **Fix:** re-saved the CSV as UTF-8 (Excel: File → Save As → "CSV UTF-8 (Comma delimited)").
- Also set `SET datestyle = 'MDY';` to correctly parse the M/D/YYYY date format in the CSV.
- Ran the `COPY` command (see `setup.sql`) in the Query Tool to import all 9,994 rows.

### 6. Verify the Import
Ran the following queries to confirm the data loaded correctly:
```sql
SELECT COUNT(*) FROM orders;
SELECT * FROM orders LIMIT 10;
SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'orders';
```

## Files in This Repository
- `README.md` — this file
- `setup.sql` — table creation and data import SQL commands
- `concept_check.md` — answers to SQL fundamentals conceptual questions
- `superstore.csv` — the dataset used (or see Dataset section above for source link)
- `/screenshots` — folder containing screenshots of each step (database created, table created, table structure, COUNT query, LIMIT query, information_schema query)

## How to Reproduce
1. Install PostgreSQL and pgAdmin
2. Create a database named `superstore_db`
3. Open the Query Tool connected to `superstore_db`
4. Run `setup.sql` (update the file path in the `COPY` command to match your local CSV location)
5. Run the verification queries at the bottom of `setup.sql`
