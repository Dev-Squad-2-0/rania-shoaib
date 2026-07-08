------------------- Part 1 — Aggregation Basics --------------------------------------------------------

-- 1. Find the total revenue generated per store.

select i.store_id, sum(p.amount) as total_revenue
from payment p
join rental r    on p.rental_id = r.rental_id
join inventory i on r.inventory_id = i.inventory_id
group by i.store_id
order by i.store_id


-- 2. Find the average rental duration per film category.

select c.name as category, round(avg(extract(epoch from (r.return_date - r.rental_date)) / 86400),2) AS avg_rental_days
from rental r
join inventory i on i.inventory_id = r.inventory_id
join film_category fc on fc.film_id = i.film_id
join category c on fc.category_id = c.category_id
where r.return_date is not null
group by c.name
order by avg_rental_days desc;

-- 3. Find the number of rentals made each month.

select date_trunc('month', rental_date)::date as rental_month, count(*) as num_rentals
from rental 
group by date_trunc('month', rental_date)
order by rental_month

 
-- 4. Find categories with more than 50 films (use HAVING).

select c.name as category, count(fc.film_id) as num_films
from category c
inner join film_category fc on fc.category_id = c.category_id 
group by c.name
having count(fc.film_id) > 50
order by num_films desc


------ Part 2 — Subquery Challenges --------------------------------------------------------------

-- 5. Find customers who spent more than the average customer spend.

select spend.customer_id, spend.total_spent
from (
   select customer_id, sum(amount) as total_spent
   from payment
   group by customer_id

) as spend
where spend.total_spent > (
select avg(customer_total)
from (
   select sum(amount) as customer_total
   from payment
   group by customer_id) as avg_calc
)
order by spend.total_spent desc

-- 6. Find the film(s) with the highest rental rate in each category (use a correlated subquery).

select cat.name as category,
f.title, 
f.rental_rate
from film f
join film_category fc on fc.film_id = f.film_id
join category cat on fc.category_id = cat.category_id 
where f.rental_rate = (
  select max(f2.rental_rate)
  from film f2
  join film_category fc2 on fc.film_id = fc2.film_id 
  where fc2.category_id = fc.category_id 
 
)
order by cat.name, f.title;



-- 7. Find customers who have never rented a film (use NOT IN / NOT EXISTS).


select c.customer_id, c.first_name || ' ' || c.last_name as customer_name
from customer c
where not exists (
select 1
from rental r
where r.customer_id = c.customer_id
);

-- alternative 

select
    customer_id,
    first_name,
    last_name
from customer
where customer_id not in (
    select customer_id from rental where customer_id is not null
);
 

-- 8. Find the store with the highest total revenue using a subquery in the WHERE clause.


select store_revenue.store_id, store_revenue.total_revenue
from (
  select i.store_id , sum(p.amount) as total_revenue
  from payment p
  join rental r on r.rental_id = p.rental_id
  join inventory i on i.inventory_id = r.inventory_id
  group by i.store_id

 ) as store_revenue
where store_revenue.total_revenue = (
  select max(t.total_revenue)
  from (
  select i.store_id , sum(p.amount) as total_revenue
  from payment p
  join rental r on r.rental_id = p.rental_id
  join inventory i on i.inventory_id = r.inventory_id
  group by i.store_id
  

  ) as t
 
);


------- Part 3 — CTE & Window Function Challenges -------------------------------------------------

-- 9. Using a CTE, rank customers by total spend within each city.

with customer_city_spend as (
select cu.customer_id,
cu.first_name || ' ' || cu.last_name as customer_name,
ci.city,
sum(p.amount) as total_spent
from customer cu
join address a on a.address_id = cu.address_id
join city ci on ci.city_id = a.city_id 
join payment p on cu.customer_id = p.customer_id 
group by cu.customer_id, customer_name, ci.city
)

select city, customer_name, total_spent,
rank() over (partition by city order by total_spent desc) as spend_rank
from customer_city_spend
order by city, spend_rank 



-- 10. Using ROW_NUMBER(), find the most recently rented film for each customer.

with ranked_rentals as (
select r.customer_id, f.title, r.rental_date, cu.first_name,
row_number() over (partition by r.customer_id order by r.rental_date desc) as rn
from rental r 
join inventory i on i.inventory_id = r.inventory_id
join film f on i.film_id = f.film_id
join customer cu on cu.customer_id = r.customer_id
)

select  customer_id, first_name,title as most_recent_film, rental_date
from ranked_rentals
where rn = 1 
order by customer_id;



-- 11. Using a CTE, calculate month-over-month rental revenue growth.

with monthly_revenue as (
    select DATE_TRUNC('month', payment_date) as revenue_month,
        SUM(amount) as total_revenue
    FROM payment
    GROUP BY DATE_TRUNC('month', payment_date)
),
revenue_with_prev as (
    SELECT
        revenue_month,
        total_revenue,
        LAG(total_revenue) OVER (ORDER BY revenue_month) AS prev_month_revenue
    FROM monthly_revenue
)
SELECT
    revenue_month,
    total_revenue,
    prev_month_revenue,
    ROUND(
        (total_revenue - prev_month_revenue) / NULLIF(prev_month_revenue, 0) * 100,
        2
    ) as mom_growth_pct
FROM revenue_with_prev
ORDER BY revenue_month;
 
 

-- 12. Find the top 3 highest-grossing films per category using RANK() inside a CTE.

 WITH film_revenue AS (
    SELECT
        f.film_id,
        f.title,
        c.name AS category,
        SUM(p.amount) AS total_revenue
    FROM payment p
    JOIN rental r          ON p.rental_id = r.rental_id
    JOIN inventory i       ON r.inventory_id = i.inventory_id
    JOIN film f            ON i.film_id = f.film_id
    JOIN film_category fc  ON f.film_id = fc.film_id
    JOIN category c        ON fc.category_id = c.category_id
    GROUP BY f.film_id, f.title, c.name
),
ranked_films AS (
    SELECT
        *,
        RANK() OVER (PARTITION BY category ORDER BY total_revenue DESC) AS revenue_rank
    FROM film_revenue
)
SELECT
    category,
    title,
    total_revenue,
    revenue_rank
FROM ranked_films
WHERE revenue_rank <= 3
ORDER BY category, revenue_rank;
 

-- Bonus Challenge
-- Without looking at any online solution, write a single query (using CTEs) that finds: Which staff member processed the highest revenue in each store, 
-- and what percentage of that store's total revenue did they contribute? This requires combining aggregation, a CTE, and a percentage calculation in the 
-- same query.

with staff_revenue as (
select
      s.store_id, s.staff_id, s.first_name, s.last_name,
      SUM(p.amount) as staff_revenue
  from payment p
  join staff s on p.staff_id = s.staff_id
  group by s.store_id, s.staff_id, s.first_name, s.last_name
),
store_revenue as (
select
      store_id, sum(staff_revenue) as store_total_revenue
from staff_revenue
group by store_id),
ranked_staff as (
    select sr.*, rank() over (partition by sr.store_id order by sr.staff_revenue DESC) as rnk
    from staff_revenue sr
)
select
  rs.store_id, rs.first_name, rs.last_name, rs.staff_revenue,st.store_total_revenue,
  ROUND(rs.staff_revenue / st.store_total_revenue * 100, 2) as pct_of_store_revenue
from ranked_staff rs
join store_revenue st on st.store_id = rs.store_id
where rs.rnk = 1
order by rs.store_id;




