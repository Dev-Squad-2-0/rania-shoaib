-- ============================================================================
-- ONE-TIME FIX: reload BusinessEntity, Person, and Store
-- These 3 tables were empty because their original CSVs used a corrupted
-- delimiter ("+|" instead of tab). Fixed versions are in fixed_csvs.zip.
--
-- HOW TO RUN THIS FILE:
-- \copy only works in real psql (not pgAdmin's Query Tool), so run this one
-- via Command Prompt, same way you ran install.sql originally:
--
--   "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d customerstore_db -f reload_fixed_tables.sql
--
-- IMPORTANT: extract fixed_csvs.zip into the SAME folder as this .sql file
-- before running (Person.csv, Store.csv, BusinessEntity.csv should sit right
-- next to reload_fixed_tables.sql).
-- ============================================================================

-- BusinessEntity already loaded successfully last run (20,777 rows) -- skip
-- it this time to avoid a duplicate primary key error. Only re-run if you
-- see businessentity count = 0 in the verification query below.
-- \copy Person.BusinessEntity FROM './BusinessEntity.csv' DELIMITER E'\t' CSV;

-- Load Person (depends on BusinessEntity)
-- NOTE: text format (not CSV) -- Person.csv has embedded XML with literal
-- double quotes (xmlns="..."), which CSV format's quote-parsing corrupts.
\copy Person.Person FROM './Person.csv' DELIMITER E'\t';

-- Load Store (independent of Person)
-- Same reasoning -- Store.csv also has embedded XML with double quotes.
\copy Sales.Store FROM './Store.csv' DELIMITER E'\t';

-- Verify
SELECT 'person.businessentity' AS tbl, COUNT(*) FROM person.businessentity
UNION ALL SELECT 'person.person', COUNT(*) FROM person.person
UNION ALL SELECT 'sales.store', COUNT(*) FROM sales.store;