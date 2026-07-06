CREATE TABLE orders (
    row_id INT PRIMARY KEY,
    order_id VARCHAR(20),
    order_date DATE,
    ship_date DATE,
    ship_mode VARCHAR(50),
    customer_id VARCHAR(20),
    customer_name VARCHAR(100),
    segment VARCHAR(50),
    country VARCHAR(50),
    city VARCHAR(50),
    state VARCHAR(50),
    postal_code VARCHAR(20),
    region VARCHAR(50),
    product_id VARCHAR(20),
    category VARCHAR(50),
    sub_category VARCHAR(50),
    product_name VARCHAR(200),
    sales NUMERIC(10,2),
    quantity INT,
    discount NUMERIC(4,2),
    profit NUMERIC(10,2)
);


SET datestyle = 'MDY';

COPY orders(row_id, order_id, order_date, ship_date, ship_mode, customer_id, 
customer_name, segment, country, city, state, postal_code, region, 
product_id, category, sub_category, product_name, sales, quantity, discount, profit)
FROM 'D:\Netixsol\Week-3\Day-1\superstore.csv'
DELIMITER ','
CSV HEADER;


select count(*) from orders;


SELECT * FROM orders LIMIT 10;



SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'orders'
ORDER BY ordinal_position;