from sqlalchemy import text
from crm_store import engine

with engine.begin() as conn:
    rows = conn.execute(text("""
        SELECT ah.id, c.phone, ah.event_id, ah.status, ah.property_title, ah.date
        FROM appointment_history ah
        JOIN clients c ON c.id = ah.client_id
        ORDER BY ah.id DESC
        LIMIT 15
    """)).fetchall()
    for r in rows:
        print(r)