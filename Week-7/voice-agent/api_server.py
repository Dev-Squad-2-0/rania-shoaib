"""
api_server.py
Task 4 — HTTP layer n8n actually talks to.
Task 5 — now also writes into the client-centric CRM store (crm_store.py):
    clients / call_transcripts / appointment_history / follow_up_reminders

n8n orchestrates via HTTP Request nodes, not Python imports, so this
wraps query_agent.py and appointment_manager.py as endpoints. Every
endpoint still logs to crm_interactions (crm.py, Task 4's flat event
log) regardless of success/failure. On top of that, every endpoint now
also resolves the caller to a client record and writes the appropriate
Task 5 rows: a transcript on every call, an appointment_history row on
every book/reschedule/cancel, and a follow_up_reminders row either way
(manual follow-up on failure, confirmation reminder on a successful
booking).

Run:
    uvicorn api_server:app --host 0.0.0.0 --port 8000

Test in the browser at http://localhost:8000/docs (FastAPI's built-in
interactive test UI) before pointing n8n at it.
"""

import os
import base64
import time
import asyncio
import logging
import json as _json
from fastapi import FastAPI, UploadFile, File, Form, Header, Response, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional

import crm
import crm_store
import stt_service
import tts_service
from query_agent import answer_query
from appointment_manager import book_appointment, reschedule_appointment, cancel_appointment
from agent_graph import build_graph

# ---------------------------------------------------------------
# STRUCTURED LOGGING (Day 6 Task 4 — Monitoring)
# JSON lines to stdout so docker logs / log aggregators (Datadog,
# CloudWatch, Loki) can parse without a custom format string.
# ---------------------------------------------------------------
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        extra = {}
        for k, v in record.__dict__.items():
            if k not in logging.LogRecord.__dict__ and not k.startswith("_"):
                try:
                    _json.dumps(v)
                    extra[k] = v
                except (TypeError, ValueError):
                    extra[k] = str(v)
        base.update(extra)
        return _json.dumps(base, ensure_ascii=False)

_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), handlers=[_handler])
log = logging.getLogger("ayesha")

crm.ensure_table()
crm_store.ensure_tables()

app = FastAPI(title="RealEstate Hub Voice Agent API")
graph_app = build_graph()


# ---------------------------------------------------------------
# REQUEST LOGGING MIDDLEWARE
# Logs every request: method, path, status, latency. Keeps the
# per-endpoint code clean — monitoring data comes from here, not
# scattered print() calls.
# ---------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    log.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": latency_ms,
        },
    )
    return response


# ---------------------------------------------------------------
# HEALTH CHECK (Day 6 Task 5 — Deployment Readiness)
# Docker HEALTHCHECK and load balancers hit this. Checks that the
# DB is reachable and the graph is loaded — not just "process is up".
# ---------------------------------------------------------------
@app.get("/health")
def health_check():
    checks = {}
    # DB
    try:
        import crm_store as _cs
        _cs.get_session_state("__healthcheck__")
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {exc}"
    # Graph
    checks["graph"] = "ok" if graph_app else "not loaded"
    # ChromaDB
    try:
        from retriever import chroma_client
        chroma_client.heartbeat()
        checks["chromadb"] = "ok"
    except Exception as exc:
        checks["chromadb"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": "ok" if all_ok else "degraded", "checks": checks},
                        status_code=status_code)


@app.get("/", response_class=HTMLResponse)
def read_root():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        return FileResponse(template_path)
    return "<h1>RealEstate Hub Voice Agent API</h1><p>Visit <a href='/docs'>/docs</a> for API interface.</p>"



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

    # BUG FIX: matches[0] can now be a {"no_coverage": True, ...} sentinel
    # (see recommend.py) instead of a real property row, which has no
    # "title" key — use .get() so this doesn't crash on the exact case
    # it's supposed to be reporting on.
    raw_top = result["matches"][0] if result["matches"] else None
    top_match = raw_top if (raw_top and not raw_top.get("no_coverage")) else None
    crm.log_interaction(
        interaction_type="query",
        status="success",
        client_name=req.client_name,
        client_phone=req.client_phone,
        property_title=top_match["title"] if top_match else None,
        details={"query": req.query, "extracted_criteria": result["extracted_criteria"]},
    )

    # Task 5: resolve/create the client, fold in anything new we learned
    # about their preferences, and log this call as a transcript.
    if req.client_phone:
        client_id = crm_store.get_or_create_client(req.client_phone, req.client_name)
        crm_store.update_client_preferences(client_id, result["extracted_criteria"])
        crm_store.log_call_transcript(
            client_id=client_id,
            intent="query",
            raw_query=req.query,
            extracted_criteria=result["extracted_criteria"],
            answer=result["answer"],
            matches_count=len(result["matches"]),
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
    success = bool(result.get("success"))

    crm.log_interaction(
        interaction_type="book",
        status="success" if success else "failed",
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

    # Task 5: client + appointment_history + the appropriate reminder
    client_id = crm_store.get_or_create_client(req.client_phone, req.client_name)
    appt_id = crm_store.log_appointment(
        client_id=client_id,
        status="booked" if success else "failed",
        event_id=result.get("event_id"),
        property_title=req.property_name,
        employee=req.employee_name,
        date=req.date,
        start_time=req.start_time,
        end_time=req.end_time,
        details={"requirements": req.requirements, "error": result.get("error")},
    )
    if success:
        crm_store.create_appointment_reminder(client_id, appt_id, req.date)
    else:
        crm_store.create_manual_followup(
            client_id, reason=f"Booking failed: {result.get('error')}", appointment_id=appt_id
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
    success = bool(result.get("success"))

    crm.log_interaction(
        interaction_type="reschedule",
        status="success" if success else "failed",
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

    client_id = crm_store.get_or_create_client(req.client_phone, req.client_name)
    appt_id = crm_store.log_appointment(
        client_id=client_id,
        status="rescheduled" if success else "failed",
        event_id=result.get("event_id", req.event_id),
        property_title=req.property_name,
        employee=req.employee_name,
        date=req.new_date,
        start_time=req.new_start,
        end_time=req.new_end,
        details={"old_date": req.old_date, "old_start": req.old_start, "error": result.get("error")},
    )
    if success:
        crm_store.create_appointment_reminder(client_id, appt_id, req.new_date)
    else:
        crm_store.create_manual_followup(
            client_id, reason=f"Reschedule failed: {result.get('error')}", appointment_id=appt_id
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
    success = bool(result.get("success"))

    crm.log_interaction(
        interaction_type="cancel",
        status="success" if success else "failed",
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

    client_id = crm_store.get_or_create_client(req.client_phone, req.client_name)
    appt_id = crm_store.log_appointment(
        client_id=client_id,
        status="cancelled" if success else "failed",
        event_id=req.event_id,
        property_title=req.property_name,
        employee=req.employee_name,
        date=req.date,
        start_time=req.start_time,
        end_time=req.end_time,
        details={"reason": req.reason, "error": result.get("error")},
    )
    # A cancellation never gets an appointment reminder (nothing to confirm).
    # A failed cancellation still needs a human to follow up though.
    if not success:
        crm_store.create_manual_followup(
            client_id, reason=f"Cancellation failed: {result.get('error')}", appointment_id=appt_id
        )

    return result


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------
# /converse — the actual entry point for the voice agent.
#
# This is the endpoint n8n should eventually call instead of fanning out
# to /query /book /reschedule /cancel: it runs the real LangGraph
# (agent_graph.py), which does intent detection, slot-filling,
# availability-checking, and CRM/email logging internally, instead of
# n8n's Switch node routing to a bare tool call with no validation.
#
# Session continuity: GraphState fields that need to survive between
# turns of the same phone call (conversation_history, user_profile,
# property_preferences, intent, appointment_details, missing_slots) are
# persisted in crm_store.session_state, keyed by phone. Every request
# loads whatever's there, merges this turn's message in, invokes the
# graph, and saves the result back — so a clarifying question asked in
# turn N ("what date works for you?") gets its answer correctly folded
# into appointment_details in turn N+1, instead of resetting each call.
# ---------------------------------------------------------------
class ConverseRequest(BaseModel):
    client_phone: str
    client_name: Optional[str] = None
    message: str


class ConverseResponse(BaseModel):
    response: str
    intent: Optional[str] = None
    missing_slots: list = []
    conversation_ended: bool = False


class ResetSessionRequest(BaseModel):
    client_phone: str


@app.post("/converse/reset")
def reset_session_endpoint(req: ResetSessionRequest):
    """
    Manually clears session_state for a phone number. Session state
    already auto-expires after crm_store.SESSION_TTL_MINUTES, and a
    successful book/reschedule/cancel clears it automatically too — this
    endpoint is for testing, so you can start a clean conversation with
    the same test phone number without waiting out the TTL or digging
    into the database directly.
    """
    crm_store.clear_session_state(req.client_phone)
    return {"status": "cleared", "client_phone": req.client_phone}


@app.post("/converse", response_model=ConverseResponse)
def converse_endpoint(req: ConverseRequest):
    saved = crm_store.get_session_state(req.client_phone)

    state = {
        "conversation_history": saved.get("conversation_history", []),
        "current_message": req.message,
        "user_profile": saved.get("user_profile") or {
            "phone": req.client_phone, "name": req.client_name,
        },
        "property_preferences": saved.get("property_preferences", {}),
        "intent": saved.get("intent"),
        "appointment_details": saved.get("appointment_details", {}),
        "missing_slots": saved.get("missing_slots", []),
        # BUG FIX: this is the field intent_detection_node relies on to know
        # "what did we just ask the caller" across turns. It's persisted via
        # crm_store.SESSION_FIELDS (see crm_store.py), so restore it here the
        # same way every other cross-turn field is restored.
        "pending_question": saved.get("pending_question"),
        "tool_outputs": {},
    }

    result = graph_app.invoke(state)

    # BUG FIX: conversation_history was being read everywhere (recommendation_
    # node passes it to generate_grounded_answer for multi-turn phrasing
    # context) but no node ever wrote to it, so it stayed permanently empty.
    # Append this turn's exchange here, once, after the graph has produced
    # its final response, then persist it like every other session field.
    history = list(state["conversation_history"])
    history.append({"role": "user", "content": req.message})
    if result.get("response"):
        history.append({"role": "assistant", "content": result["response"]})
    result["conversation_history"] = history

    if result.get("conversation_ended"):
        crm_store.clear_session_state(req.client_phone)
    else:
        crm_store.save_session_state(req.client_phone, result)

    return ConverseResponse(
        response=result.get("response", ""),
        intent=result.get("intent"),
        missing_slots=result.get("missing_slots", []),
        conversation_ended=bool(result.get("conversation_ended")),
    )


# ---------------------------------------------------------------
# /voice_converse — End-to-end Voice Entry Point (STT -> LLM Graph -> TTS)
# ---------------------------------------------------------------
class VoiceConverseRequest(BaseModel):
    client_phone: str
    client_name: Optional[str] = None
    audio_base64: str  # Base64 encoded input audio
    content_type: str = "audio/m4a"


class VoiceConverseResponse(BaseModel):
    transcript: str
    response_text: str
    intent: Optional[str] = None
    missing_slots: list = []
    conversation_ended: bool = False
    audio_base64: Optional[str] = None  # Base64 encoded output MP3
    stt_latency_ms: float = 0.0
    tts_latency_ms: float = 0.0


@app.post("/voice_converse", response_model=VoiceConverseResponse)
def voice_converse_endpoint(req: VoiceConverseRequest):
    # 1. Decode audio base64 payload
    try:
        audio_bytes = base64.b64decode(req.audio_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 audio string: {e}")

    # 2. STT via Deepgram
    try:
        stt_result = stt_service.transcribe_audio(audio_bytes, content_type=req.content_type)
        transcript = stt_result.get("transcript", "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"STT processing failed: {e}")

    if not transcript:
        transcript = "[Silence / Unclear Audio]"
        response_text = "Main aap ki baat samajh nahi saki, kya aap dobara keh sakte hain?"
        # Synthesize fallback TTS
        tts_result = tts_service.synthesize_speech(response_text)
        return VoiceConverseResponse(
            transcript=transcript,
            response_text=response_text,
            audio_base64=base64.b64encode(tts_result["audio_bytes"]).decode("utf-8"),
            stt_latency_ms=stt_result.get("processing_time_ms", 0.0),
            tts_latency_ms=tts_result.get("total_ms", 0.0),
        )

    # 3. LangGraph conversation turn
    converse_req = ConverseRequest(
        client_phone=req.client_phone,
        client_name=req.client_name,
        message=transcript,
    )
    conv_resp = converse_endpoint(converse_req)

    # 4. TTS via Fish Audio
    try:
        tts_result = tts_service.synthesize_speech(conv_resp.response)
        audio_out_b64 = base64.b64encode(tts_result["audio_bytes"]).decode("utf-8")
    except Exception as e:
        print(f"[api_server] TTS synthesis warning: {e}")
        audio_out_b64 = None
        tts_result = {"total_ms": 0.0}

    return VoiceConverseResponse(
        transcript=transcript,
        response_text=conv_resp.response,
        intent=conv_resp.intent,
        missing_slots=conv_resp.missing_slots,
        conversation_ended=conv_resp.conversation_ended,
        audio_base64=audio_out_b64,
        stt_latency_ms=stt_result.get("processing_time_ms", 0.0),
        tts_latency_ms=tts_result.get("total_ms", 0.0),
    )


@app.post("/voice_converse_file")
async def voice_converse_file_endpoint(
    client_phone: str = Form(...),
    client_name: Optional[str] = Form(None),
    file: UploadFile = File(...)
):
    """Multipart file upload endpoint for testing directly with audio files."""
    audio_bytes = await file.read()
    stt_result = stt_service.transcribe_audio(audio_bytes, content_type=file.content_type or "audio/m4a")
    transcript = stt_result.get("transcript", "")

    conv_resp = converse_endpoint(
        ConverseRequest(client_phone=client_phone, client_name=client_name, message=transcript)
    )

    tts_result = tts_service.synthesize_speech(conv_resp.response)

    return Response(
        content=tts_result["audio_bytes"],
        media_type="audio/mpeg",
        headers={
            "X-Transcript": transcript,
            "X-Response-Text": conv_resp.response,
            "X-Intent": str(conv_resp.intent),
            "X-Conversation-Ended": str(conv_resp.conversation_ended),
        }
    )

# ---------------------------------------------------------------
# /chat/completions — OpenAI Compatible Endpoint for Vapi
# Streams immediately so Vapi never times out waiting for the
# first byte, even when booking/reschedule/cancel take 30+ s.
# The graph runs in a thread executor; SSE keepalive comments
# are emitted every second so Vapi's timer keeps resetting.
# ---------------------------------------------------------------
@app.post("/chat/completions")
async def chat_completions_endpoint(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    user_messages = [m for m in messages if m.get("role") == "user"]
    latest_user_message = user_messages[-1].get("content", "") if user_messages else ""
    if not latest_user_message:
        latest_user_message = "Hello"

    call_id = request.headers.get("x-vapi-call-id", "vapi_default_session")
    print(f"[chat/completions] Handling request for {call_id}: {latest_user_message}")

    converse_req = ConverseRequest(
        client_phone=call_id,
        client_name="Vapi Caller",
        message=latest_user_message,
    )

    is_stream = body.get("stream", False)

    if is_stream:
        from fastapi.responses import StreamingResponse
        import json
        import concurrent.futures

        async def event_generator():
            # Run the blocking graph in a thread so this coroutine
            # can yield SSE keepalive comments immediately, preventing
            # Vapi from timing out during long calendar/email operations.
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(None, converse_endpoint, converse_req)

            # Emit SSE keepalive comments every second while we wait.
            # Vapi ignores ": keepalive" lines but they reset its idle timer.
            while not future.done():
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

            try:
                conv_resp = await future
                response_text = conv_resp.response
            except Exception as e:
                print(f"[chat/completions] ERROR: {e}")
                response_text = f"Maafi, meri taraf se ek masla aa gaya: {type(e).__name__}. Thori der baad dobara try karein."
                print(f"[chat/completions] GRAPH ERROR DETAIL: {e}")

            print(f"[chat/completions] Graph response: {response_text}")

            # Stream the response word-by-word
            words = response_text.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                delta = {"role": "assistant", "content": chunk} if i == 0 else {"content": chunk}
                data = {
                    "id": f"chatcmpl-{call_id}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": body.get("model", "voice-agent"),
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                }
                yield f"data: {json.dumps(data)}\n\n"
                await asyncio.sleep(0.01)

            # Final stop chunk
            final_data = {
                "id": f"chatcmpl-{call_id}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": body.get("model", "voice-agent"),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final_data)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # Non-streaming fallback
    try:
        conv_resp = converse_endpoint(converse_req)
        response_text = conv_resp.response
    except Exception as e:
        print(f"[chat/completions] ERROR (non-stream): {type(e).__name__}: {e}")
        response_text = "Maafi, meri taraf se ek masla aa gaya. Thori der baad dobara try karein."

    print(f"[chat/completions] Graph response: {response_text}")

    return {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "voice-agent"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }