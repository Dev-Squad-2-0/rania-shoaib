"""
retry_utils.py
Task 4 — retry/failure handling.

A single retry decorator used by calendar_tool.py and email_tool.py,
since both make real calls to external services (Google Calendar API,
Gmail SMTP) that can fail transiently — a dropped connection, a brief
Google API 503, an SMTP timeout — and shouldn't be treated the same as
a genuine, permanent failure (bad credentials, invalid event_id, etc.).

Only retries on the kind of exception that's plausibly transient.
Does NOT retry on errors that are clearly permanent (e.g. a 404 for a
deleted event, a 400 for malformed data) — retrying those just wastes
time and delays the real error reaching the caller.
"""

import time
import functools


# Exceptions worth retrying: network-level failures. Google API client
# raises these as generic Exception/HttpError subtypes depending on the
# failure, and smtplib raises its own connection-level exceptions — so
# rather than trying to enumerate every possible transient type across
# two different libraries, we retry on the broad connection/timeout
# categories and let anything else (e.g. auth errors, validation errors)
# fail immediately without wasting retry attempts on something that
# will never succeed no matter how many times it's tried.
import socket
import ssl

TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    socket.timeout,
    ssl.SSLError,
    OSError,  # covers most low-level network failures (DNS, refused, reset)
)


def retry_with_backoff(max_attempts: int = 3, base_delay: float = 1.0):
    """
    Retries the wrapped function on transient failures with exponential
    backoff (1s, 2s, 4s, ...). Re-raises the last exception if all
    attempts are exhausted, so the caller's existing try/except still
    sees a real failure and can return {"success": False, "error": ...}
    as before — this only adds retries in front of that, it doesn't
    change what a final failure looks like to the rest of the code.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except TRANSIENT_EXCEPTIONS as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        print(
                            f"[retry] {func.__name__} failed (attempt {attempt}/{max_attempts}): "
                            f"{e}. Retrying in {delay:.0f}s..."
                        )
                        time.sleep(delay)
                    else:
                        print(f"[retry] {func.__name__} failed after {max_attempts} attempts, giving up.")
            raise last_exception
        return wrapper
    return decorator