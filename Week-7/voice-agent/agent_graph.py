"""
agent_graph.py
Week 7, Day 5, Task 2 — Graph Design
Week 7, Day 5, Task 3 — Tool Integration (Search Property, Calendar,
    Email, CRM, Availability Checker, RAG Search — all wrapped as nodes)
Week 7, Day 5, Task 4 — Validation (slot_filling_node + availability_check_node)
Week 7, Day 5, Task 5 — State Logging (@log_transition decorator)

Wires graph_state.GraphState through nodes that wrap the existing,
already-tested pipeline: query_agent, recommend, retriever,
appointment_manager, calendar_tool, crm_store, employee_data. No tool
logic is reimplemented here — every node is a thin adapter that reads
state in, calls a real function, writes state out.

Graph shape:

    START -> greeting -> intent_detection -> (conditional) -> [one of]
        rag_node -> END
        recommendation_node -> END
        slot_filling -> (missing?) -> END (clarifying question)
                      -> availability_check (book/reschedule only)
                            -> (unavailable?) -> END (clarifying question)
                            -> booking_node / reschedule_node -> email_node -> END
                      -> cancellation_node -> email_node -> END (cancel skips availability check)
        goodbye_node -> END

    Task 4 validation lives in slot_filling_node and availability_check_node:
    neither booking_node, reschedule_node, nor cancellation_node can be
    reached with incomplete appointment_details or an unconfirmed slot —
    both gate nodes route back to END with a clarifying question instead.

    Task 5 logging: every node is wrapped in @log_transition(name), which
    appends {"node", "timestamp", "intent"} to state["execution_trace"] —
    inspect result["execution_trace"] after invoking the graph for a full
    annotated record of exactly which nodes ran, in order.
"""

from langgraph.graph import StateGraph, END
from openai import OpenAI
import os
import json
import datetime

from graph_state import GraphState
from query_agent import extract_criteria, generate_grounded_answer
from recommend import recommend_properties
from retriever import unified_retrieve
import appointment_manager
import calendar_tool
import crm_store
import employee_data

GATEWAY_BASE_URL = "https://llm.netixsol.com/v1"
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY")
GATEWAY_MODEL = "smart"

client = OpenAI(base_url=GATEWAY_BASE_URL, api_key=GATEWAY_API_KEY)

# Task 4: which appointment_details fields each intent needs before it's
# safe to call appointment_manager. Anything missing triggers a
# clarification turn instead of calling the tool with a None.
REQUIRED_SLOTS = {
    "book": ["property_name", "date", "start_time", "end_time"],
    "reschedule": ["event_id", "new_date", "new_start", "new_end"],
    "cancel": ["event_id", "date", "start_time", "end_time"],
}


# ---------------------------------------------------------------
# TASK 5 — STATE LOGGING
# ---------------------------------------------------------------
def log_transition(node_name):
    """
    Decorator wrapping every node function. Appends one entry to
    state["execution_trace"] per node executed, so a full multi-turn
    conversation accumulates an annotated record of exactly which nodes
    ran, in what order, with what intent — independent of any print
    statement, so it survives being inspected after the fact (e.g. for
    debugging a bad routing decision).
    """
    def decorator(fn):
        def wrapped(state: GraphState) -> dict:
            trace_entry = {
                "node": node_name,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "intent": state.get("intent"),
            }
            result = fn(state)
            trace = list(state.get("execution_trace", []))
            trace.append(trace_entry)
            result["execution_trace"] = trace
            return result
        wrapped.__name__ = fn.__name__
        return wrapped
    return decorator


# ---------------------------------------------------------------
# NODES
# ---------------------------------------------------------------
@log_transition("greeting")
def greeting_node(state: GraphState) -> dict:
    """
    Runs once at the start of a conversation. Resolves the caller to a
    real client record via crm_store (reusing Task 5's dedup-on-phone
    logic) so every later node has user_profile available without
    re-deriving it.
    """
    profile = state.get("user_profile", {}) or {}
    phone = profile.get("phone")
    name = profile.get("name")

    if phone:
        client_id = crm_store.get_or_create_client(phone, name)
        profile = {"client_id": client_id, "name": name, "phone": phone}

    return {
        "user_profile": profile,
        "response": "Hi, thanks for calling. How can I help you today?",
    }


INTENT_SYSTEM_PROMPT = """Classify the customer's message into exactly one intent.
Respond with ONLY the single word, no other text:
greeting, query, book, reschedule, cancel, goodbye

- "query" = asking about properties, prices, availability, general questions
- "book" = wants to schedule a new visit/appointment
- "reschedule" = wants to change an existing appointment's time
- "cancel" = wants to cancel an existing appointment
- "goodbye" = ending the conversation, thanks, that's all
- "greeting" = only if this is a pure hello with no other content
"""


@log_transition("intent_detection")
def intent_detection_node(state: GraphState) -> dict:
    """
    Replaces what n8n's Switch node did in Task 4 — but now inside the
    agent itself, and now with an LLM classifying free text instead of
    n8n matching an already-structured intent field. This is the actual
    "true AI agent" upgrade: n8n needed the caller to already say the
    word "book"; this can infer intent from natural phrasing.

    If a previous turn already established an intent and is still
    waiting on missing_slots, this turn is treated as the answer to that
    pending question rather than re-classified from scratch. Without
    this, a reply like a bare event_id or "2pm works" has no words that
    look like "cancel"/"book" on their own, so the classifier would
    default to "query" and derail an in-progress conversation — this bit
    the reschedule/cancel flow directly during testing.
    """
    if state.get("intent") and state.get("missing_slots"):
        return {"intent": state["intent"]}

    message = state.get("current_message", "")

    response = client.chat.completions.create(
        model=GATEWAY_MODEL,
        messages=[
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
        temperature=0,
    )
    intent = response.choices[0].message.content.strip().lower()
    if intent not in {"greeting", "query", "book", "reschedule", "cancel", "goodbye"}:
        intent = "query"  # safe fallback — same principle as query_agent's JSON parse fallback

    return {"intent": intent}


@log_transition("rag")
def rag_node(state: GraphState) -> dict:
    """
    Wraps retriever.unified_retrieve() — the semantic/structured-routing
    lookup, distinct from recommendation_node's scored ranking. Used for
    questions like "do you help with registry" that aren't a property
    search with criteria, just a factual/FAQ lookup.
    """
    message = state.get("current_message", "")
    result = unified_retrieve(message)

    tool_outputs = dict(state.get("tool_outputs", {}))
    tool_outputs["rag"] = result

    _log_transcript(state, intent="query", raw_query=message, extra={"rag_route": result["route"]})

    return {
        "tool_outputs": tool_outputs,
        "response": _summarize_rag(result),
    }


def _summarize_rag(result: dict) -> str:
    if result["route"] == "structured" and result["results"]:
        top = result["results"][0]
        return f"{top['title']} in {top['area_name']}, PKR {top.get('price') or top.get('rent_per_month')}."
    if result["route"] == "semantic" and result["results"]:
        return result["results"][0]["text"][:300]
    return "I couldn't find anything matching that, could you rephrase?"


@log_transition("recommendation")
def recommendation_node(state: GraphState) -> dict:
    """
    Wraps query_agent's extract_criteria + recommend.recommend_properties
    + generate_grounded_answer — i.e. Task 4's full grounded-answer
    pipeline, unchanged, just called from inside a graph node instead of
    a standalone function. Updates property_preferences in state so it
    persists across turns (Task 1 requirement).
    """
    message = state.get("current_message", "")
    criteria = extract_criteria(message)

    prefs = dict(state.get("property_preferences", {}))
    prefs.update({k: v for k, v in criteria.items() if v not in (None, "", [], {})})

    matches = recommend_properties(
        budget=prefs.get("budget"), city=prefs.get("city"), area=prefs.get("area"),
        bedrooms=prefs.get("bedrooms"), purpose=prefs.get("purpose"),
        amenities=prefs.get("amenities"), investment_goals=prefs.get("investment_goals"),
    )
    answer = generate_grounded_answer(message, matches, prefs)

    tool_outputs = dict(state.get("tool_outputs", {}))
    tool_outputs["recommendation"] = matches

    client_id = state.get("user_profile", {}).get("client_id")
    if client_id:
        crm_store.update_client_preferences(client_id, criteria)
        crm_store.log_call_transcript(
            client_id=client_id, intent="query", raw_query=message,
            extracted_criteria=criteria, answer=answer, matches_count=len(matches),
        )

    return {"property_preferences": prefs, "tool_outputs": tool_outputs, "response": answer}


@log_transition("booking")
def booking_node(state: GraphState) -> dict:
    """
    Wraps appointment_manager.book_appointment(). Requires
    appointment_details + user_profile already populated in state —
    see module docstring's "known gap" note; no slot-filling happens here.
    """
    details = state.get("appointment_details", {})
    profile = state.get("user_profile", {})

    result = appointment_manager.book_appointment(
        client_name=profile.get("name"),
        client_phone=profile.get("phone"),
        employee=details.get("employee"),
        employee_email=details.get("employee_email"),
        employee_name=details.get("employee_name"),
        property_name=details.get("property_name"),
        date=details.get("date"),
        start_time=details.get("start_time"),
        end_time=details.get("end_time"),
        meeting_notes=details.get("meeting_notes", ""),
        requirements=details.get("requirements", ""),
    )

    tool_outputs = dict(state.get("tool_outputs", {}))
    tool_outputs["booking"] = result

    status = "booked" if result.get("success") else "failed"
    appt_id = _log_appointment(state, status, details, result)
    if result.get("success") and appt_id:
        crm_store.create_appointment_reminder(profile.get("client_id"), appt_id, details.get("date"))
    elif appt_id:
        crm_store.create_manual_followup(
            profile.get("client_id"), reason=f"Booking failed: {result.get('error')}", appointment_id=appt_id
        )

    response = (
        f"Booked for {details.get('date')} at {details.get('start_time')}."
        if result.get("success") else f"Sorry, booking failed: {result.get('error')}"
    )
    # A successful booking completes what the caller came here to do — end
    # the conversation so session_state clears and the next call starts
    # fresh. A failure keeps the session open so they can retry without
    # re-supplying everything (event_id, property, etc. stay filled).
    return {
        "tool_outputs": tool_outputs, "appointment_status": status, "response": response,
        "conversation_ended": bool(result.get("success")),
    }


@log_transition("reschedule")
def reschedule_node(state: GraphState) -> dict:
    """Wraps appointment_manager.reschedule_appointment(). Same slot-data gap as booking_node."""
    details = state.get("appointment_details", {})
    profile = state.get("user_profile", {})

    result = appointment_manager.reschedule_appointment(
        event_id=details.get("event_id"),
        new_date=details.get("new_date"), new_start=details.get("new_start"), new_end=details.get("new_end"),
        employee_email=details.get("employee_email"), employee_name=details.get("employee_name"),
        client_name=profile.get("name"), client_phone=profile.get("phone"),
        property_name=details.get("property_name"),
        old_date=details.get("old_date"), old_start=details.get("old_start"), old_end=details.get("old_end"),
        meeting_notes=details.get("meeting_notes", ""), requirements=details.get("requirements", ""),
    )

    tool_outputs = dict(state.get("tool_outputs", {}))
    tool_outputs["reschedule"] = result

    status = "rescheduled" if result.get("success") else "failed"
    appt_id = _log_appointment(state, status, details, result, date_field="new_date",
                                start_field="new_start", end_field="new_end")
    if result.get("success") and appt_id:
        crm_store.create_appointment_reminder(profile.get("client_id"), appt_id, details.get("new_date"))
    elif appt_id:
        crm_store.create_manual_followup(
            profile.get("client_id"), reason=f"Reschedule failed: {result.get('error')}", appointment_id=appt_id
        )

    response = (
        f"Rescheduled to {details.get('new_date')} at {details.get('new_start')}."
        if result.get("success") else f"Sorry, reschedule failed: {result.get('error')}"
    )
    return {
        "tool_outputs": tool_outputs, "appointment_status": status, "response": response,
        "conversation_ended": bool(result.get("success")),
    }


@log_transition("cancellation")
def cancellation_node(state: GraphState) -> dict:
    """Wraps appointment_manager.cancel_appointment(). Same slot-data gap as booking_node."""
    details = state.get("appointment_details", {})
    profile = state.get("user_profile", {})

    result = appointment_manager.cancel_appointment(
        event_id=details.get("event_id"),
        employee_email=details.get("employee_email"), employee_name=details.get("employee_name"),
        client_name=profile.get("name"), client_phone=profile.get("phone"),
        property_name=details.get("property_name"),
        date=details.get("date"), start_time=details.get("start_time"), end_time=details.get("end_time"),
        reason=details.get("reason", ""),
    )

    tool_outputs = dict(state.get("tool_outputs", {}))
    tool_outputs["cancellation"] = result

    status = "cancelled" if result.get("success") else "failed"
    appt_id = _log_appointment(state, status, details, result)
    if not result.get("success") and appt_id:
        crm_store.create_manual_followup(
            profile.get("client_id"), reason=f"Cancellation failed: {result.get('error')}", appointment_id=appt_id
        )

    response = (
        "Your appointment has been cancelled." if result.get("success")
        else f"Sorry, cancellation failed: {result.get('error')}"
    )
    return {
        "tool_outputs": tool_outputs, "appointment_status": status, "response": response,
        "conversation_ended": bool(result.get("success")),
    }


@log_transition("email")
def email_node(state: GraphState) -> dict:
    """
    appointment_manager already sends the actual notification email
    internally (that's what email_tool.send_*_notification calls are for).
    This node doesn't send a second email — it's the confirmation/logging
    step after Booking/Reschedule/Cancellation, closing out the
    "Match -> Appointment -> Calendar -> Email -> CRM Update" chain from
    the original task diagram.
    """
    last_tool = state.get("tool_outputs", {})
    email_sent = any(
        v.get("email_sent") for k, v in last_tool.items()
        if k in ("booking", "reschedule", "cancellation") and isinstance(v, dict)
    )
    note = " A confirmation email has been sent." if email_sent else ""
    return {"response": state.get("response", "") + note}


@log_transition("goodbye")
def goodbye_node(state: GraphState) -> dict:
    return {"response": "Thanks for calling, have a great day!", "conversation_ended": True}


# ---------------------------------------------------------------
# TASK 4 — VALIDATION NODES
# ---------------------------------------------------------------
SLOT_QUESTIONS = {
    "property_name": "which property are you interested in visiting",
    "date": "what date works for you",
    "start_time": "what time would you like to come",
    "end_time": "what time would you like to finish",
    "event_id": "which appointment (I'll need the booking reference)",
    "new_date": "what new date works for you",
    "new_start": "what new start time works for you",
    "new_end": "what new end time works for you",
}

# Maps the extractor's generic field names to the intent-specific keys
# REQUIRED_SLOTS actually uses. "date"/"start_time"/"end_time" from the
# extractor become "new_date"/"new_start"/"new_end" for reschedule, since
# that's what appointment_manager.reschedule_appointment expects.
FIELD_MAP_BY_INTENT = {
    "book": {"property_name": "property_name", "date": "date",
             "start_time": "start_time", "end_time": "end_time", "event_id": "event_id"},
    "reschedule": {"property_name": "property_name", "date": "new_date",
                   "start_time": "new_start", "end_time": "new_end", "event_id": "event_id"},
    "cancel": {"property_name": "property_name", "date": "date",
               "start_time": "start_time", "end_time": "end_time", "event_id": "event_id"},
}

SLOT_EXTRACTION_SYSTEM_PROMPT = """Extract appointment scheduling details from the customer's message.
Respond with ONLY a JSON object, no other text, no markdown fences, using exactly these keys:
  "property_name": string or null
  "date": string in YYYY-MM-DD format or null — resolve relative dates ("tomorrow", "next Monday") using the reference date given below
  "start_time": string in 24-hour HH:MM format or null
  "end_time": string in 24-hour HH:MM format or null
  "event_id": string or null — only if the customer references an existing booking/reference number

If a start time is given but no end time, leave end_time null — do not guess a duration.
Today's reference date is: {today}
"""


def extract_appointment_slots(message: str) -> dict:
    """
    LLM-based extraction of booking fields from free text, the booking
    equivalent of query_agent.extract_criteria() for property search.
    Returns only the keys the model actually found (nulls/empties dropped),
    using generic field names — slot_filling_node maps these onto the
    intent-specific keys REQUIRED_SLOTS expects via FIELD_MAP_BY_INTENT.
    Never raises: a parse failure or API error just yields no extraction,
    so the turn falls through to the existing clarifying-question flow
    instead of crashing the graph.
    """
    today = datetime.date.today().isoformat()
    try:
        response = client.chat.completions.create(
            model=GATEWAY_MODEL,
            messages=[
                {"role": "system", "content": SLOT_EXTRACTION_SYSTEM_PROMPT.format(today=today)},
                {"role": "user", "content": message},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
    except Exception as e:
        print(f"[slot_extraction] WARNING: extraction failed, continuing with no new fields: {e}")
        parsed = {}

    return {k: v for k, v in parsed.items() if v not in (None, "", "null")}


@log_transition("slot_filling")
def slot_filling_node(state: GraphState) -> dict:
    """
    Task 4: "ask clarification instead of guessing." First runs
    extract_appointment_slots() against this turn's message and merges
    anything found into appointment_details (without overwriting fields
    already set from a previous turn), THEN checks appointment_details
    against REQUIRED_SLOTS for the current intent. If anything's still
    missing, this turn ends with a clarifying question and missing_slots
    populated — the graph does NOT proceed to booking/reschedule/
    cancellation with a None in a required field.

    Also auto-fills employee assignment from employee_data.py when a
    property/city is already known, so the caller isn't asked to pick an
    employee themselves — that's staff-side, not something the client
    should have to know.
    """
    intent = state.get("intent")
    required = REQUIRED_SLOTS.get(intent, [])
    details = dict(state.get("appointment_details", {}))

    message = state.get("current_message", "")
    extracted = extract_appointment_slots(message)
    field_map = FIELD_MAP_BY_INTENT.get(intent, {})
    for generic_key, target_key in field_map.items():
        if generic_key in extracted and not details.get(target_key):
            details[target_key] = extracted[generic_key]

    # Fallback: if the caller already discussed a specific property earlier
    # this conversation (via recommendation_node), don't make them repeat
    # it just to book — use the top match from that turn's results.
    if intent == "book" and not details.get("property_name"):
        last_matches = state.get("tool_outputs", {}).get("recommendation")
        if last_matches:
            details["property_name"] = last_matches[0].get("title")

    # Auto-assign employee based on known city preference, if not already set
    if intent == "book" and not details.get("employee_email"):
        city = state.get("property_preferences", {}).get("city")
        emp = employee_data.get_employee_by_city(city)
        details["employee"] = emp["id"]
        details["employee_name"] = emp["name"]
        details["employee_email"] = emp["email"]

    # Cancel only needs the caller to identify WHICH appointment — once
    # event_id is known, pull property/date/time/employee from what's
    # already on file instead of making them re-state details the system
    # already has. If the lookup finds nothing (stale/mistyped event_id),
    # fall through to the normal missing-slot flow so they're asked to
    # confirm rather than the request silently going through with gaps.
    if intent == "cancel" and details.get("event_id") and not details.get("date"):
        existing = crm_store.get_appointment_by_event_id(details["event_id"])
        if existing:
            details.setdefault("property_name", existing.get("property_title"))
            details.setdefault("date", existing.get("date"))
            details.setdefault("start_time", existing.get("start_time"))
            details.setdefault("end_time", existing.get("end_time"))
            details.setdefault("employee_name", existing.get("employee"))
            details.setdefault("employee_email", existing.get("employee_email"))

    missing = [f for f in required if not details.get(f)]

    if missing:
        questions = [SLOT_QUESTIONS.get(f, f) for f in missing]
        response = "Before I continue, could you tell me " + " and ".join(questions) + "?"
        return {"appointment_details": details, "missing_slots": missing, "response": response}

    return {"appointment_details": details, "missing_slots": []}


@log_transition("availability_check")
def availability_check_node(state: GraphState) -> dict:
    """
    Task 4: "never book unavailable slots." Wraps calendar_tool.check_availability
    directly (not through appointment_manager, which doesn't check availability
    itself — it just creates the event outright). Runs after slot_filling_node
    confirms date/start/end are present, before booking_node/reschedule_node
    are allowed to run.

    available == None means the check itself failed (network/API error) —
    treated as "can't confirm it's free," same conservative rule
    calendar_tool.py's own docstring specifies, not treated as available.
    """
    intent = state.get("intent")
    details = state.get("appointment_details", {})

    date_field = "new_date" if intent == "reschedule" else "date"
    start_field = "new_start" if intent == "reschedule" else "start_time"
    end_field = "new_end" if intent == "reschedule" else "end_time"

    date, start, end = details.get(date_field), details.get(start_field), details.get(end_field)
    if not (date and start and end):
        # Shouldn't happen if slot_filling ran first, but don't crash if it does
        return {"missing_slots": [date_field, start_field, end_field]}

    result = calendar_tool.check_availability(date, start, end)

    tool_outputs = dict(state.get("tool_outputs", {}))
    tool_outputs["availability"] = result

    if result.get("available") is not True:
        conflicts = ", ".join(result.get("conflicting_events", [])) or "an unknown scheduling conflict"
        reason = "couldn't confirm that slot is free" if result.get("available") is None else f"that slot conflicts with {conflicts}"
        # Clear the rejected time from appointment_details, not just flag it
        # missing — slot_filling_node only overwrites a field when it's
        # currently empty, so leaving the stale rejected time in place would
        # make the caller's next answer ("2pm to 3pm works") get silently
        # discarded and the same conflict re-checked forever.
        cleared_details = dict(details)
        cleared_details.pop(start_field, None)
        cleared_details.pop(end_field, None)
        return {
            "tool_outputs": tool_outputs,
            "appointment_details": cleared_details,
            "missing_slots": [start_field, end_field],
            "response": f"Sorry, {reason}. Could you suggest a different time?",
        }

    return {"tool_outputs": tool_outputs, "missing_slots": []}


# ---------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------
def _log_transcript(state: GraphState, intent: str, raw_query: str, extra: dict = None) -> None:
    client_id = state.get("user_profile", {}).get("client_id")
    if client_id:
        crm_store.log_call_transcript(
            client_id=client_id, intent=intent, raw_query=raw_query,
            extracted_criteria=extra or {}, answer=state.get("response", ""),
        )


def _log_appointment(state: GraphState, status: str, details: dict, result: dict,
                      date_field="date", start_field="start_time", end_field="end_time") -> int:
    client_id = state.get("user_profile", {}).get("client_id")
    if not client_id:
        return None
    return crm_store.log_appointment(
        client_id=client_id, status=status,
        event_id=result.get("event_id", details.get("event_id")),
        property_title=details.get("property_name"), employee=details.get("employee_name"),
        employee_email=details.get("employee_email"),
        date=details.get(date_field), start_time=details.get(start_field), end_time=details.get(end_field),
        details={"error": result.get("error")} if not result.get("success") else None,
    )


# ---------------------------------------------------------------
# ROUTING
# ---------------------------------------------------------------
def _has_filter_terms(message: str) -> bool:
    """
    recommend.py needs actual criteria (budget/bedrooms/etc) to produce a
    ranked list, so anything with at least one filterable term should go
    to Recommendation; open-ended factual questions ("do you help with
    registry") should go to RAG instead.
    """
    filter_terms = ["bedroom", "budget", "crore", "lakh", "price", "kamra"]
    return any(t in message.lower() for t in filter_terms)


def route_by_intent(state: GraphState) -> str:
    """
    Single conditional-edge function for intent_detection. Handles both
    the top-level intent split AND the query/rag-vs-recommendation split
    in one place, since LangGraph only honors the most recently added
    add_conditional_edges call per source node — two separate calls off
    the same node don't compose, the second just overrides the first.

    book/reschedule/cancel all route through slot_filling first now
    (Task 4), not directly to their action node.
    """
    intent = state.get("intent")
    if intent == "query":
        return "recommendation" if _has_filter_terms(state.get("current_message", "")) else "rag"
    return {
        "book": "slot_filling",
        "reschedule": "slot_filling",
        "cancel": "slot_filling",
        "goodbye": "goodbye",
        "greeting": "goodbye",  # a bare "hello" with nothing else just loops to closing for now
    }.get(intent, "rag")


def route_after_slot_filling(state: GraphState) -> str:
    """
    If slot_filling_node found anything missing, stop here and return the
    clarifying question — do NOT proceed to availability checking or the
    action node with incomplete data. Otherwise, book/reschedule go
    through availability_check next (Task 4); cancel doesn't need an
    availability check (cancelling doesn't need a free slot), so it goes
    straight to cancellation_node.
    """
    if state.get("missing_slots"):
        return "end_turn"
    intent = state.get("intent")
    if intent in ("book", "reschedule"):
        return "availability_check"
    return "cancellation"


def route_after_availability(state: GraphState) -> str:
    """If the slot wasn't available, stop and return the clarifying
    response — don't call appointment_manager with a conflicting time."""
    if state.get("missing_slots"):
        return "end_turn"
    intent = state.get("intent")
    return "booking" if intent == "book" else "reschedule"


# ---------------------------------------------------------------
# GRAPH ASSEMBLY
# ---------------------------------------------------------------
def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("greeting", greeting_node)
    graph.add_node("intent_detection", intent_detection_node)
    graph.add_node("rag", rag_node)
    graph.add_node("recommendation", recommendation_node)
    graph.add_node("slot_filling", slot_filling_node)
    graph.add_node("availability_check", availability_check_node)
    graph.add_node("booking", booking_node)
    graph.add_node("reschedule", reschedule_node)
    graph.add_node("cancellation", cancellation_node)
    graph.add_node("email", email_node)
    graph.add_node("goodbye", goodbye_node)

    graph.set_entry_point("greeting")
    graph.add_edge("greeting", "intent_detection")

    graph.add_conditional_edges(
        "intent_detection",
        route_by_intent,
        {
            "rag": "rag",
            "recommendation": "recommendation",
            "slot_filling": "slot_filling",
            "goodbye": "goodbye",
        },
    )

    graph.add_conditional_edges(
        "slot_filling",
        route_after_slot_filling,
        {"end_turn": END, "availability_check": "availability_check", "cancellation": "cancellation"},
    )

    graph.add_conditional_edges(
        "availability_check",
        route_after_availability,
        {"end_turn": END, "booking": "booking", "reschedule": "reschedule"},
    )

    graph.add_edge("rag", END)
    graph.add_edge("recommendation", END)
    graph.add_edge("booking", "email")
    graph.add_edge("reschedule", "email")
    graph.add_edge("cancellation", "email")
    graph.add_edge("email", END)
    graph.add_edge("goodbye", END)

    return graph.compile()


if __name__ == "__main__":
    app = build_graph()

    demo_state = {
        "conversation_history": [],
        "current_message": "3 bedroom Karachi 5 crore, swimming pool chahiye",
        "user_profile": {"name": "Ahmed Raza", "phone": "0300-9998888"},
        "property_preferences": {},
        "tool_outputs": {},
    }

    result = app.invoke(demo_state)
    print("Intent detected:", result.get("intent"))
    print("Response:", result.get("response"))
    print("Missing slots:", result.get("missing_slots"))
    print("\nExecution trace:")
    for entry in result.get("execution_trace", []):
        print(f"  {entry['timestamp']}  {entry['node']}  (intent={entry['intent']})")