"""
calendar_tool.py
Week 7, Day 4, Task 1 (+ Task 4 retry/failure handling)

Provides functions for the voice agent to use as tools:

  check_availability(date, start_time, end_time) -> dict
  create_event(...) -> dict
  update_event(...) -> dict
  delete_event(event_id) -> dict

Auth: service account (google_service_account.json), calendar shared
with the service account's client_email at "Make changes to events"
permission level. Calendar ID + credentials file path read from .env.

Retry/failure handling (Task 4): the actual network calls to the
Google Calendar API (_execute_with_retry) retry up to 3 times with
exponential backoff on transient failures (dropped connection, DNS
hiccup, timeout). A permanent failure (bad credentials, invalid event
id, malformed request) is NOT retried — it fails immediately and
returns {"success": False, "error": ...} as before, so callers don't
need to change how they handle results.
"""

import os
import datetime as dt
from dotenv import load_dotenv, find_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from retry_utils import retry_with_backoff

load_dotenv(find_dotenv())

SCOPES = ["https://www.googleapis.com/auth/calendar"]
SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_service_account.json")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

TIMEZONE = "Asia/Karachi"


def _get_service():
    if not CALENDAR_ID:
        raise RuntimeError("GOOGLE_CALENDAR_ID not found — check your .env file")
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise RuntimeError(
            f"Service account file not found at '{SERVICE_ACCOUNT_FILE}' — "
            "check GOOGLE_SERVICE_ACCOUNT_FILE in .env and that the file exists."
        )
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=credentials)


@retry_with_backoff(max_attempts=3, base_delay=1.0)
def _execute_with_retry(request):
    """Every real Google API call goes through here so retry logic lives in one place."""
    return request.execute()


def check_availability(date: str, start_time: str, end_time: str) -> dict:
    """
    Returns {"available": bool, "conflicting_events": [...]} on success,
    {"available": None, "error": str} if the check itself failed even
    after retries — callers should treat available=None as "unknown,
    don't confirm a slot" rather than assuming free.
    """
    try:
        service = _get_service()
        start_dt = f"{date}T{start_time}:00"
        end_dt = f"{date}T{end_time}:00"

        request = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=f"{start_dt}+05:00",
            timeMax=f"{end_dt}+05:00",
            singleEvents=True,
            orderBy="startTime",
        )
        events_result = _execute_with_retry(request)
        events = events_result.get("items", [])
        conflicting = [e.get("summary", "(no title)") for e in events]
        return {"available": len(conflicting) == 0, "conflicting_events": conflicting}
    except Exception as e:
        return {"available": None, "conflicting_events": [], "error": str(e)}


def create_event(
    client_name: str,
    client_phone: str,
    employee: str,
    property_name: str,
    date: str,
    start_time: str,
    end_time: str,
    meeting_notes: str = "",
) -> dict:
    try:
        service = _get_service()
        start_dt = f"{date}T{start_time}:00"
        end_dt = f"{date}T{end_time}:00"

        description_lines = [
            f"Client: {client_name}",
            f"Phone: {client_phone}",
            f"Assigned Employee: {employee}",
            f"Property: {property_name}",
        ]
        if meeting_notes:
            description_lines.append(f"Notes: {meeting_notes}")

        event_body = {
            "summary": f"Property Visit — {client_name} / {property_name}",
            "description": "\n".join(description_lines),
            "start": {"dateTime": f"{start_dt}+05:00", "timeZone": TIMEZONE},
            "end": {"dateTime": f"{end_dt}+05:00", "timeZone": TIMEZONE},
        }

        request = service.events().insert(calendarId=CALENDAR_ID, body=event_body)
        created_event = _execute_with_retry(request)
        return {
            "success": True,
            "event_id": created_event.get("id"),
            "event_link": created_event.get("htmlLink"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def update_event(
    event_id: str,
    date: str,
    start_time: str,
    end_time: str,
    client_name: str = None,
    client_phone: str = None,
    employee: str = None,
    property_name: str = None,
    meeting_notes: str = None,
) -> dict:
    try:
        service = _get_service()

        get_request = service.events().get(calendarId=CALENDAR_ID, eventId=event_id)
        event = _execute_with_retry(get_request)

        start_dt = f"{date}T{start_time}:00"
        end_dt = f"{date}T{end_time}:00"
        event["start"] = {"dateTime": f"{start_dt}+05:00", "timeZone": TIMEZONE}
        event["end"] = {"dateTime": f"{end_dt}+05:00", "timeZone": TIMEZONE}

        if client_name or property_name:
            client_part = client_name or _extract_from_description(event.get("description", ""), "Client") or "Client"
            prop_part = property_name or _extract_from_description(event.get("description", ""), "Property") or "Property"
            event["summary"] = f"Property Visit — {client_part} / {prop_part}"

        description_lines = []
        for label, value, key in [
            ("Client", client_name, "Client"),
            ("Phone", client_phone, "Phone"),
            ("Assigned Employee", employee, "Assigned Employee"),
            ("Property", property_name, "Property"),
            ("Notes", meeting_notes, "Notes"),
        ]:
            final_value = value or _extract_from_description(event.get("description", ""), key)
            if final_value:
                description_lines.append(f"{label}: {final_value}")
        event["description"] = "\n".join(description_lines)

        update_request = service.events().update(calendarId=CALENDAR_ID, eventId=event_id, body=event)
        updated = _execute_with_retry(update_request)
        return {"success": True, "event_id": updated.get("id"), "event_link": updated.get("htmlLink")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_event(event_id: str) -> dict:
    try:
        service = _get_service()
        request = service.events().delete(calendarId=CALENDAR_ID, eventId=event_id)
        _execute_with_retry(request)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _extract_from_description(description: str, key: str) -> str:
    if not description:
        return ""
    for line in description.splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return ""


if __name__ == "__main__":
    print("Checking connection to Google Calendar...")
    test_date = (dt.date.today() + dt.timedelta(days=1)).isoformat()

    print(f"\n1. Checking availability on {test_date} 15:00-15:30...")
    availability = check_availability(test_date, "15:00", "15:30")
    print(f"   Available: {availability['available']}")
    if availability.get("error"):
        print(f"   Error: {availability['error']}")

    print(f"\n2. Creating a test event on {test_date} 15:00-15:30...")
    result = create_event(
        client_name="Test Client",
        client_phone="0300-1234567",
        employee="Ayesha (Test Run)",
        property_name="DHA Phase 5, Plot 24-C",
        date=test_date,
        start_time="15:00",
        end_time="15:30",
        meeting_notes="This is a test event created by calendar_tool.py — safe to delete.",
    )
    if result["success"]:
        print(f"   Event created successfully. ID: {result['event_id']}")
        print(f"   View it here: {result['event_link']}")
    else:
        print(f"   FAILED: {result['error']}")