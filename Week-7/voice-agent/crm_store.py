"""
crm_store.py
Task 5 — CRM Logging Store.

crm.py (Task 4) is a flat event log: every API call gets one row in
crm_interactions, no matter which client made it. That's fine as an audit
trail but it isn't a CRM — nothing links two calls from the same person,
nothing accumulates what they told you, nothing tells you who to follow up
with tomorrow.

This module is client-centric instead of event-centric:

    clients                one row per person (keyed by phone)
    call_transcripts        every call, linked to a client, storing what was
                             asked and what was answered
    appointment_history      every booking/reschedule/cancel, linked to a
                             client, so you can see a full lifecycle
    follow_up_reminders       a queue of future actions: pending manual
                             follow-ups after a failure, and scheduled
                             appointment-confirmation reminders

Run this file directly once to create the tables:
    python crm_store.py
"""

import json
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rania:mm1234@localhost:5432/realestate_agent",  # local fallback
)
engine = create_engine(DATABASE_URL)

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS clients (
    id SERIAL PRIMARY KEY,
    phone TEXT NOT NULL UNIQUE,
    name TEXT,
    preferences JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    last_contact_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS call_transcripts (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    intent TEXT NOT NULL,              -- 'query' | 'book' | 'reschedule' | 'cancel'
    raw_query TEXT,
    extracted_criteria JSONB,
    answer TEXT,
    matches_count INTEGER
);

CREATE TABLE IF NOT EXISTS appointment_history (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    event_id TEXT,
    property_title TEXT,
    employee TEXT,
    employee_email TEXT,
    date TEXT,
    start_time TEXT,
    end_time TEXT,
    status TEXT NOT NULL,              -- 'booked' | 'rescheduled' | 'cancelled' | 'failed'
    details JSONB
);

CREATE TABLE IF NOT EXISTS follow_up_reminders (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    appointment_id INTEGER REFERENCES appointment_history(id),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    remind_at TIMESTAMP NOT NULL,
    reminder_type TEXT NOT NULL,       -- 'manual_followup' | 'appointment_confirmation'
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending'  -- 'pending' | 'sent' | 'cancelled'
);

CREATE TABLE IF NOT EXISTS session_state (
    phone TEXT PRIMARY KEY,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_call_transcripts_client ON call_transcripts(client_id);
CREATE INDEX IF NOT EXISTS idx_appointment_history_client ON appointment_history(client_id);
CREATE INDEX IF NOT EXISTS idx_reminders_pending ON follow_up_reminders(status, remind_at);
"""


def ensure_tables():
    with engine.begin() as conn:
        conn.execute(text(CREATE_TABLES_SQL))
        # Migration: appointment_history existed before employee_email was
        # added to it — CREATE TABLE IF NOT EXISTS won't backfill a new
        # column onto an already-existing table, so add it explicitly.
        conn.execute(text(
            "ALTER TABLE appointment_history ADD COLUMN IF NOT EXISTS employee_email TEXT"
        ))


# ---------------------------------------------------------------
# CLIENTS — get-or-create + preference merge
# ---------------------------------------------------------------
def get_or_create_client(phone: str, name: str = None) -> int:
    """Returns the client's id, creating a row if this phone hasn't called before.
    Always bumps last_contact_at and fills in name if we didn't have one yet."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM clients WHERE phone = :phone"),
            {"phone": phone},
        ).fetchone()

        if row:
            client_id = row[0]
            conn.execute(
                text("""
                    UPDATE clients
                    SET last_contact_at = now(),
                        name = COALESCE(name, :name)
                    WHERE id = :id
                """),
                {"id": client_id, "name": name},
            )
            return client_id

        row = conn.execute(
            text("""
                INSERT INTO clients (phone, name)
                VALUES (:phone, :name)
                RETURNING id
            """),
            {"phone": phone, "name": name},
        ).fetchone()
        return row[0]


def update_client_preferences(client_id: int, new_preferences: dict) -> None:
    """Merges new_preferences into whatever the client already had, rather than
    overwriting. A budget mentioned on call 1 survives a bedrooms-only call 2.
    Null/empty values in new_preferences are dropped so they don't clobber a
    previously known value."""
    if not new_preferences:
        return

    cleaned = {
        k: v for k, v in new_preferences.items()
        if v not in (None, "", [], {}) and not k.startswith("_")
    }
    if not cleaned:
        return

    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE clients
                SET preferences = preferences || CAST(:new_prefs AS jsonb)
                WHERE id = :id
            """),
            {"id": client_id, "new_prefs": json.dumps(cleaned)},
        )


# ---------------------------------------------------------------
# CALL TRANSCRIPTS
# ---------------------------------------------------------------
def log_call_transcript(
    client_id: int,
    intent: str,
    raw_query: str = None,
    extracted_criteria: dict = None,
    answer: str = None,
    matches_count: int = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO call_transcripts
                    (client_id, intent, raw_query, extracted_criteria, answer, matches_count)
                VALUES
                    (:client_id, :intent, :raw_query, :extracted_criteria, :answer, :matches_count)
            """),
            {
                "client_id": client_id,
                "intent": intent,
                "raw_query": raw_query,
                "extracted_criteria": json.dumps(extracted_criteria) if extracted_criteria else None,
                "answer": answer,
                "matches_count": matches_count,
            },
        )


# ---------------------------------------------------------------
# APPOINTMENT HISTORY
# ---------------------------------------------------------------
def get_appointment_by_event_id(event_id: str) -> dict:
    """
    Looks up the most recent appointment_history row for a given
    event_id (most recent, since a reschedule inserts a new row rather
    than mutating the old one). Used so cancellation only has to ask the
    caller for the event_id — property/date/time/employee for the
    cancel_appointment() call can be pulled from what's already on file
    instead of making them repeat details the system already knows.
    Returns None if nothing matches (e.g. a mistyped/stale reference).
    """
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT property_title, employee, employee_email, date, start_time, end_time, status
                FROM appointment_history
                WHERE event_id = :event_id
                ORDER BY id DESC
                LIMIT 1
            """),
            {"event_id": event_id},
        ).fetchone()
        if not row:
            return None
        return {
            "property_title": row[0], "employee": row[1], "employee_email": row[2],
            "date": row[3], "start_time": row[4], "end_time": row[5], "status": row[6],
        }


def get_appointment_by_client_details(
    client_id: int,
    property_title: str = None,
    date: str = None,
    start_time: str = None,
    end_time: str = None,
) -> dict:
    """Looks up the most recent appointment for a client using human-provided
    details instead of a booking reference number.

    This lets the cancel flow work the way callers naturally speak: property,
    date, and time first; event_id only as a fallback if the lookup is still
    ambiguous.
    """
    with engine.begin() as conn:
        # BUG FIX: no status filter meant this could match a leftover
        # 'failed' attempt (booking_node/reschedule_node/cancellation_node
        # all log a row with status='failed' and event_id=NULL whenever the
        # calendar step itself fails) or an already-'cancelled' appointment.
        # Either one flows straight into cancel_appointment() with a NULL
        # event_id, which is exactly what produced the raw
        # "TypeError: Missing required parameter eventId" — this client
        # never actually had a live, active booking to resolve against.
        # get_latest_appointment_for_client() already got this right
        # (status IN ('booked','rescheduled')); this function needs the
        # same filter for the same reason.
        clauses = ["client_id = :client_id", "status IN ('booked', 'rescheduled')"]
        params = {"client_id": client_id}

        if date:
            clauses.append("date = :date")
            params["date"] = date
        if start_time:
            clauses.append("start_time = :start_time")
            params["start_time"] = start_time
        if end_time:
            clauses.append("end_time = :end_time")
            params["end_time"] = end_time

        where_clause = " AND ".join(clauses)
        rows = conn.execute(
            text(f"""
                SELECT id, event_id, property_title, employee, employee_email, date, start_time, end_time, status
                FROM appointment_history
                WHERE {where_clause}
                ORDER BY id DESC
            """),
            params,
        ).mappings().all()

        if not rows:
            return None

        def normalize(value: str) -> str:
            if not value:
                return ""
            lowered = value.lower()
            for token in ["appointment", "rental", "booking", "visit", "the", "a", "an"]:
                lowered = lowered.replace(token, " ")
            return " ".join(lowered.split())

        wanted = normalize(property_title)
        if wanted:
            wanted_tokens = {token for token in wanted.split() if len(token) > 2}

            def score(row: dict) -> tuple[int, int]:
                title = normalize(row["property_title"])
                title_tokens = set(title.split())
                overlap = len(wanted_tokens & title_tokens)
                contains = int(wanted in title or title in wanted)
                return contains + overlap, row["id"]

            best = max(rows, key=score)
            best_score, _ = score(best)
            if best_score > 0:
                return {
                    "event_id": best["event_id"],
                    "property_title": best["property_title"],
                    "employee": best["employee"],
                    "employee_email": best["employee_email"],
                    "date": best["date"],
                    "start_time": best["start_time"],
                    "end_time": best["end_time"],
                    "status": best["status"],
                }

        # BUG FIX: previously fell through to "just return rows[0]"
        # unconditionally whenever the property title didn't fuzzy-match
        # ANY row (best_score == 0 for everything) — which, for a client
        # with more than one active booking, meant a mistyped or
        # unrelated property name could silently resolve to a completely
        # different appointment and cancel/reschedule the wrong one with
        # no warning. Only auto-resolve without a real title match when
        # there's genuinely nothing to disambiguate (exactly one active
        # booking for this client) — otherwise return None so the caller
        # asks a clarifying question instead of guessing.
        if len(rows) == 1:
            best = rows[0]
            return {
                "event_id": best["event_id"],
                "property_title": best["property_title"],
                "employee": best["employee"],
                "employee_email": best["employee_email"],
                "date": best["date"],
                "start_time": best["start_time"],
                "end_time": best["end_time"],
                "status": best["status"],
            }

        return None


def get_latest_appointment_for_client(client_id: int) -> dict:
    """Returns the most recent non-failed appointment for a client.

    Used as a last-resort cancel fallback when the caller refers to
    "the one I just booked" or similar wording instead of repeating the
    booking details.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT event_id, property_title, employee, employee_email, date, start_time, end_time, status
                FROM appointment_history
                WHERE client_id = :client_id AND status IN ('booked', 'rescheduled')
                ORDER BY id DESC
                LIMIT 1
            """),
            {"client_id": client_id},
        ).mappings().fetchone()

        if not row:
            return None

        return {
            "event_id": row["event_id"],
            "property_title": row["property_title"],
            "employee": row["employee"],
            "employee_email": row["employee_email"],
            "date": row["date"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "status": row["status"],
        }


def log_appointment(
    client_id: int,
    status: str,
    event_id: str = None,
    property_title: str = None,
    employee: str = None,
    employee_email: str = None,
    date: str = None,
    start_time: str = None,
    end_time: str = None,
    details: dict = None,
) -> int:
    """Returns the new appointment_history row's id, so a reminder can be
    linked back to it."""
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO appointment_history
                    (client_id, event_id, property_title, employee, employee_email, date,
                     start_time, end_time, status, details)
                VALUES
                    (:client_id, :event_id, :property_title, :employee, :employee_email, :date,
                     :start_time, :end_time, :status, :details)
                RETURNING id
            """),
            {
                "client_id": client_id,
                "event_id": event_id,
                "property_title": property_title,
                "employee": employee,
                "employee_email": employee_email,
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "status": status,
                "details": json.dumps(details) if details is not None else None,
            },
        ).fetchone()
        return row[0]


# ---------------------------------------------------------------
# FOLLOW-UP REMINDERS
# ---------------------------------------------------------------
def create_manual_followup(client_id: int, reason: str, appointment_id: int = None, delay_hours: int = 1) -> None:
    """Failure case: something (booking/reschedule/cancel) didn't go through.
    Queues a near-term reminder for a human to call the client back."""
    remind_at = datetime.utcnow() + timedelta(hours=delay_hours)
    _insert_reminder(client_id, appointment_id, remind_at, "manual_followup", reason)


def create_appointment_reminder(client_id: int, appointment_id: int, appointment_date: str, hours_before: int = 24) -> None:
    """Success case: a booking went through. Queues a reminder ~1 day before
    the appointment so someone (or an automated call) confirms attendance.
    appointment_date is expected as 'YYYY-MM-DD'; if it's not parseable this
    just no-ops rather than crashing a booking that already succeeded."""
    try:
        appt_dt = datetime.strptime(appointment_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        print(f"[crm_store] WARNING: could not parse appointment_date={appointment_date!r}, skipping reminder")
        return

    remind_at = appt_dt - timedelta(hours=hours_before)
    _insert_reminder(client_id, appointment_id, remind_at, "appointment_confirmation", "Confirm attendance")


def _insert_reminder(client_id: int, appointment_id, remind_at: datetime, reminder_type: str, reason: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO follow_up_reminders
                    (client_id, appointment_id, remind_at, reminder_type, reason)
                VALUES
                    (:client_id, :appointment_id, :remind_at, :reminder_type, :reason)
            """),
            {
                "client_id": client_id,
                "appointment_id": appointment_id,
                "remind_at": remind_at,
                "reminder_type": reminder_type,
                "reason": reason,
            },
        )


def get_due_reminders(reminder_type: str = None) -> list[dict]:
    """What an n8n cron workflow would poll: everything pending whose
    remind_at has arrived. Marks nothing as sent — that's a separate call
    (mark_reminder_sent) so the caller controls when it's considered done."""
    query = """
        SELECT r.id, r.client_id, c.phone, c.name, r.appointment_id,
               r.reminder_type, r.reason, r.remind_at
        FROM follow_up_reminders r
        JOIN clients c ON c.id = r.client_id
        WHERE r.status = 'pending' AND r.remind_at <= now()
    """
    params = {}
    if reminder_type:
        query += " AND r.reminder_type = :reminder_type"
        params["reminder_type"] = reminder_type
    query += " ORDER BY r.remind_at ASC"

    with engine.begin() as conn:
        rows = conn.execute(text(query), params).mappings().all()
        return [dict(r) for r in rows]


def mark_reminder_sent(reminder_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE follow_up_reminders SET status = 'sent' WHERE id = :id"),
            {"id": reminder_id},
        )


# ---------------------------------------------------------------
# SESSION STATE — carries GraphState across turns of one phone call
# ---------------------------------------------------------------
# Keyed on phone rather than a generated call/session id: this project has
# no telephony layer wired in yet to hand us a call id, and phone number is
# already the natural per-caller key everything else here uses. One live
# row per phone means a caller can't have two overlapping in-progress
# conversations, which is an acceptable simplification for now — revisit
# if the voice platform gives us a real call/session id later.
SESSION_FIELDS = (
    "conversation_history", "user_profile", "property_preferences",
    "intent", "appointment_details", "missing_slots", "pending_question",
)
# NOTE: "response" is deliberately still excluded — it's per-turn scratch,
# not conversation state. Fixed bug (see agent_graph.py greeting_node /
# intent_detection_node): intent_detection_node used to read state["response"]
# to recover "what did we ask the caller last turn", but response is (a)
# never saved here and (b) unconditionally overwritten by greeting_node on
# every single turn before intent_detection_node ever runs — so the
# continuation logic was silently always broken. pending_question is the
# field that actually survives and is safe to read for that purpose.

# A real phone call won't span this long, so any session older than this
# is almost certainly an abandoned/forgotten conversation (e.g. a dev left
# a test mid-flow, or a caller hung up without finishing) rather than a
# live in-progress one. get_session_state() treats it as if it doesn't
# exist and deletes it, so the next message starts a clean conversation
# instead of resuming stale intent/missing_slots from an unrelated session.
SESSION_TTL_MINUTES = 30


def get_session_state(phone: str) -> dict:
    """Returns the saved partial GraphState for this phone, or {} if this
    is a fresh conversation (no row yet, the last one was cleared, or it's
    older than SESSION_TTL_MINUTES and treated as abandoned)."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT state, updated_at FROM session_state WHERE phone = :phone"),
            {"phone": phone},
        ).fetchone()
        if not row:
            return {}

        state, updated_at = row[0], row[1]
        if datetime.utcnow() - updated_at > timedelta(minutes=SESSION_TTL_MINUTES):
            conn.execute(
                text("DELETE FROM session_state WHERE phone = :phone"),
                {"phone": phone},
            )
            return {}

        return state


def save_session_state(phone: str, state: dict) -> None:
    """Upserts the subset of GraphState (SESSION_FIELDS) that needs to
    survive to the next turn. Only those fields are persisted — things
    like execution_trace/response/tool_outputs are per-turn scratch, not
    carried forward, to keep the row from growing unbounded."""
    to_save = {k: state[k] for k in SESSION_FIELDS if k in state}
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO session_state (phone, state, updated_at)
                VALUES (:phone, CAST(:state AS jsonb), now())
                ON CONFLICT (phone) DO UPDATE
                SET state = CAST(:state AS jsonb), updated_at = now()
            """),
            {"phone": phone, "state": json.dumps(to_save)},
        )


def clear_session_state(phone: str) -> None:
    """Called when a conversation ends (goodbye_node fires) so the next
    call from this number starts fresh instead of resuming stale state."""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM session_state WHERE phone = :phone"),
            {"phone": phone},
        )


if __name__ == "__main__":
    ensure_tables()
    print("clients, call_transcripts, appointment_history, follow_up_reminders, session_state tables ready.")