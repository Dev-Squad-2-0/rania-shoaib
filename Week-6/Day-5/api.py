"""
api.py
=======

Task 3: Wrap the LangGraph app (`graph.py`) behind a FastAPI chat
endpoint, with structured logging as the foundation for Task 4's
monitoring plan.

## Why a thin wrapper, not a rewrite

`run_turn()` in graph.py is already the single entry point the test
suites (`test_eval_suite.py`, `test_e2e.py`) call directly. This file
does not duplicate any of that logic -- it only adds the two things a
deployed API needs that a Python test script doesn't:

1. Session persistence across HTTP requests (the graph itself is
   stateless per call; chat_history has to live somewhere between
   requests, since each request is a fresh process-level call).
2. Structured, queryable logging per turn (query, intent, tools called,
   latency, status) -- exactly the fields Task 4's monitoring checklist
   says to track, emitted here rather than invented separately later.

## Session storage

In-memory dict keyed by conversation_id. This is intentionally the
simplest thing that works for a demo/capstone deployment: it resets on
process restart and doesn't scale across multiple worker processes.
That's a real limitation, called out again in the monitoring doc -- a
production deployment would swap this for Redis/a DB-backed session
store, but the *shape* of what's stored (a list of {"role","content"}
turns, exactly what GraphState already expects) would not change, so
swapping the backing store later is a small change, not a redesign.

## Why fields are returned the way they are

`tool_result` is passed through as-is (not reshaped) so a UI can render
whatever the underlying tool actually returned (probability + grounding
factors for predictions, raw stat rows for retrieval) without this file
needing its own copy of that schema that could drift from tools.py's.
"""

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from graph import run_turn

# ---------------------------------------------------------------------------
# Structured logging setup (Task 3's "foundation for monitoring" ask).
# One JSON object per line, per turn -- easy to grep, easy to ship to a
# log aggregator later without reformatting. Deliberately NOT using the
# request/response bodies directly as the log shape, since API contracts
# and log schemas evolve for different reasons and coupling them makes
# both harder to change independently.
# ---------------------------------------------------------------------------
logger = logging.getLogger("afl_assistant")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)


def log_turn(*, conversation_id: str, query: str, result: dict,
             latency_ms: float, error: Optional[str] = None) -> None:
    """Emits exactly the fields Task 4's monitoring checklist tracks:
    latency, tool errors, intent distribution (for off-topic leak rate),
    and enough identifying info to trace back to a specific turn.
    token_usage is a placeholder (None) since this router is regex-based,
    not an LLM call -- the field stays in the schema now so switching
    router.py to a real LLM gateway call later (already anticipated in
    router.py's own docstring) doesn't require a logging schema change,
    only populating a field that already exists."""
    record = {
        "ts": time.time(),
        "conversation_id": conversation_id,
        "query": query,
        "intent": result.get("intent"),
        "sub_type": result.get("sub_type"),
        "router_confidence": result.get("router_confidence"),
        "tool_name": result.get("tool_name"),
        "validation_status": (result.get("validation") or {}).get("status"),
        "resolution_issues": bool(result.get("_resolution_issues")),
        "latency_ms": round(latency_ms, 1),
        "token_usage": None,
        "error": error,
    }
    logger.info(json.dumps(record))


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------
_SESSIONS: Dict[str, List[Dict[str, str]]] = {}
_MAX_HISTORY_TURNS = 20  # keep the last N messages; history growth is O(1)
                         # work per turn for resolver.py's history scans,
                         # but unbounded growth would still be a slow leak
                         # across a very long-lived conversation_id.


def _get_history(conversation_id: str) -> List[Dict[str, str]]:
    return _SESSIONS.setdefault(conversation_id, [])


def _append_turn(conversation_id: str, query: str, response: str) -> None:
    history = _get_history(conversation_id)
    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": response})
    if len(history) > _MAX_HISTORY_TURNS:
        del history[: len(history) - _MAX_HISTORY_TURNS]


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's message.")
    conversation_id: Optional[str] = Field(
        None,
        description="Groups turns into one conversation. Omit to start a "
                    "new conversation (a fresh id is generated and returned).",
    )


class PredictionMetadata(BaseModel):
    """Only populated when intent/sub_type is a prediction; None for
    retrieval/factual/off_topic turns, rather than an empty dict, so a UI
    can check `if prediction_metadata` directly."""
    winner: Optional[str] = None
    probability: Optional[float] = None
    grounding: Optional[List[Dict[str, Any]]] = None
    caveat: Optional[str] = None


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    intent: Optional[str]
    sub_type: Optional[str]
    tool_name: Optional[str]
    validation_status: Optional[str]
    prediction_metadata: Optional[PredictionMetadata]
    latency_ms: float


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AFL Assistant API",
    description="Domain-locked AFL chat + prediction assistant (LangGraph-backed).",
    version="1.0.0",
)

# CORS open by default so the bundled demo UI (served from a different
# origin/port during local dev) can call this without a proxy. Tighten
# to specific origins before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """Liveness check -- Task 4's monitoring checklist assumes something
    is polling this on a schedule."""
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    conversation_id = req.conversation_id or str(uuid.uuid4())
    history = list(_get_history(conversation_id))  # copy: run_turn must not
                                                    # mutate the stored list
                                                    # out from under us if a
                                                    # node error partially
                                                    # modifies it.

    start = time.perf_counter()
    error = None
    try:
        result = run_turn(req.message, session_id=conversation_id, chat_history=history)
    except Exception as exc:  # noqa: BLE001 -- last-resort catch; safe_node
        # already wraps every graph node, so reaching here means something
        # broke outside the graph itself (e.g. run_turn's own setup).
        error = str(exc)
        latency_ms = (time.perf_counter() - start) * 1000
        log_turn(conversation_id=conversation_id, query=req.message,
                  result={}, latency_ms=latency_ms, error=error)
        raise HTTPException(status_code=500, detail="Internal error processing request.") from exc

    latency_ms = (time.perf_counter() - start) * 1000
    response_text = result.get("final_response") or "Sorry, I couldn't produce a response for that."

    _append_turn(conversation_id, req.message, response_text)
    log_turn(conversation_id=conversation_id, query=req.message,
              result=result, latency_ms=latency_ms)

    tool_result = result.get("tool_result") or {}
    prediction_metadata = None
    if result.get("intent") == "prediction" and tool_result:
        prediction_metadata = PredictionMetadata(
            winner=tool_result.get("winner"),
            probability=tool_result.get("probability"),
            grounding=tool_result.get("grounding"),
            caveat=tool_result.get("caveat"),
        )

    return ChatResponse(
        conversation_id=conversation_id,
        response=response_text,
        intent=result.get("intent"),
        sub_type=result.get("sub_type"),
        tool_name=result.get("tool_name"),
        validation_status=(result.get("validation") or {}).get("status"),
        prediction_metadata=prediction_metadata,
        latency_ms=round(latency_ms, 1),
    )


@app.delete("/chat/{conversation_id}")
def reset_conversation(conversation_id: str) -> dict:
    """Clears a conversation's stored history -- useful for the demo UI's
    'new chat' button and for test isolation."""
    _SESSIONS.pop(conversation_id, None)
    return {"conversation_id": conversation_id, "cleared": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
