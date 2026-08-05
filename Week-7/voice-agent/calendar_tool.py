"""
calendar_tool.py
Week 7, Day 4, Task 1 — Google Calendar Integration

Provides two functions for the voice agent to use as tools:

  check_availability(date, start_time, end_time) -> bool
      Checks the shared calendar for conflicting events in a given
      window before Ayesha confirms a slot to the caller.

  create_event(...) -> dict
      Creates a calendar event with client name, phone, employee,
      property, date, time, and meeting notes, once a slot is
      confirmed.

Auth: service account (google_service_account.json), calendar shared
with the service account's client_email at "Make changes to events"
permission level. Calendar ID + credentials file path read from .env.

Run this file directly to sanity-check the connection and create one
real test event on the shared calendar:
    python calendar_tool.py
"""

import os
import datetime as dt
from dotenv import load_dotenv, find_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv(find_dotenv())

SCOPES = ["https://www.googleapis.com/auth/calendar"]
SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_service_account.json")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# Local timezone for all event creation/availability checks.
# Change this if the business operates in a different timezone.
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


def check_availability(date: str, start_time: str, end_time: str) -> dict:
    """
    Check whether the given time window on the given date is free on
    the shared calendar.

    Args:
        date: "YYYY-MM-DD"
        start_time: "HH:MM" (24-hour)
        end_time: "HH:MM" (24-hour)

    Returns:
        {"available": bool, "conflicting_events": [event summaries]}
    """
    service = _get_service()

    start_dt = f"{date}T{start_time}:00"
    end_dt = f"{date}T{end_time}:00"

    events_result = (
        service.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=f"{start_dt}+05:00",  # Asia/Karachi is UTC+5, no DST
            timeMax=f"{end_dt}+05:00",
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = events_result.get("items", [])
    conflicting = [e.get("summary", "(no title)") for e in events]

    return {
        "available": len(conflicting) == 0,
        "conflicting_events": conflicting,
    }


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
    """
    Create a calendar event for a property visit.

    Args:
        client_name: caller's name
        client_phone: caller's phone number
        employee: name of the RealEstate Hub employee assigned to the visit
        property_name: property being visited (e.g. "DHA Phase 5, Plot 24-C")
        date: "YYYY-MM-DD"
        start_time: "HH:MM" (24-hour)
        end_time: "HH:MM" (24-hour)
        meeting_notes: any additional context (client requirements, budget, etc.)

    Returns:
        {"success": bool, "event_id": str, "event_link": str} on success,
        {"success": False, "error": str} on failure.
    """
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

    try:
        created_event = (
            service.events().insert(calendarId=CALENDAR_ID, body=event_body).execute()
        )
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
    """
    Update an existing calendar event's time and optional details.

    Returns: {"success": True, "event_id": str, "event_link": str} or
    {"success": False, "error": str}
    """
    service = _get_service()

    try:
        event = service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()

        start_dt = f"{date}T{start_time}:00"
        end_dt = f"{date}T{end_time}:00"

        # Update start/end
        event["start"] = {"dateTime": f"{start_dt}+05:00", "timeZone": TIMEZONE}
        event["end"] = {"dateTime": f"{end_dt}+05:00", "timeZone": TIMEZONE}

        # Update summary if we have identifying fields
        if client_name or property_name:
            client_part = client_name or _extract_from_description(event.get("description", ""), "Client") or "Client"
            prop_part = property_name or _extract_from_description(event.get("description", ""), "Property") or "Property"
            event["summary"] = f"Property Visit — {client_part} / {prop_part}"

        # Rebuild description preserving any existing lines we don't override
        description_lines = []
        if client_name:
            description_lines.append(f"Client: {client_name}")
        else:
            existing_client = _extract_from_description(event.get("description", ""), "Client")
            if existing_client:
                description_lines.append(f"Client: {existing_client}")

        if client_phone:
            description_lines.append(f"Phone: {client_phone}")
        else:
            existing_phone = _extract_from_description(event.get("description", ""), "Phone")
            if existing_phone:
                description_lines.append(f"Phone: {existing_phone}")

        if employee:
            description_lines.append(f"Assigned Employee: {employee}")
        else:
            existing_emp = _extract_from_description(event.get("description", ""), "Assigned Employee")
            if existing_emp:
                description_lines.append(f"Assigned Employee: {existing_emp}")

        if property_name:
            description_lines.append(f"Property: {property_name}")
        else:
            existing_prop = _extract_from_description(event.get("description", ""), "Property")
            if existing_prop:
                description_lines.append(f"Property: {existing_prop}")

        if meeting_notes:
            description_lines.append(f"Notes: {meeting_notes}")
        else:
            existing_notes = _extract_from_description(event.get("description", ""), "Notes")
            if existing_notes:
                description_lines.append(f"Notes: {existing_notes}")

        event["description"] = "\n".join(description_lines)

        updated = service.events().update(calendarId=CALENDAR_ID, eventId=event_id, body=event).execute()

        return {"success": True, "event_id": updated.get("id"), "event_link": updated.get("htmlLink")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_event(event_id: str) -> dict:
    """
    Delete an event by event_id from the shared calendar.

    Returns: {"success": True} or {"success": False, "error": str}
    """
    service = _get_service()
    try:
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _extract_from_description(description: str, key: str) -> str:
    """Helper to parse simple 'Key: value' lines from event descriptions."""
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
    if not availability["available"]:
        print(f"   Conflicting events: {availability['conflicting_events']}")

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
        print(f"   Event created successfully.")
        print(f"   Event ID: {result['event_id']}")
        print(f"   View it here: {result['event_link']}")
    else:
        print(f"   FAILED: {result['error']}")