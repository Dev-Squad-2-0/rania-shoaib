"""
crm.py
Task 4 — CRM logging.

One table, one function. Every interaction the API handles (a query, a
booking, a reschedule, a cancellation) gets logged here so there's a
record n8n (or anyone) can look up later — this is the "CRM Update"
step in the Task 4 chain.

Run this file directly once to create the table:
    python crm.py
"""

from sqlalchemy import create_engine, text
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rania:mm1234@localhost:5432/realestate_agent",  # local fallback
)
engine = create_engine(DATABASE_URL)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS crm_interactions (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    interaction_type TEXT NOT NULL,   -- 'query' | 'book' | 'reschedule' | 'cancel'
    status TEXT NOT NULL,             -- 'success' | 'failed'
    client_name TEXT,
    client_phone TEXT,
    property_title TEXT,
    employee TEXT,
    event_id TEXT,
    date TEXT,
    start_time TEXT,
    end_time TEXT,
    details JSONB                     -- anything extra: error message, requirements, gaps, etc.
);
"""


def ensure_table():
    with engine.begin() as conn:
        conn.execute(text(CREATE_TABLE_SQL))


def log_interaction(
    interaction_type: str,
    status: str,
    client_name: str = None,
    client_phone: str = None,
    property_title: str = None,
    employee: str = None,
    event_id: str = None,
    date: str = None,
    start_time: str = None,
    end_time: str = None,
    details: dict = None,
) -> None:
    """Fire-and-forget insert. Never raises — a CRM logging failure should
    never take down a real booking/cancellation that already succeeded."""
    import json
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO crm_interactions
                        (interaction_type, status, client_name, client_phone,
                         property_title, employee, event_id, date, start_time,
                         end_time, details)
                    VALUES
                        (:interaction_type, :status, :client_name, :client_phone,
                         :property_title, :employee, :event_id, :date, :start_time,
                         :end_time, :details)
                """),
                {
                    "interaction_type": interaction_type,
                    "status": status,
                    "client_name": client_name,
                    "client_phone": client_phone,
                    "property_title": property_title,
                    "employee": employee,
                    "event_id": event_id,
                    "date": date,
                    "start_time": start_time,
                    "end_time": end_time,
                    "details": json.dumps(details) if details is not None else None,
                },
            )
    except Exception as e:
        # Deliberately swallowed + printed rather than raised. If Postgres
        # logging itself fails, that's a monitoring problem, not a reason
        # to fail a booking that already went through on the calendar/email side.
        print(f"[crm] WARNING: failed to log interaction: {e}")


if __name__ == "__main__":
    ensure_table()
    print("crm_interactions table ready.")