"""
graph_state.py
Week 7, Day 5, Task 1 — LangGraph State Design

One shared state object flows through every node in agent_graph.py.
Each node reads what it needs and returns a partial dict of updates,
which LangGraph merges back into the state automatically.

Design notes on how this maps to the existing (Week 6/7) codebase:

- property_preferences uses the exact same shape query_agent.extract_criteria()
  already produces (budget/city/area/bedrooms/purpose/amenities/investment_goals),
  so the Recommendation node can pass state["property_preferences"] straight
  into recommend_properties(**...) with no translation layer.
- user_profile mirrors what crm_store.get_or_create_client() returns/needs
  (id, name, phone) — the graph resolves this once at Greeting and every
  later node just reads it instead of re-asking for the phone number.
- appointment_status uses the same vocabulary as crm_store's
  appointment_history.status column: booked / rescheduled / cancelled / failed.
- tool_outputs is a scratch space for whatever the last tool call returned
  (RAG results, recommendation matches, booking result) so downstream nodes
  (e.g. Email, after Booking) can see it without re-running the tool.
"""

from typing import TypedDict, Optional, List, Dict, Any
from langgraph.graph.message import add_messages
from typing_extensions import Annotated


class GraphState(TypedDict, total=False):
    # --- Conversation history ---
    # Annotated with add_messages so LangGraph appends rather than overwrites
    # on every node's return, the same way RunnableWithMessageHistory did in
    # the Week 5 Travel Concierge Bot, just built into the graph itself now.
    conversation_history: Annotated[List[Dict[str, Any]], add_messages]

    # The specific utterance this turn is responding to — set at the top of
    # each turn, read by Intent Detection and any node that needs raw text.
    current_message: str

    # --- User profile ---
    # {"client_id": int, "name": str, "phone": str}
    # Populated once by Greeting via crm_store.get_or_create_client(), then
    # just carried forward — nothing after Greeting should need to re-derive it.
    user_profile: Dict[str, Any]

    # --- Property preferences / budget ---
    # Exact shape of query_agent.extract_criteria()'s return:
    # {"budget", "city", "area", "bedrooms", "purpose", "amenities", "investment_goals"}
    # Kept as its own top-level field (not buried in tool_outputs) because
    # Recommendation, RAG routing, and Booking all read from it directly.
    property_preferences: Dict[str, Any]

    # --- Intent ---
    # One of: "greeting" | "query" | "book" | "reschedule" | "cancel" | "goodbye"
    # Set by the Intent Detection node; read by the conditional-edge router
    # to decide which node runs next.
    intent: str

    # --- Tool outputs ---
    # Scratch space for whatever the last tool call produced. Namespaced by
    # tool so nothing overwrites another tool's result:
    #   {"rag": {...}, "recommendation": [...], "booking": {...}, ...}
    tool_outputs: Dict[str, Any]

    # --- Appointment status ---
    # "booked" | "rescheduled" | "cancelled" | "failed" | None
    # Mirrors crm_store.appointment_history.status vocabulary exactly.
    appointment_status: Optional[str]

    # --- Appointment slot data ---
    # Logistics needed by appointment_manager.py's functions, distinct from
    # property_preferences (which is about *what* they want, not *when*).
    # Populated incrementally by slot_filling_node in agent_graph.py as the
    # caller answers clarifying questions across turns.
    appointment_details: Dict[str, Any]

    # Task 4 — Validation: fields slot_filling_node determined are still
    # missing for the current intent. Non-empty means the graph should ask
    # a clarifying question this turn instead of proceeding to book/
    # reschedule/cancel.
    missing_slots: List[str]

    # Task 5 — State Logging: one entry per node executed this turn,
    # {"node": str, "timestamp": str, "intent": str}. Appended to, never
    # overwritten, so a full multi-turn conversation keeps its whole trace.
    execution_trace: List[Dict[str, Any]]

    # --- What the agent says back this turn ---
    response: str

    # Internal control flag: set True by the Goodbye node so the caller
    # (whatever drives the graph loop) knows to stop invoking it again.
    conversation_ended: bool