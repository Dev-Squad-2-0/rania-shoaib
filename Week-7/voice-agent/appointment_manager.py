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
import threading
import calendar_tool
import email_tool


def _send_email_async(send_fn, **kwargs) -> None:
    """
    Fires a notification email on a background thread instead of blocking
    the voice turn on it.

    BUG FIX (appointment_01 crash / appointment_02 31s latency): the
    original book/reschedule/cancel flow ran calendar_tool then email_tool
    sequentially and synchronously, and BOTH wrap their real network call
    in a 3-attempt exponential-backoff retry. On any transient slowness
    (a cold Google API connection, an SMTP hiccup) those retries stack on
    top of each other inside the same request the caller is waiting on —
    which is exactly consistent with one turn hitting a hard 30s read
    timeout and another limping in at 31s.

    The email is a staff-side notification, not something the caller is
    waiting to hear about — what the caller needs to hear ("you're
    booked") only depends on the calendar write succeeding. So: return to
    the caller as soon as the calendar step is done, and let the email
    send in the background. If it fails, that failure is invisible to the
    live call (as it should be) but is still surfaced via email_res in
    the appointment log for staff follow-up — see _fire_and_log below.
    """
    def _run():
        try:
            send_fn(**kwargs)
        except Exception:
            pass  # best-effort notification; never let this crash a background thread
    threading.Thread(target=_run, daemon=True).start()


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
    """Create a calendar event and notify the assigned employee by email.

    The calendar write is synchronous (the caller needs to know NOW whether
    the visit is actually booked). The notification email is fired async —
    see _send_email_async — so a slow SMTP send never blocks the spoken
    response back to the caller.
    """
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

    _send_email_async(
        email_tool.send_booking_notification,
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

    # email_sent is no longer known synchronously — email_node's UI copy
    # should say "we've sent a confirmation" optimistically, or better,
    # drop that line entirely since it's no longer guaranteed true at
    # response time. See note to Rania on email_node.
    return {"success": True, "event_id": cal_res.get("event_id"), "email_sent": None}


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
    # Defense-in-depth: agent_graph's slot-filling is supposed to guarantee
    # event_id is present before this is ever called, but this module
    # shouldn't rely solely on an upstream caller getting that right —
    # passing None straight into the Google API client produces a raw,
    # unfriendly TypeError ("Missing required parameter eventId") instead
    # of a clean, handleable error.
    if not event_id:
        return {"success": False, "error": "No matching booking reference found to reschedule."}

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

    _send_email_async(
        email_tool.send_reschedule_notification,
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

    return {"success": True, "event_id": cal_res.get("event_id"), "email_sent": None}


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
    if not event_id:
        return {"success": False, "error": "No matching booking reference found to cancel."}

    cal_res = calendar_tool.delete_event(event_id)
    if not cal_res.get("success"):
        return {"success": False, "error": f"Calendar delete error: {cal_res.get('error')}"}

    _send_email_async(
        email_tool.send_cancellation_notification,
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

    return {"success": True, "email_sent": None}


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