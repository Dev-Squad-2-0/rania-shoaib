"""
test_objection_handling.py
Day 3, Task 4 — Objection Handling

Runs six short, independent two-turn scenarios (a basic inquiry
followed by one objection) covering all six objection categories:
price, trust, location, investment, builder, maintenance.

Each scenario is a fresh conversation (no shared history between
scenarios) since the goal here is to check the objection-handling
pattern itself, not memory continuity — that's covered separately in
test_context_memory.py.

Watch for, per scenario: does the response empathize before reframing
(not argue or dismiss)? Does it avoid inventing facts/guarantees where
retrieved data would be needed? Does it stay warm and move toward a
next step rather than getting defensive?
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

from test_convo_flow import SYSTEM_PROMPT

# ---------------------------------------------------------------
# Each scenario: (label, [inquiry turn, objection turn])
# ---------------------------------------------------------------
SCENARIOS = [
    ("PRICE", [
        "Mujhe DHA mein do bedroom apartment chahiye.",
        "Yeh toh bahut mehnga hai, mera budget itna nahi hai.",
    ]),
    ("TRUST", [
        "Aap logo ke paas DHA mein properties hain?",
        "Aap logo par bharosa kyun karoon, pehle kabhi naam nahi suna.",
    ]),
    ("LOCATION", [
        "Mujhe Gulshan mein ek ghar dikhayein.",
        "Yeh area mujhe theek nahi lagta, thora unsafe sa hai.",
    ]),
    ("INVESTMENT", [
        "Main DHA mein ek plot investment ke liye lena chahta hoon.",
        "Kya yeh acha investment hai, price badhega aage?",
    ]),
    ("BUILDER", [
        "Yeh naya project kis builder ka hai?",
        "Yeh builder acha hai? Pehle kabhi kaam dekha nahi maine.",
    ]),
    ("MAINTENANCE", [
        "Mujhe is apartment complex ke baare mein bataiye.",
        "Maintenance charges kitne hain har mahine?",
    ]),
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


def run_scenario(client, label, turns):
    print(f"\n{'='*55}")
    print(f"OBJECTION TYPE: {label}")
    print(f"{'='*55}")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for turn_num, caller_text in enumerate(turns, start=1):
        print(f"\nCALLER: {caller_text}")
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


def main():
    if not LLM_API_KEY:
        raise RuntimeError("LLM API key not found — check GROQ_API_KEY/GATEWAY_API_KEY in .env")

    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=0)

    for label, turns in SCENARIOS:
        run_scenario(client, label, turns)

    print(f"\n{'='*55}")
    print("All objection scenarios complete.")
    print("For each: empathize-then-reframe, not argue? No invented")
    print("facts/guarantees (trust/investment/builder/maintenance)?")
    print("Stays warm, moves toward a next step?")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()