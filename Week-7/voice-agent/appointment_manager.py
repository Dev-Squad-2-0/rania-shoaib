"""
appointment_manager.py
Week 7, Day 4, Task 3 — Appointment Management

Provides high-level functions to book, reschedule, and cancel appointments
while keeping Google Calendar and email notifications in sync.

This module orchestrates `calendar_tool` and `email_tool`.

Note: Sending real emails and updating Google Calendar requires valid
credentials in .env and the service account JSON file.
"""

from typing import Optional
import calendar_tool
import email_tool


def book_appointment(
    client_name: str,
    client_phone: str,
    employee: str,
    employee_email: str,
    employee_name: str,
    property_name: str,
    date: str,
    start_time: str,
    end_time: str,
    meeting_notes: str = "",
    requirements: str = "",
) -> dict:
    """Create a calendar event and notify the assigned employee by email."""
    cal_res = calendar_tool.create_event(
        client_name=client_name,
        client_phone=client_phone,
        employee=employee,
        property_name=property_name,
        date=date,
        start_time=start_time,
        end_time=end_time,
        meeting_notes=meeting_notes,
    )

    if not cal_res.get("success"):
        return {"success": False, "error": f"Calendar error: {cal_res.get('error')}"}

    email_res = email_tool.send_booking_notification(
        employee_email=employee_email,
        employee_name=employee_name,
        client_name=client_name,
        client_phone=client_phone,
        property_name=property_name,
        date=date,
        start_time=start_time,
        end_time=end_time,
        requirements=requirements,
    )

    return {"success": True, "event_id": cal_res.get("event_id"), "email_sent": email_res.get("success")}


def reschedule_appointment(
    event_id: str,
    new_date: str,
    new_start: str,
    new_end: str,
    employee_email: str,
    employee_name: str,
    client_name: str,
    client_phone: str,
    property_name: str,
    old_date: Optional[str] = None,
    old_start: Optional[str] = None,
    old_end: Optional[str] = None,
    meeting_notes: str = "",
    requirements: str = "",
) -> dict:
    """Update the calendar event and notify the employee about the new time."""
    cal_res = calendar_tool.update_event(
        event_id=event_id,
        date=new_date,
        start_time=new_start,
        end_time=new_end,
        client_name=client_name,
        client_phone=client_phone,
        employee=employee_name,
        property_name=property_name,
        meeting_notes=meeting_notes,
    )

    if not cal_res.get("success"):
        return {"success": False, "error": f"Calendar update error: {cal_res.get('error')}"}

    email_res = email_tool.send_reschedule_notification(
        employee_email=employee_email,
        employee_name=employee_name,
        client_name=client_name,
        client_phone=client_phone,
        property_name=property_name,
        old_date=old_date or "(unknown)",
        old_start=old_start or "(unknown)",
        old_end=old_end or "(unknown)",
        new_date=new_date,
        new_start=new_start,
        new_end=new_end,
        requirements=requirements,
    )

    return {"success": True, "event_id": cal_res.get("event_id"), "email_sent": email_res.get("success")}


def cancel_appointment(
    event_id: str,
    employee_email: str,
    employee_name: str,
    client_name: str,
    client_phone: str,
    property_name: str,
    date: str,
    start_time: str,
    end_time: str,
    reason: str = "",
) -> dict:
    """Delete the calendar event and notify the employee about the cancellation."""
    cal_res = calendar_tool.delete_event(event_id)
    if not cal_res.get("success"):
        return {"success": False, "error": f"Calendar delete error: {cal_res.get('error')}"}

    email_res = email_tool.send_cancellation_notification(
        employee_email=employee_email,
        employee_name=employee_name,
        client_name=client_name,
        client_phone=client_phone,
        property_name=property_name,
        date=date,
        start_time=start_time,
        end_time=end_time,
        reason=reason,
    )

    return {"success": True, "email_sent": email_res.get("success")}


if __name__ == "__main__":
    # Simple runnable scenario: attempt to book a test appointment and send notification.
    print("Running appointment_manager smoke scenario: booking + email notification")

    demo = book_appointment(
        client_name="Demo Client",
        client_phone="0300-5551234",
        employee="ayesha",
        employee_email="ayesha@example.com",
        employee_name="Ayesha Khan",
        property_name="Demo Property, Block A",
        date="2026-08-06",
        start_time="10:00",
        end_time="10:30",
        meeting_notes="Demo run created by appointment_manager main",
        requirements="No special requirements",
    )

    print("Result:")
    print(demo)

    if not demo.get("success"):
        print("One or more actions failed. Check .env credentials and service account permissions.")
    else:
        print("Booking attempted. If credentials are valid, calendar event created and email sent.")
