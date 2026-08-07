"""Run a real booking using appointment_manager and show diagnostics.

Usage (from project root):
    python run_test_booking.py --employee-email ayesha@example.com --employee-name Ayesha

Reads credentials from .env (SMTP and Google). Requires real credentials.
"""
import argparse
from dotenv import load_dotenv, find_dotenv
import os
import json

load_dotenv(find_dotenv())

from appointment_manager import book_appointment


def main():
    parser = argparse.ArgumentParser(description="Run a test booking and print results")
    parser.add_argument("--client-name", default=os.environ.get("TEST_CLIENT_NAME", "Test Client"))
    parser.add_argument("--client-phone", default=os.environ.get("TEST_CLIENT_PHONE", "0300-1234567"))
    parser.add_argument("--employee", default=os.environ.get("TEST_EMPLOYEE", "Ayesha"))
    parser.add_argument("--employee-email", required=True)
    parser.add_argument("--employee-name", default=os.environ.get("TEST_EMPLOYEE_NAME", "Ayesha"))
    parser.add_argument("--property-name", default=os.environ.get("TEST_PROPERTY", "DHA Phase 5, Plot 24-C"))
    parser.add_argument("--date", default=os.environ.get("TEST_DATE", "2026-08-10"))
    parser.add_argument("--start-time", default=os.environ.get("TEST_START", "15:00"))
    parser.add_argument("--end-time", default=os.environ.get("TEST_END", "15:30"))
    parser.add_argument("--meeting-notes", default="Test booking from run_test_booking.py")
    parser.add_argument("--requirements", default="")

    args = parser.parse_args()

    print("Running booking with the following parameters:")
    print(json.dumps(vars(args), indent=2))

    # Call orchestrator
    res = book_appointment(
        client_name=args.client_name,
        client_phone=args.client_phone,
        employee=args.employee,
        employee_email=args.employee_email,
        employee_name=args.employee_name,
        property_name=args.property_name,
        date=args.date,
        start_time=args.start_time,
        end_time=args.end_time,
        meeting_notes=args.meeting_notes,
        requirements=args.requirements,
    )

    print("\nResult:")
    print(json.dumps(res, indent=2))

    if not res.get("success"):
        print("\nBooking failed. Check errors above and verify .env credentials and calendar permissions.")
    else:
        print("\nBooking succeeded. Verify the calendar and employee inbox for the event/email.")


if __name__ == "__main__":
    main()
