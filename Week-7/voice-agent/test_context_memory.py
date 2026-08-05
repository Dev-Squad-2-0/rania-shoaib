"""
test_context_memory.py
Day 3, Task 3 — Context Memory Support

Same harness as test_conversation_flow.py, but the caller turns are
deliberately non-linear: budget stated early, then a gap of unrelated
turns, then a callback that requires resolving a reference ("us se
sasti") back to something said several turns earlier — not just the
immediately preceding turn.

Watch for: does Ayesha correctly recall the budget without re-asking?
Does she correctly identify which property "us se sasti" refers to,
or does she guess wrong / ask the caller to repeat themselves?
"""

import os
import re
import time
from dotenv import load_dotenv, find_dotenv
from openai import OpenAI, RateLimitError

load_dotenv(find_dotenv())

USE_GROQ = os.environ.get("USE_GROQ", "true").lower() == "true"
if USE_GROQ:
    LLM_BASE_URL = "https://api.groq.com/openai/v1"
    LLM_API_KEY = os.environ.get("GROQ_API_KEY")
    LLM_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
else:
    LLM_BASE_URL = "https://llm.netixsol.com/v1"
    LLM_API_KEY = os.environ.get("GATEWAY_API_KEY")
    LLM_MODEL = os.environ.get("GATEWAY_MODEL", "fast")

# Import the shared persona prompt from test_conversation_flow.py once
# you've synced your latest edits there — for now, paste the current
# SYSTEM_PROMPT in directly (same as test_conversation_flow.py).
from test_convo_flow import SYSTEM_PROMPT

# ---------------------------------------------------------------
# Non-linear scenario: budget stated turn 1, then two unrelated
# detour turns, then a callback referencing the budget AND a
# specific property mentioned mid-conversation — tests both
# long-range recall and pronoun/reference resolution.
# ---------------------------------------------------------------

CALLER_TURNS = [
    "Assalam o alaikum, budget 3 crore hai, DHA mein ghar dekhna hai.",
    "Aap logo ka office kahan hai, main visit kar sakta hoon kya?",
    "Achha theek hai. DHA phase 5 mein sabse achi property kaunsi hai, price ke saath bataiye.",
    "Us se sasti koi option hai?",
]

def call_with_retry(client, max_attempts=4, **kwargs):
    for attempt in range(1, max_attempts + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            if attempt == max_attempts:
                raise
            msg = str(e)
            match = re.search(r"try again in ([\d.]+)s", msg)
            wait = float(match.group(1)) + 2 if match else 10.0
            print(f"\n  [rate limited, waiting {wait:.1f}s before retry {attempt}/{max_attempts - 1}...]")
            time.sleep(wait)


def run_conversation(turns: list[str]):
    if not LLM_API_KEY:
        raise RuntimeError("LLM API key not found — check GROQ_API_KEY/GATEWAY_API_KEY in .env")

    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=0)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for turn_num, caller_text in enumerate(turns, start=1):
        print(f"\n{'='*55}")
        print(f"TURN {turn_num}")
        print(f"{'='*55}")
        print(f"CALLER: {caller_text}")

        messages.append({"role": "user", "content": caller_text})

        stream = call_with_retry(client, model=LLM_MODEL, messages=messages, stream=True)

        full_response = ""
        print("AYESHA: ", end="", flush=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                print(delta, end="", flush=True)
                full_response += delta
        print()

        messages.append({"role": "assistant", "content": full_response})

    print(f"\n{'='*55}")
    print("Conversation complete.")
    print("Check: does turn 3 recall the 3 crore budget from turn 1")
    print("without re-asking? Does turn 4 correctly resolve 'us se")
    print("sasti' to whatever specific option was named in turn 3,")
    print("not a generic/wrong property?")
    print(f"{'='*55}")


if __name__ == "__main__":
    run_conversation(CALLER_TURNS)