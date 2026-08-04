"""
test_db_connection.py
Quick sanity check: confirms SQLAlchemy + psycopg2 can reach the
Dockerized Postgres container and read the seeded property data.

Run with your venv activated:
    python test_db_connection.py
"""

from sqlalchemy import create_engine, text

# Same credentials as docker-compose.yml
DATABASE_URL = "postgresql://rania:mm1234@localhost:5432/realestate_agent"

def main():
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        # 1. Basic connectivity check
        result = conn.execute(text("SELECT count(*) FROM properties;"))
        count = result.scalar()
        print(f"Connected successfully. properties table has {count} rows.")

        # 2. A slightly more realistic query — same shape the agent will run
        result = conn.execute(text("""
            SELECT title, price, bedrooms, location_id
            FROM properties
            WHERE listing_status = 'buy' AND price < 50000000
            ORDER BY price ASC
            LIMIT 5;
        """))
        print("\nSample query — properties for sale under 5 crore:")
        for row in result:
            print(f"  - {row.title} | PKR {row.price:,.0f} | {row.bedrooms} bed")

if __name__ == "__main__":
    main()