"""
agent.py
=========

Task 3: Wire the structured retrieval tools into a LangChain tool-calling
agent

## Why create_tool_calling_agent + AgentExecutor
## The grounding check the risk with any tool-calling agent is that the model calls a tool, gets
a real number back, but then also invents a *different* number in its
prose response (paraphrasing loosely, or blending the tool result with
something from its own training data about AFL). A tool being available
doesn't guarantee it was believed.

`GroundingLogger` below hooks into the agent's callback system and records
every tool call's raw input/output alongside the final text answer, so you
can grep the final answer for the numbers that came out of the tool. This
is a manual-inspection aid, not an automatic guarantee — Task 5's report
is where you actually eyeball a sample and confirm agreement.
"""

import os
from dotenv import load_dotenv, find_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.chat_history import InMemoryChatMessageHistory as ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

from system_prompt import SYSTEM_PROMPT
from tools import ALL_TOOLS

load_dotenv(find_dotenv())

GATEWAY_BASE_URL = os.environ.get("GATEWAY_BASE_URL", "https://llm.netixsol.com/v1")
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY")
MODEL_ALIAS = os.environ.get("AFL_AGENT_MODEL", "smart")  


class GroundingLogger(BaseCallbackHandler):
    """Records every tool call's input/output for this run so the final
    answer can be checked against real tool results afterward. Kept
    intentionally simple (a plain list) rather than writing to a database —
    for a bootcamp-scale eval, being able to `print(logger.calls)` after a
    run matters more than infrastructure."""

    def __init__(self):
        self.calls = []

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.calls.append({"tool": serialized.get("name"), "input": input_str, "output": None})

    def on_tool_end(self, output, **kwargs):
        if self.calls:
            self.calls[-1]["output"] = str(output)


# ---------------------------------------------------------------------------
# Prompt: system scope + a placeholder for prior turns (Task 4 memory) +
# the current human input + the agent_scratchpad LangChain needs to
# record intermediate tool-calling steps within a single turn.
# ---------------------------------------------------------------------------
prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder("chat_history", optional=True),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

llm = ChatOpenAI(
    base_url=GATEWAY_BASE_URL,
    api_key=GATEWAY_API_KEY,
    model=MODEL_ALIAS,
    temperature=0,  # deterministic-leaning: stat lookups shouldn't be "creative"
)

agent = create_tool_calling_agent(llm, ALL_TOOLS, prompt)
agent_executor = AgentExecutor(agent=agent, tools=ALL_TOOLS, verbose=False)

# ---------------------------------------------------------------------------
# Task 4: Memory
# In-memory chat history keyed by session_id, same pattern as your travel
# bot's RunnableWithMessageHistory. One store per process; swap for a
# persistent store (Redis, a DB table) if this needs to survive restarts.
# ---------------------------------------------------------------------------
_session_store = {}


def _get_session_history(session_id: str) -> ChatMessageHistory:
    if session_id not in _session_store:
        _session_store[session_id] = ChatMessageHistory()
    return _session_store[session_id]


agent_with_memory = RunnableWithMessageHistory(
    agent_executor,
    _get_session_history,
    input_messages_key="input",
    history_messages_key="chat_history",
)


def chat(message: str, session_id: str = "default", logger: GroundingLogger = None) -> dict:
    """Single entry point used by both the demo scripts and the eval script.
    Returns the final answer text plus (if a logger was passed) the tool
    calls made during this turn, for the grounding check."""
    callbacks = [logger] if logger else []
    result = agent_with_memory.invoke(
        {"input": message},
        config={"configurable": {"session_id": session_id}, "callbacks": callbacks},
    )
    return {
        "answer": result["output"],
        "tool_calls": logger.calls if logger else None,
    }


if __name__ == "__main__":
    if not GATEWAY_API_KEY:
        print(
            "GATEWAY_API_KEY not set. Add it to a .env file next to this script "
            
        )
    else:
        logger = GroundingLogger()
        out = chat("How many disposals did Dustin Martin have in round 14, 2017?", logger=logger)
        print("ANSWER:", out["answer"])
        print("TOOL CALLS:", out["tool_calls"])
