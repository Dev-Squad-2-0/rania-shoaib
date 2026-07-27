"""
FastAPI wrapper. Two endpoints:
  POST /background-check            -- submit a candidate, get back either
                                         an immediate rejection or a pending
                                         report awaiting human review
  POST /background-check/{id}/decision -- human reviewer approves/rejects/
                                         asks for more info; returns final status

Run with:
    uvicorn main:app --reload
"""

import logging
import os
import uuid
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langgraph.types import Command

from graph import build_graph

load_dotenv()

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("traqcheck")

app = FastAPI(title="TraqCheck-Lite")
graph = build_graph()


class CandidateSubmission(BaseModel):
    name: str
    claimed_company: str
    claimed_title: str
    claimed_institution: str
    claimed_degree: str
    consent_given: bool


class ReviewDecision(BaseModel):
    decision: Literal["approve", "reject", "request_more_info"]


@app.post("/background-check")
def submit_background_check(candidate: CandidateSubmission):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    logger.info(f"New submission [{thread_id}]: name='{candidate.name}', "
                f"consent={candidate.consent_given}")

    result = graph.invoke(candidate.model_dump(), config=config)

    if result.get("error"):
        return {
            "thread_id": thread_id,
            "status": result.get("final_status", "error"),
            "detail": result["error"],
        }

    # Graph paused at the human_review interrupt -- fetch its payload from state
    state = graph.get_state(config)
    proposed_report = None
    if state.tasks and state.tasks[0].interrupts:
        proposed_report = state.tasks[0].interrupts[0].value.get("report")

    return {
        "thread_id": thread_id,
        "status": "pending_human_review",
        "proposed_report": proposed_report,
    }


@app.post("/background-check/{thread_id}/decision")
def submit_decision(thread_id: str, decision: ReviewDecision):
    config = {"configurable": {"thread_id": thread_id}}
    try:
        result = graph.invoke(Command(resume={"decision": decision.decision}), config=config)
    except Exception as exc:
        logger.error(f"Failed to resume thread {thread_id}: {exc}")
        raise HTTPException(status_code=404, detail=f"No pending review found for thread_id={thread_id}")

    logger.info(f"Decision recorded [{thread_id}]: {decision.decision} -> {result.get('final_status')}")
    return {"thread_id": thread_id, "final_status": result.get("final_status")}


@app.get("/health")
def health():
    return {"status": "ok"}