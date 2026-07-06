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

select customer_name, sales from orders;

select distinct customer_name from orders;

select distinct region from orders;

select state from orders;

select * from orders where state = 'California';

select customer_name, state, city, sales from orders where state = 'California' and sales > 500;


select customer_name, sales from orders
order by sales desc;

select * from orders 
order by sales 
desc 
limit 5;

select customer_name as customer, sales as order_value from orders
order by sales desc;

select distinct segment from orders;

select count(*) from orders where segment = 'Consumer';

select sum(sales) from orders;

select avg(profit) from orders;

select min(sales) as cheapest_order, max(sales) as priciest_order from orders;

select region, sum(sales) as total_sales
from orders
group by region
order by total_sales desc
having sum(sales) > 20000;

select region, sum(sales) as total_sales
from orders
group by region
having sum(sales) > 400000;

select category, count(*) as order_count 
from orders 
group by category;


