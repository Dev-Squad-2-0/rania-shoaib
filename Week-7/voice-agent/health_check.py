"""Health checks for SMTP and Google Calendar connectivity.

Run:
    python health_check.py

Prints whether SMTP login and Calendar API access succeed.
"""
import os
from dotenv import load_dotenv, find_dotenv
import smtplib
import json

load_dotenv(find_dotenv())

from google.oauth2 import service_account
from googleapiclient.discovery import build

SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD")
SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_service_account.json")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")


def check_smtp():
    if not SMTP_EMAIL or not SMTP_APP_PASSWORD:
        return {"ok": False, "error": "SMTP_EMAIL or SMTP_APP_PASSWORD missing in .env"}
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_calendar():
    if not CALENDAR_ID:
        return {"ok": False, "error": "GOOGLE_CALENDAR_ID missing in .env"}
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        return {"ok": False, "error": f"Service account file not found at {SERVICE_ACCOUNT_FILE}"}
    try:
        credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/calendar"])
        service = build("calendar", "v3", credentials=credentials)
        # Try a harmless read: list next 1 event
        events = service.events().list(calendarId=CALENDAR_ID, maxResults=1, singleEvents=True, orderBy="startTime").execute()
        return {"ok": True, "sample_events": events.get("items", [])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    print("Checking SMTP...")
    smtp_res = check_smtp()
    print(json.dumps(smtp_res, indent=2))

    print("\nChecking Google Calendar access...")
    cal_res = check_calendar()
    print(json.dumps(cal_res, indent=2))

    if smtp_res.get("ok") and cal_res.get("ok"):
        print("\nHealth check passed: SMTP and Calendar access OK.")
    else:
        print("\nHealth check failed. Fix the reported errors and try again.")


if __name__ == "__main__":
    main()
