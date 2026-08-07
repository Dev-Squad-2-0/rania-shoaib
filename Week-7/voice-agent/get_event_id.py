from sqlalchemy import text
from crm_store import engine

with engine.begin() as conn:
    row = conn.execute(
        text("SELECT id, event_id, status, property_title, date FROM appointment_history ORDER BY id DESC LIMIT 5")
    ).fetchall()
    for r in row:
        print(r)