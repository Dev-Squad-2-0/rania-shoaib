"""
api_server.py
Task 4 — HTTP layer n8n actually talks to.

n8n orchestrates via HTTP Request nodes, not Python imports, so this
wraps query_agent.py and appointment_manager.py as endpoints. Every
endpoint logs to crm_interactions regardless of success/failure, so
there's always a record.

Run:
    uvicorn api_server:app --host 0.0.0.0 --port 8000

Test in the browser at http://localhost:8000/docs (FastAPI's built-in
interactive test UI) before pointing n8n at it.
"""

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

import crm
from query_agent import answer_query
from appointment_manager import book_appointment, reschedule_appointment, cancel_appointment

crm.ensure_table()

app = FastAPI(title="RealEstate Hub Voice Agent API")


# ---------------------------------------------------------------
# /query — property match + grounded answer
# ---------------------------------------------------------------
class QueryRequest(BaseModel):
    query: str
    client_name: Optional[str] = None
    client_phone: Optional[str] = None


@app.post("/query")
def query_endpoint(req: QueryRequest):
    result = answer_query(req.query)

    top_match = result["matches"][0] if result["matches"] else None
    crm.log_interaction(
        interaction_type="query",
        status="success",
        client_name=req.client_name,
        client_phone=req.client_phone,
        property_title=top_match["title"] if top_match else None,
        details={"query": req.query, "extracted_criteria": result["extracted_criteria"]},
    )
    return result


# ---------------------------------------------------------------
# /book
# ---------------------------------------------------------------
class BookRequest(BaseModel):
    client_name: str
    client_phone: str
    employee: str
    employee_email: str
    employee_name: str
    property_name: str
    date: str
    start_time: str
    end_time: str
    meeting_notes: str = ""
    requirements: str = ""


@app.post("/book")
def book_endpoint(req: BookRequest):
    result = book_appointment(**req.dict())

    crm.log_interaction(
        interaction_type="book",
        status="success" if result.get("success") else "failed",
        client_name=req.client_name,
        client_phone=req.client_phone,
        property_title=req.property_name,
        employee=req.employee_name,
        event_id=result.get("event_id"),
        date=req.date,
        start_time=req.start_time,
        end_time=req.end_time,
        details={"requirements": req.requirements, "error": result.get("error")},
    )
    return result


# ---------------------------------------------------------------
# /reschedule
# ---------------------------------------------------------------
class RescheduleRequest(BaseModel):
    event_id: str
    new_date: str
    new_start: str
    new_end: str
    employee_email: str
    employee_name: str
    client_name: str
    client_phone: str
    property_name: str
    old_date: Optional[str] = None
    old_start: Optional[str] = None
    old_end: Optional[str] = None
    meeting_notes: str = ""
    requirements: str = ""


@app.post("/reschedule")
def reschedule_endpoint(req: RescheduleRequest):
    result = reschedule_appointment(**req.dict())

    crm.log_interaction(
        interaction_type="reschedule",
        status="success" if result.get("success") else "failed",
        client_name=req.client_name,
        client_phone=req.client_phone,
        property_title=req.property_name,
        employee=req.employee_name,
        event_id=result.get("event_id", req.event_id),
        date=req.new_date,
        start_time=req.new_start,
        end_time=req.new_end,
        details={"old_date": req.old_date, "old_start": req.old_start, "error": result.get("error")},
    )
    return result


# ---------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------
class CancelRequest(BaseModel):
    event_id: str
    employee_email: str
    employee_name: str
    client_name: str
    client_phone: str
    property_name: str
    date: str
    start_time: str
    end_time: str
    reason: str = ""


@app.post("/cancel")
def cancel_endpoint(req: CancelRequest):
    result = cancel_appointment(**req.dict())

    crm.log_interaction(
        interaction_type="cancel",
        status="success" if result.get("success") else "failed",
        client_name=req.client_name,
        client_phone=req.client_phone,
        property_title=req.property_name,
        employee=req.employee_name,
        event_id=req.event_id,
        date=req.date,
        start_time=req.start_time,
        end_time=req.end_time,
        details={"reason": req.reason, "error": result.get("error")},
    )
    return result


@app.get("/health")
def health():
    return {"status": "ok"}