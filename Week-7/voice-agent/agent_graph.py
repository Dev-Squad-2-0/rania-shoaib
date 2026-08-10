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
import json
import datetime

from graph_state import GraphState
from query_agent import extract_criteria, generate_grounded_answer
from recommend import recommend_properties
from retriever import unified_retrieve
from price_format import format_pkr
import appointment_manager
import calendar_tool
import crm_store
import employee_data
from llm_client import client, MODEL as GATEWAY_MODEL
from openai import RateLimitError, APIError, APITimeoutError

# Task 4: which appointment_details fields each intent needs before it's
# safe to call appointment_manager. Anything missing triggers a
# clarification turn instead of calling the tool with a None.
REQUIRED_SLOTS = {
    "book": ["property_name", "date", "start_time", "end_time"],
    "reschedule": ["event_id", "new_date", "new_start", "new_end"],
    # Cancellation should be speakable in natural terms first: property,
    # date, and time. event_id is resolved from the client's own booking
    # history when possible, and only asked for as a fallback if the lookup
    # stays ambiguous.
    "cancel": ["property_name", "date", "start_time", "end_time"],
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


import re

def _strip_markdown(text: str) -> str:
    """
    Removes markdown formatting characters that TTS engines read aloud literally:
    - **bold** and *italic* asterisks
    - # headings
    - - or * bullet list markers at start of lines
    - Numbered list formatting (1. 2. etc — kept as spoken numbers)
    Produces clean spoken prose suitable for Fish Audio / ElevenLabs TTS.
    """
    if not text:
        return text
    # Remove bold/italic: **text** -> text, *text* -> text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Remove heading markers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove bullet list markers at start of line (- item or * item)
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    # Collapse multiple blank lines to one
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _turn_role_and_content(turn) -> tuple[str, str]:
    """Accept both dict history items and LangChain message objects."""
    if isinstance(turn, dict):
        return turn.get("role", "user"), turn.get("content", "")

    role = getattr(turn, "type", None) or getattr(turn, "role", None) or "user"
    content = getattr(turn, "content", "")
    return role, content


PROMPT_INJECTION_PATTERNS = (
    # was `ignore (all )?instructions` — broke on any word between "ignore"
    # and "instructions" (e.g. "ignore all PREVIOUS instructions"). Allow
    # up to ~3 filler words so common variants ("ignore the above/prior/
    # earlier instructions") still match.
    r"ignore\b(?:\s+\w+){0,3}?\s+instructions",
    r"forget\b(?:\s+\w+){0,3}?\s+(instructions|above|prompt)",
    r"disregard\b(?:\s+\w+){0,3}?\s+instructions",
    r"reveal (your|the) prompt",
    r"(show|tell) (me )?(your|the) (system )?prompt",
    r"what('s| is) your (system )?prompt",
    r"give (me )?internal (company )?data",
    r"api key",
    r"database password",
    r"book fake appointments?",
    r"simulate fake appointments?",
    r"fake booking",
    r"internal instructions",
    r"developer message",
    r"jailbreak",
    r"act as an? unrestricted",
    r"you are now (in )?(dan|developer) mode",
)


def _looks_like_prompt_injection(message: str) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(re.search(pattern, lowered) for pattern in PROMPT_INJECTION_PATTERNS)


FOLLOWUP_CLASSIFIER_PROMPT = """You are a routing classifier for a real estate voice agent.
Decide whether the current customer message is a FOLLOW-UP PROPERTY SEARCH.

A FOLLOW-UP PROPERTY SEARCH is a vague request that refers back to prior property discussion,
such as asking for another option, a similar option, or something in the same range/budget.

Use the recent conversation context to decide. If the current message clearly continues a property
search, respond with exactly one word: FOLLOWUP_PROPERTY.
Otherwise respond with exactly one word: OTHER.
"""


def _call_llm_with_retry(*, model: str, messages: list[dict], temperature: float = 0):
    last_error = None
    for attempt in range(1, 4):
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
        except (RateLimitError, APITimeoutError, APIError) as exc:
            last_error = exc
            if attempt == 3:
                raise
            wait_seconds = 5
            match = re.search(r"try again in ([\d.]+)s", str(exc))
            if match:
                wait_seconds = float(match.group(1)) + 1
            time.sleep(wait_seconds)
    raise last_error


def _is_followup_property_query(message: str, conversation_history: list) -> bool:
    if not message:
        return False

    recent_turns = []
    for turn in (conversation_history or [])[-4:]:
        role, content = _turn_role_and_content(turn)
        if content and role in ("user", "assistant"):
            recent_turns.append({"role": role, "content": content})

    prompt_messages = [{"role": "system", "content": FOLLOWUP_CLASSIFIER_PROMPT}]
    if recent_turns:
        prompt_messages.append({
            "role": "user",
            "content": "Recent conversation:\n" + "\n".join(f"{turn['role']}: {turn['content']}" for turn in recent_turns)
        })
    prompt_messages.append({"role": "user", "content": f"Current message: {message}"})

    try:
        response = _call_llm_with_retry(model=GATEWAY_MODEL, messages=prompt_messages, temperature=0)
        verdict = response.choices[0].message.content.strip().upper()
        return verdict == "FOLLOWUP_PROPERTY"
    except Exception:
        return False


# ---------------------------------------------------------------
# NODES
# ---------------------------------------------------------------
GREETING_TEXT = "Assalam-o-Alaikum! RealEstate Hub se Ayesha baat kar rahi hoon. Main aap ki kis tarah madad kar sakti hoon?"


@log_transition("greeting")
def greeting_node(state: GraphState) -> dict:
    """
    Runs on EVERY turn (it's the graph's entry point) purely to resolve the
    caller to a real client record via crm_store — every later node needs
    user_profile available without re-deriving it.

    BUG FIX: this used to also unconditionally set state["response"] to the
    welcome line on every single turn, before intent_detection_node ran.
    That meant intent_detection_node's "what did we ask last turn"
    (pending_question) logic was reading the just-clobbered welcome text
    instead of the real previous clarifying question — every turn, not just
    the first — which silently broke multi-turn slot-filling (booking dates/
    times got routed to the FAQ/RAG node instead of being accepted) and
    randomly caused the welcome line to resurface mid-conversation.

    Fix: only emit the welcome response here on a genuinely fresh session
    (no prior intent and no conversation history yet). A real mid-call
    "hello" is handled separately by greeting_reply_node, downstream of
    intent_detection actually classifying it as such — so this node no
    longer overwrites state["response"] (or pending_question) on turns
    where the caller is answering an outstanding question.
    """
    profile = state.get("user_profile", {}) or {}
    phone = profile.get("phone")
    name = profile.get("name")

    if phone:
        client_id = crm_store.get_or_create_client(phone, name)
        profile = {"client_id": client_id, "name": name, "phone": phone}

    is_fresh_session = not state.get("intent") and not state.get("conversation_history")
    if is_fresh_session:
        return {"user_profile": profile, "response": GREETING_TEXT, "pending_question": None}

    return {"user_profile": profile}


@log_transition("greeting_reply")
def greeting_reply_node(state: GraphState) -> dict:
    """
    Handles a genuine mid-conversation "hello" (intent_detection_node
    classified this turn as 'greeting'). Separate from greeting_node so a
    real greeting still gets a friendly reply without greeting_node having
    to guess at classification before intent_detection has even run.
    """
    conversation_history = state.get("conversation_history", []) or []
    if conversation_history:
        return {"response": "Jee, batayein kya madad kar sakti hoon?", "pending_question": None}

    return {"response": GREETING_TEXT, "pending_question": None}


@log_transition("safety")
def safety_node(state: GraphState) -> dict:
    return {
        "response": (
            "Sorry, mein internal prompt ya company data share nahi kar sakti. "
            "Agar aap real property search, booking, reschedule, ya cancellation chahen to bata dein."
        ),
        "pending_question": None,
        "conversation_ended": False,
    }


INTENT_SYSTEM_PROMPT = """Classify the customer's message into exactly one intent.
Respond with ONLY the single word, no other text:
greeting, query, book, reschedule, cancel, seller, complaint, goodbye

- "query" = asking about properties, prices, availability, general questions
- "book" = wants to schedule a new visit/appointment. This includes any phrasing
  that means "I want an appointment/visit," even without the word "book" —
  e.g. "appointment lena hai", "visit karni hai", "mujhe dikhana hai property",
  "kal aa sakte hain kya" — these are all "book", not "query".
- "reschedule" = wants to change an existing appointment's time
- "cancel" = wants to cancel an existing appointment
- "seller" = wants to list or sell a property, publish a listing, or ask about seller-side help
- "complaint" = expresses frustration, dissatisfaction, or asks for a human after a bad experience
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

    If a previous turn is still waiting on missing_slots (asked a
    clarifying question like "what time works?"), that pending question
    is passed to the classifier as context, and it's told to keep the
    same intent ONLY if this message actually answers it (a date, time,
    confirmation, reference number, etc). This still handles a bare
    reply like "2pm works" or a bare event_id correctly (those wouldn't
    contain "book"/"cancel" keywords on their own), without the earlier
    version's bug: previously this was a hardcoded shortcut that trusted
    ANY leftover intent+missing_slots unconditionally and never even
    looked at the new message, which meant a stale/abandoned session
    (or a genuine topic change mid-conversation) hijacked every future
    turn into the same clarifying question forever, regardless of what
    the caller actually said.

    If the classifier decides this message is a genuine topic switch
    away from the pending intent, appointment_details/missing_slots from
    that abandoned flow are cleared so the new intent starts clean
    instead of dragging along irrelevant half-filled slot data.
    """
    message = state.get("current_message", "")
    pending_intent = state.get("intent")
    if _looks_like_prompt_injection(message):
        return {"intent": "safety", "missing_slots": [], "pending_question": None}
    # BUG FIX: this used to read state.get("response"), which greeting_node
    # (the unconditional entry node, running just before this one every
    # turn) had already overwritten with the welcome line — so this was
    # never actually the real previous clarifying question. pending_question
    # is a dedicated field only ever written by the nodes that genuinely ask
    # a clarifying question (slot_filling_node, availability_check_node),
    # and it's persisted across turns via crm_store.SESSION_FIELDS, so it's
    # safe to read here as ground truth.
    pending_question = state.get("pending_question") if state.get("missing_slots") else None

    system_prompt = INTENT_SYSTEM_PROMPT
    conversation_history = state.get("conversation_history", []) or []
    recent_turns = []
    for turn in conversation_history[-4:]:
        role, content = _turn_role_and_content(turn)
        if content:
            recent_turns.append(f"{role}: {content}")
    if recent_turns:
        system_prompt += (
            "\n\nRecent conversation context (use this to resolve short follow-ups like 'wo konsa hai' "
            "or 'us mein se sasti'):\n" + "\n".join(recent_turns)
        )
    if pending_intent and pending_question:
        system_prompt += (
            f"\n\nContext: the assistant's previous turn was mid-way through a "
            f"'{pending_intent}' flow and asked the customer: \"{pending_question}\". "
            f"If this message is a direct answer to that question (e.g. a date, "
            f"time, confirmation, or reference number), classify it as "
            f"'{pending_intent}' again. Only classify it as something else if the "
            f"customer is clearly changing topics (a new question, a new intent, "
            f"or a plain greeting/goodbye unrelated to that question)."
        )

    response = client.chat.completions.create(
        model=GATEWAY_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ],
        temperature=0,
    )
    intent = response.choices[0].message.content.strip().lower()
    if intent not in {"greeting", "query", "book", "reschedule", "cancel", "seller", "complaint", "goodbye"}:
        intent = "query"  # safe fallback — same principle as query_agent's JSON parse fallback

    # BUG FIX: the system_prompt instruction above ("keep the same intent
    # only if this message answers the pending question") is a single LLM
    # judgment call with nothing to catch it when the model doesn't follow
    # it. Verified failure case: mid-'book' flow (missing property/date/
    # time), customer sends a full fresh property search — "Bahria Town
    # mein 3 bed ghar chahiye, 3 crore mein" — which answers NONE of the
    # pending slots and is unambiguously a new query, and the classifier
    # still returned 'book' again, permanently wedging the caller behind
    # the same clarifying question. Add a deterministic cross-check: if the
    # classifier said "stay on the pending intent," verify that either (a)
    # this message actually extracts a new appointment slot, or (b) it
    # doesn't look like an independent property search. If neither holds,
    # override to 'query' — a message that answers nothing and looks like
    # a full property ask is a topic switch no matter what the classifier said.
    # BUG FIX: the system_prompt instruction above ("keep the same intent
    # only if this message answers the pending question") is a single LLM
    # judgment call with nothing to catch it when the model doesn't follow
    # it. Verified failure case: mid-'book' flow (missing property/date/
    # time), customer sends a full fresh property search — "Bahria Town
    # mein 3 bed ghar chahiye, 3 crore mein" — which answers NONE of the
    # pending slots and is unambiguously a new query, and the classifier
    # still returned 'book' again, permanently wedging the caller behind
    # the same clarifying question.
    #
    # Add a deterministic cross-check when the classifier says "stay."
    # NOTE: an earlier version of this check used _is_property_search()
    # (agent_graph's own keyword list + a second single-shot LLM fallback)
    # to decide "does this look like a property search" — that's the same
    # brittle pattern this whole fix exists to guard against: a keyword
    # list will always have gaps (STT/Urdu-script variance especially),
    # and a second bare classifier call is no more trustworthy than the
    # first one that just failed. Use extract_criteria() instead — it's
    # already the purpose-built, tested LLM extraction for "does this
    # message contain real property criteria" (city/area/budget/bedrooms/
    # purpose/amenities), reused as-is from query_agent.py rather than a
    # second heuristic guess. If it comes back with every field null, this
    # genuinely isn't a property search and the classifier's "stay" verdict
    # is trusted. If it finds real criteria AND extract_appointment_slots()
    # found nothing answering the pending question, that's a strong enough
    # double-signal to override the classifier.
    if (
        pending_intent
        and intent == pending_intent
        and pending_intent in ("book", "reschedule", "cancel")
    ):
        criteria = extract_criteria(message)
        has_property_criteria = any(
            criteria.get(k) not in (None, "", [], {})
            for k in ("budget", "city", "area", "bedrooms", "purpose", "amenities", "investment_goals")
        )
        if has_property_criteria:
            extracted_slots = extract_appointment_slots(message)
            answered_something = bool(extracted_slots)
            if not answered_something:
                return {
                    "intent": "query",
                    "missing_slots": [],
                    "appointment_details": {},
                    "pending_question": None,
                }

    if pending_intent and intent != pending_intent:
        # Real topic switch away from an unfinished flow — don't let the
        # old flow's half-filled slots leak into whatever comes next.
        return {"intent": intent, "missing_slots": [], "appointment_details": {}, "pending_question": None}

    return {"intent": intent}


@log_transition("rag")
def rag_node(state: GraphState) -> dict:
    """
    Wraps retriever.unified_retrieve() — the semantic/structured-routing
    lookup, distinct from recommendation_node's scored ranking. Used for
    questions like "do you help with registry" that aren't a property
    search with criteria, just a factual/FAQ lookup.

    BUG FIX: this used to call unified_retrieve(message) with nothing else
    — no idea a specific property was already on the table from an earlier
    recommendation_node turn. A vague follow-up like "yeh Islamabad mein
    hai?" would then run a brand-new, unfiltered semantic search on that
    sentence, which could easily surface a DIFFERENT property's brochure
    chunk, and _summarize_rag() spoke that chunk's raw text back as fact —
    that's exactly how "Bahria Town ... F-10 Markaz" got said about a
    property that was actually in Karachi. If a property was already
    discussed this conversation, scope the semantic search to that
    specific property_id instead of searching the whole collection fresh.
    """
    message = state.get("current_message", "")
    last_property_id = None
    last_matches = state.get("tool_outputs", {}).get("recommendation")
    if last_matches and isinstance(last_matches, list) and last_matches:
        last_property_id = last_matches[0].get("id")
    if last_property_id is None:
        last_property_id = state.get("property_preferences", {}).get("_last_recommended_property_id")
    result = unified_retrieve(message, property_id=last_property_id)

    tool_outputs = dict(state.get("tool_outputs", {}))
    tool_outputs["rag"] = result

    _log_transcript(state, intent="query", raw_query=message, extra={"rag_route": result["route"]})

    return {
        "tool_outputs": tool_outputs,
        "response": _summarize_rag(result),
        "pending_question": None,  # this is a final answer, not a clarifying question
    }


def _summarize_rag(result: dict) -> str:
    if result["route"] == "structured" and result["results"]:
        top = result["results"][0]
        price = top.get('price') or top.get('rent_per_month')
        # BUG FIX: this used to speak f"PKR {price:,}" — a raw digit string
        # like "PKR 45,000,000", which is both unnatural for TTS to read
        # aloud and inconsistent with recommendation_node's phrasing. Route
        # it through the same format_pkr() used everywhere else a price is
        # spoken, so this says "4 crore 50 lakh" like every other answer.
        return f"Jee bilkul, {top['title']} {top['area_name']} mein available hai, is ki price {format_pkr(price)} hai."
    if result["route"] == "semantic" and result["results"]:
        # BUG FIX: if this was scoped to a specific property (see rag_node)
        # and Chroma still came back empty for that property's own chunks,
        # do NOT fall through and let a later branch read out some other
        # property's text as if it answered the question — that's exactly
        # how "Bahria Town ... F-10 Markaz" got said about a Karachi
        # property. Say honestly that the detail isn't available instead.
        return f"Jee bilkul, {result['results'][0]['text'][:250]}"
    if result.get("grounded_to_property_id") is not None:
        return "Ye detail mere paas abhi nahi hai us property ke liye, mein confirm kar ke aap ko bata deti hoon."
    return "Mujhe is baare mein exact detail nahi mil saki, kya aap thora mazeed bata sakte hain?"



@log_transition("recommendation")
def recommendation_node(state: GraphState) -> dict:
    """
    Wraps query_agent's extract_criteria + recommend.recommend_properties
    + generate_grounded_answer. Passes full conversation_history so the
    agent remembers prior turns (e.g. "Bahria Town" from turn 1 when
    responding to a follow-up in turn 2).
    """
    message = state.get("current_message", "")
    conversation_history = state.get("conversation_history", [])
    criteria = extract_criteria(message)

    prefs = dict(state.get("property_preferences", {}))
    prefs.update({k: v for k, v in criteria.items() if v not in (None, "", [], {})})

    matches = recommend_properties(
        budget=prefs.get("budget"), city=prefs.get("city"), area=prefs.get("area"),
        bedrooms=prefs.get("bedrooms"), purpose=prefs.get("purpose"),
        property_category=prefs.get("property_category"),
        amenities=prefs.get("amenities"), investment_goals=prefs.get("investment_goals"),
    )
    answer = generate_grounded_answer(message, matches, prefs, conversation_history=conversation_history)
    # Strip any markdown formatting before TTS (asterisks, hashes, dashes as bullets)
    answer = _strip_markdown(answer)

    tool_outputs = dict(state.get("tool_outputs", {}))
    tool_outputs["recommendation"] = matches

    # BUG FIX: slot_filling_node's "use the property we already discussed"
    # fallback (see its docstring) reads tool_outputs["recommendation"] —
    # but tool_outputs is per-turn scratch (api_server.py resets it to {}
    # at the start of every turn) and was never carried into
    # property_preferences, which IS persisted across turns. So that
    # fallback only ever worked if the recommendation and the booking
    # request landed in the SAME message — the moment a caller said "show
    # me DHA Phase 6" in one turn and "book it" in the next, the property
    # was already lost and slot_filling_node had to ask "kaun si property
    # visit karni hai?" again despite it being obvious from context.
    # Persisting just the top match's title (not the whole match list) in
    # property_preferences keeps the session row small while fixing this.
    if matches and not matches[0].get("no_coverage"):
        prefs["_last_recommended_property"] = matches[0].get("title")
        # BUG FIX: only the title was being persisted, not the id — rag_node
        # needs the actual property_id to scope a follow-up semantic lookup
        # to the right property (see rag_node/retriever.py). Persisting it
        # alongside the title the same way, so it survives across turns via
        # crm_store's session_state the same as everything else here.
        prefs["_last_recommended_property_id"] = matches[0].get("id")

    client_id = state.get("user_profile", {}).get("client_id")
    if client_id:
        crm_store.update_client_preferences(client_id, criteria)
        crm_store.log_call_transcript(
            client_id=client_id, intent="query", raw_query=message,
            extracted_criteria=criteria, answer=answer, matches_count=len(matches),
        )

    return {
        "property_preferences": prefs, "tool_outputs": tool_outputs, "response": answer,
        "pending_question": None,  # this is a final answer, not a clarifying question
    }


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
        f"Aap ki visit {details.get('date')} ko {details.get('start_time')} baje ke liye book ho gayi hai."
        if result.get("success")
        else "Sorry, booking abhi complete nahi ho saki. Mein isko manually check karwa deti hoon, aur aapko confirm kar deti hoon."
    )
    # A successful booking completes what the caller came here to do — end
    # the conversation so session_state clears and the next call starts
    # fresh. A failure keeps the session open so they can retry without
    # re-supplying everything (event_id, property, etc. stay filled).
    return {
        "tool_outputs": tool_outputs, "appointment_status": status, "response": response,
        "conversation_ended": bool(result.get("success")), "pending_question": None,
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
        f"Aap ki visit {details.get('new_date')} ko {details.get('new_start')} baje ke liye reschedule ho gayi hai."
        if result.get("success") else "Sorry, reschedule abhi complete nahi ho saka. Mein isko manually check karwa deti hoon, aur aapko confirm kar deti hoon."
    )
    return {
        "tool_outputs": tool_outputs, "appointment_status": status, "response": response,
        "conversation_ended": bool(result.get("success")), "pending_question": None,
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

    # BUG FIX: result.get("error") is a raw technical string from
    # calendar_tool/appointment_manager (e.g. "Calendar delete error:
    # TypeError: Missing required parameter eventId") — that's exactly the
    # kind of internal detail that should never reach a live caller, in
    # any language, let alone read aloud by TTS in English. The real error
    # is still captured above via _log_appointment/create_manual_followup
    # for staff to investigate; the caller just gets a clean UrduLish
    # apology and a path forward.
    response = (
        "Aap ki appointment cancel ho gayi hai." if result.get("success")
        else "Sorry, cancellation abhi complete nahi ho saki. Mein isko manually check karwa deti hoon, "
             "aur aapko confirm kar deti hoon."
    )
    return {
        "tool_outputs": tool_outputs, "appointment_status": status, "response": response,
        "conversation_ended": bool(result.get("success")), "pending_question": None,
    }


@log_transition("seller")
def seller_node(state: GraphState) -> dict:
    return {
        "response": (
            "Jee bilkul, mein aapki listing publish karne mein madad kar sakti hoon. "
            "Bas property ka area, type, bedrooms, aur expected price bata dein, phir mein next step share karti hoon."
        ),
        "pending_question": None,
        "conversation_ended": False,
    }


@log_transition("complaint")
def complaint_node(state: GraphState) -> dict:
    return {
        "response": (
            "Jee, aap ka point samajh gayi. Sorry ke aap ko yeh experience hua. "
            "Kya mein aapko senior consultant se connect kar doon, ya aap chahen to mein property search ya booking mein madad karun?"
        ),
        "pending_question": None,
        "conversation_ended": False,
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
    # BUG FIX: appointment_manager now fires the notification email
    # asynchronously (see appointment_manager._send_email_async) so a slow
    # SMTP send can't block the spoken response — which means "email_sent"
    # is no longer known at this point in the turn, it's just been kicked
    # off. Say something that's true regardless of timing rather than
    # asserting delivery that hasn't necessarily happened yet.
    last_tool = state.get("tool_outputs", {})
    action_taken = any(
        k in last_tool and isinstance(last_tool[k], dict) and last_tool[k].get("success")
        for k in ("booking", "reschedule", "cancellation")
    )
    note = " Confirmation email bhi bhej di jayegi." if action_taken else ""
    return {"response": state.get("response", "") + note}


@log_transition("goodbye")
def goodbye_node(state: GraphState) -> dict:
    return {"response": "Thanks for calling, have a great day!", "conversation_ended": True, "pending_question": None}


# ---------------------------------------------------------------
# TASK 4 — VALIDATION NODES
# ---------------------------------------------------------------
SLOT_QUESTIONS = {
    "property_name": "kaun si property visit karni hai",
    "date": "kaunsa din theek rahega aap ke liye",
    "start_time": "kis time aana chahte hain",
    "end_time": "kis time tak visit khatam karni hai",
    "event_id": "apna booking reference number bata dein",
    "new_date": "naya kaunsa din theek rahega",
    "new_start": "naya start time kya hoga",
    "new_end": "naya end time kya hoga",
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
  "date": string in YYYY-MM-DD format or null — resolve relative dates using the reference date given below.
    Supported relative date terms (Urdu, Roman Urdu, English):
      aaj / today -> today's date
      kal / tomorrow / aane wala din -> tomorrow
      parson / day after tomorrow -> two days from today
      next Monday/Tuesday/etc. -> next occurrence of that weekday
  "start_time": string in 24-hour HH:MM format or null — convert 12-hour ("3 baje", "3 pm", "teen baje", "3 o'clock") to 24-hour.
  "end_time": string in 24-hour HH:MM format or null — if only start_time is found, set end_time = start_time + 1 hour automatically.
  "event_id": string or null — only if the customer references an existing booking/reference number

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
    # Checks tool_outputs first (same-turn case: property recommended and
    # booking requested in one message) and falls back to
    # property_preferences["_last_recommended_property"] (cross-turn case —
    # see recommendation_node's comment on why this is persisted there and
    # not in tool_outputs, which doesn't survive between turns).
    if intent == "book" and not details.get("property_name"):
        last_matches = state.get("tool_outputs", {}).get("recommendation")
        if last_matches:
            details["property_name"] = last_matches[0].get("title")
        else:
            last_property = state.get("property_preferences", {}).get("_last_recommended_property")
            if last_property:
                details["property_name"] = last_property

    # Auto-assign employee based on known city preference, if not already set
    if intent == "book" and not details.get("employee_email"):
        city = state.get("property_preferences", {}).get("city")
        emp = employee_data.get_employee_by_city(city)
        details["employee"] = emp["id"]
        details["employee_name"] = emp["name"]
        details["employee_email"] = emp["email"]

    # Reschedule flow: if the caller means the booking they just made, use
    # the latest successful appointment so we can ask for the new date/time
    # instead of forcing them to repeat the old booking reference.
    if intent == "reschedule" and not details.get("event_id"):
        client_id = state.get("user_profile", {}).get("client_id")
        recent_message = message.lower()
        if client_id and any(phrase in recent_message for phrase in ("isko", "is ko", "jo abhi", "abhi", "pichli", "previous", "purani", "this", "the one", "recent", "last one", "reschedule kar", "reschedule")):
            latest = crm_store.get_latest_appointment_for_client(client_id)
            if latest:
                details.setdefault("event_id", latest.get("event_id"))
                details.setdefault("property_name", latest.get("property_title"))
                details.setdefault("date", latest.get("date"))
                details.setdefault("start_time", latest.get("start_time"))
                details.setdefault("end_time", latest.get("end_time"))
                details.setdefault("employee_name", latest.get("employee"))
                details.setdefault("employee_email", latest.get("employee_email"))

    # Cancel flow: resolve the booking from the caller's own appointment
    # history using natural details first. This avoids forcing people to
    # hunt for a reference number before they can cancel.
    if intent == "cancel" and not details.get("event_id"):
        client_id = state.get("user_profile", {}).get("client_id")
        if client_id and details.get("property_name") and details.get("date"):
            existing = crm_store.get_appointment_by_client_details(
                client_id=client_id,
                property_title=details.get("property_name"),
                date=details.get("date"),
                start_time=details.get("start_time"),
                end_time=details.get("end_time"),
            )
            if existing:
                details.setdefault("event_id", existing.get("event_id"))
                details.setdefault("property_name", existing.get("property_title"))
                details.setdefault("date", existing.get("date"))
                details.setdefault("start_time", existing.get("start_time"))
                details.setdefault("end_time", existing.get("end_time"))
                details.setdefault("employee_name", existing.get("employee"))
                details.setdefault("employee_email", existing.get("employee_email"))

        # If the caller is referring to "the one I just booked" and didn't
        # repeat the booking details, fall back to the most recent booking
        # for this client. This is the common immediate-cancel case from the
        # UI transcript and keeps the agent from asking for details it should
        # already know from the latest appointment row.
        if client_id and not details.get("event_id"):
            recent_message = message.lower()
            if any(phrase in recent_message for phrase in ("abhi", "just booked", "jo abhi", "pichli", "previous", "purani", "recent", "last one", "jo abhi book")):
                latest = crm_store.get_latest_appointment_for_client(client_id)
                if latest:
                    details.setdefault("event_id", latest.get("event_id"))
                    details.setdefault("property_name", latest.get("property_title"))
                    details.setdefault("date", latest.get("date"))
                    details.setdefault("start_time", latest.get("start_time"))
                    details.setdefault("end_time", latest.get("end_time"))
                    details.setdefault("employee_name", latest.get("employee"))
                    details.setdefault("employee_email", latest.get("employee_email"))

    if intent == "cancel" and not details.get("event_id"):
        lookup_question = (
            "Zaroor! Mujhe apni booking ka property name, date aur time bata dein, "
            "phir main cancellation confirm kar deti hoon."
        )
        return {
            "appointment_details": details,
            "missing_slots": [f for f in ("property_name", "date", "start_time", "end_time") if not details.get(f)],
            "response": lookup_question,
            "pending_question": lookup_question,
        }

    missing = [f for f in required if not details.get(f)]

    if missing:
        questions = [SLOT_QUESTIONS.get(f, f) for f in missing]
        # Build a natural Roman UrduLish clarifying question
        if len(questions) == 1:
            response = f"Zaroor! Bas ek cheez bata dein — {questions[0]}?"
        else:
            response = "Zaroor! Kuch cheezein confirm karni hain — " + "، aur ".join(questions) + "?"
        return {
            "appointment_details": details, "missing_slots": missing, "response": response,
            "pending_question": response,
        }

    return {"appointment_details": details, "missing_slots": [], "pending_question": None}


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
        # BUG FIX: previously an availability-CHECK FAILURE (available=None
        # — Google Calendar unreachable, bad credentials, calendar not
        # shared with the service account, etc.) was treated identically to
        # a REAL CONFLICT (available=False, an actual competing event) —
        # both just told the caller "try a different time." But if the
        # check itself is broken, no time will ever succeed: the caller
        # gets stuck in an infinite loop proposing times against a system
        # that can't confirm anything, ever, and nobody operating this
        # agent ever sees why (the actual exception string was only ever
        # written into tool_outputs, which is per-turn scratch — never
        # logged, never surfaced).
        check_failed = result.get("available") is None
        if check_failed:
            print(f"[availability_check] Calendar check FAILED (not a real conflict) "
                  f"for {date} {start}-{end}: {result.get('error')}")

        retry_count = int(state.get("appointment_details", {}).get("_availability_retry_count", 0))

        if check_failed and retry_count >= 1:
            # Second consecutive failure of the CHECK ITSELF (not a real
            # conflict) — stop asking the caller for more times, that will
            # never resolve this. Hand off to a human instead of looping.
            client_id = state.get("user_profile", {}).get("client_id")
            if client_id:
                crm_store.create_manual_followup(
                    client_id,
                    reason=f"Calendar availability check repeatedly failed: {result.get('error')}",
                )
            return {
                "tool_outputs": tool_outputs,
                "missing_slots": [],
                "response": (
                    "Sorry, hamara scheduling system abhi thora issue kar raha hai. "
                    "Main aapki details save kar rahi hoon aur hamari team aapko "
                    "jald hi call karke visit confirm karegi."
                ),
                "pending_question": None,
                "appointment_details": {
                    **state.get("appointment_details", {}),
                    "_availability_retry_count": 0,
                },
            }

        conflicts = ", ".join(result.get("conflicting_events", [])) or "ek doosri booking"
        reason = (
            "mein abhi confirm nahi kar saki ke yeh slot free hai"
            if check_failed
            else f"is waqt {conflicts} ke saath clash ho raha hai"
        )
        # Clear the rejected time from appointment_details, not just flag it
        # missing — slot_filling_node only overwrites a field when it's
        # currently empty, so leaving the stale rejected time in place would
        # make the caller's next answer ("2pm to 3pm works") get silently
        # discarded and the same conflict re-checked forever.
        cleared_details = dict(details)
        cleared_details.pop(start_field, None)
        cleared_details.pop(end_field, None)
        cleared_details["_availability_retry_count"] = retry_count + 1 if check_failed else 0
        # BUG FIX: this was plain English ("Sorry, that slot conflicts with
        # X. Could you suggest a different time?") — a hard violation of the
        # system prompt's "NEVER respond in plain English" rule, and it was
        # the exact line a caller would hit on almost every double-booked
        # slot, so it wasn't a rare edge case.
        clarifying_response = f"Sorry, {reason}. Koi aur time bata dein?"
        return {
            "tool_outputs": tool_outputs,
            "appointment_details": cleared_details,
            "missing_slots": [start_field, end_field],
            "response": clarifying_response,
            "pending_question": clarifying_response,
        }

    return {"tool_outputs": tool_outputs, "missing_slots": [], "pending_question": None}


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
# ---------------------------------------------------------------
# LLM-BACKED ROUTING CLASSIFIER
# Used as fallback when keyword matching alone cannot determine
# if an Urdu-script STT transcript is a property search query or a FAQ.
# ---------------------------------------------------------------
ROUTE_CLASSIFIER_PROMPT = """You are a routing classifier for a real estate AI voice agent.
Given a customer's message (may be in English, Roman Urdu, or Urdu/Arabic script), 
decide if it is a PROPERTY SEARCH (looking for a specific property to buy/rent, asking about
locations, prices, bedrooms, apartments, houses, plots, villas) or a GENERAL FAQ 
(asking about deposit policies, contracts, agent info, process questions).

Respond with EXACTLY one word: PROPERTY or FAQ"""


def _is_property_search(message: str) -> bool:
    """
    Uses keyword matching first (fast, no API call).
    If no keywords match, falls back to LLM classification to catch
    Urdu-script STT transcriptions that don't contain known keywords.
    """
    if not message:
        return False
    msg_lower = message.lower()

    # Fast keyword path — covers most cases in English + Roman Urdu
    keywords = [
        "bedroom", "bed", "budget", "crore", "lakh", "price", "kamra", "ghar",
        "house", "apartment", "villa", "flat", "plot", "buy", "rent", "bahria",
        "dha", "clifton", "gulberg", "johar", "f-10", "islamabad", "karachi", "lahore",
        # BUG FIX: generic "show me listings / what do you have available"
        # questions had no keyword to match at all — they'd only ever be
        # caught if the LLM fallback classifier below happened to call them
        # PROPERTY, which was inconsistent. These are unambiguously property
        # search intent regardless of city/type specifics.
        "listing", "listings", "option", "options", "properties", "property",
        "dekha", "dikha", "dikhayen", "dekhayen",
        # Urdu script variants (including common STT mishearings)
        "بحریہ", "ڈی ایچ اے", "اپارٹمنٹ", "ڈیپارٹمنٹ", "ڈپارٹمنٹ",
        "مکان", "ولا", "پلاٹ", "بیڈ", "کروڑ", "لاکھ", "خریدنا", "کرایہ",
        "گھر", "فلیٹ", "کمرہ", "کمروں", "بار", "لسٹنگ", "پراپرٹی",
    ]
    if any(k in msg_lower for k in keywords):
        return True

    # LLM fallback for non-English, unusual STT output, or mixed scripts
    try:
        resp = client.chat.completions.create(
            model=GATEWAY_MODEL,
            messages=[
                {"role": "system", "content": ROUTE_CLASSIFIER_PROMPT},
                {"role": "user", "content": message},
            ],
            temperature=0,
        )
        return resp.choices[0].message.content.strip().upper() == "PROPERTY"
    except Exception:
        return False  # safe fallback: go to RAG rather than crash


def route_by_intent(state: GraphState) -> str:
    """
    Routes queries to recommendation (property search) or rag (FAQ).
    Uses LLM classifier as fallback for Urdu-script STT output.
    """
    intent = state.get("intent")
    if intent == "query":
        message = state.get("current_message", "")
        conversation_history = state.get("conversation_history", []) or []
        if _is_property_search(message) or _is_followup_property_query(message, conversation_history):
            return "recommendation"
        return "rag"
    return {
        "book": "slot_filling",
        "reschedule": "slot_filling",
        "cancel": "slot_filling",
        "seller": "seller",
        "complaint": "complaint",
        "goodbye": "goodbye",
        "safety": "safety",
        # A plain "hello" mid-call should NOT hang up. greeting_node only
        # emits its welcome response on a genuinely fresh session (see its
        # docstring), so a mid-call "hello" needs its own reply here via
        # greeting_reply_node — end the turn on that and keep the session
        # open for whatever the caller says next. Only an explicit
        # "goodbye" intent should route to the goodbye node and end the call.
        "greeting": "greeting_end",
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
    graph.add_node("greeting_reply", greeting_reply_node)
    graph.add_node("intent_detection", intent_detection_node)
    graph.add_node("rag", rag_node)
    graph.add_node("recommendation", recommendation_node)
    graph.add_node("slot_filling", slot_filling_node)
    graph.add_node("availability_check", availability_check_node)
    graph.add_node("booking", booking_node)
    graph.add_node("reschedule", reschedule_node)
    graph.add_node("cancellation", cancellation_node)
    graph.add_node("seller", seller_node)
    graph.add_node("complaint", complaint_node)
    graph.add_node("safety", safety_node)
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
            "seller": "seller",
            "complaint": "complaint",
            "safety": "safety",
            "greeting_end": "greeting_reply",
        },
    )
    graph.add_edge("greeting_reply", END)
    graph.add_edge("seller", END)
    graph.add_edge("complaint", END)
    graph.add_edge("safety", END)

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