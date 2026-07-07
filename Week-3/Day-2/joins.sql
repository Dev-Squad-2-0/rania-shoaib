-- 1.Display Customer Name, Email, City, and Country.

select c.first_name || c.last_name As customer_name, c.email, ci.city, co.country
from customer c
inner join address a on c.address_id = a.address_id
inner join city ci on ci.city_id = a.city_id 
inner join country co on co.country_id = ci.country_id  

-----------------------------------------------------------------------------------

-- 2/3.Display every payment with Customer Name, Film Title, and Amount Paid.

select c.first_name || c.last_name as customer_name, f.title, p.amount as Amount_Paid, p.payment_date
from payment p 
inner join customer c on p.customer_id = c.customer_id
inner join rental r on r.rental_id = p.rental_id
inner join inventory i on i.inventory_id = r.inventory_id 
inner join film f on f.film_id = i.film_id 
order by p.payment_date

-----------------------------------------------------------------------------------

-- 4.Find the Top 10 customers based on total amount spent.

select c.first_name || ' ' || c.last_name as customer_name,
c.customer_id,
SUM(p.amount) as total_spent
from customer c
inner join payment p on p.customer_id = c.customer_id
group by c.customer_id, customer_name
order by total_spent desc
limit 10;

-----------------------------------------------------------------------------------


-- 5.Display each film with its Category and Rental Rate.

select f.title, cat.name as category_name, f.rental_rate
from film f
inner join film_category fc on f.film_id = fc.film_id 
inner join category cat on cat.category_id = fc.category_id 
order by f.title

-----------------------------------------------------------------------------------


-- 6.Find all actors who appeared in each film.

select a.first_name || ' ' || a.last_name as actor_name, f.title 
from film f
inner join film_actor fa on fa.film_id = f.film_id
inner join actor a on fa.actor_id = a.actor_id 
order by f.title, actor_name

-----------------------------------------------------------------------------------


-- 7.Count how many films belong to each category.

select cat.name as category_name, count(fc.film_id) as total_films
from category cat
inner join film_category fc on fc.category_id = cat.category_id 
group by cat.name
order by total_films desc

-----------------------------------------------------------------------------------

-- 8.Which categories generated the highest revenue? (Hint: This requires joining multiple tables.)

select cat.name as category, sum(p.amount) as total_revenue
from category cat
inner join film_category fc on fc.category_id = cat.category_id 
inner join film f on f.film_id = fc.film_id 
inner join inventory i on i.film_id = f.film_id
inner join rental r on r.inventory_id = i.inventory_id 
inner join payment p on r.rental_id = p.rental_id 
group by category
order by total_revenue desc

-----------------------------------------------------------------------------------

-- 9.Find customers who have rented more than 20 films.

select c.customer_id , c.first_name || ' ' || c.last_name as customer_name, sum(r.rental_id) as total_rentals
from customer c
inner join rental r on r.customer_id = c.customer_id 
group by c.customer_id, customer_name
having count(r.rental_id) > 20
order by total_rentals desc


-- 10.Which cities generated the highest rental revenue?

select ci.city, sum(p.amount) as total_revenue
from city ci 
inner join address a on a.city_id = ci.city_id
inner join customer c on c.address_id = a.address_id
inner join payment p on p.customer_id = c.customer_id
group by ci.city
order by total_revenue desc

-------------------------------------------------------------------------------------

-- BONUS 

select a.first_name || ' ' || a.last_name as actor_name,
sum(p.amount) as total_revenue
from actor a
inner join film_actor fa on a.actor_id = fa.actor_id
inner join film f on fa.film_id = f.film_id
inner join inventory i on f.film_id = i.film_id
inner join rental r on i.inventory_id = r.inventory_id
inner join payment p on r.rental_id = p.rental_id
group by a.actor_id, actor_name
order by total_revenue desc
limit 1;


--------------------------------------------------------------------------------------
