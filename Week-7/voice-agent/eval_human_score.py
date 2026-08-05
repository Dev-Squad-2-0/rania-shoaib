"""
eval_human_score.py
Day 3, Task 5 — Human Evaluation

Runs a conversation scenario once, measuring per-turn time-to-first-
token automatically (latency is scored objectively, not by ear), then
writes a plain-text record file with the full transcript plus blank
score fields for the four human-judged criteria (naturalness,
persuasiveness, fluency, conversation flow) per the rubric in
eval_rubric.md.

Reuses the exact SYSTEM_PROMPT and retry wrapper from
test_conversation_flow.py so scores reflect the same prompt you've
been testing all along — no duplicate logic to keep in sync.

Defaults to the standard test_conversation_flow scenario (4 turns,
cheapest option) since this makes real API calls and you're rate
limited. Swap the import + CALLER_TURNS line at the bottom to score a
different scenario (test_context_memory, test_objection_handling)
when you actually want to.

Run with venv active, from inside the voice-agent project folder:
    python eval_human_score.py
"""

import os
import re
import time
from datetime import datetime
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

from test_convo_flow import SYSTEM_PROMPT, CALLER_TURNS

OUTPUT_DIR = "eval_records"


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


def run_and_record(turns: list[str], scenario_label: str):
    if not LLM_API_KEY:
        raise RuntimeError("LLM API key not found — check GROQ_API_KEY/GATEWAY_API_KEY in .env")

    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=0)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    transcript_lines = []
    latencies = []

    for turn_num, caller_text in enumerate(turns, start=1):
        print(f"\n{'='*55}")
        print(f"TURN {turn_num}")
        print(f"{'='*55}")
        print(f"CALLER: {caller_text}")
        transcript_lines.append(f"CALLER: {caller_text}")

        messages.append({"role": "user", "content": caller_text})

        start = time.monotonic()
        first_token_time = None
        stream = call_with_retry(client, model=LLM_MODEL, messages=messages, stream=True)

        full_response = ""
        print("AYESHA: ", end="", flush=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                if first_token_time is None:
                    first_token_time = time.monotonic() - start
                print(delta, end="", flush=True)
                full_response += delta
        print()

        ttft = first_token_time if first_token_time is not None else 0.0
        latencies.append(ttft)
        transcript_lines.append(f"AYESHA ({ttft:.2f}s TTFT): {full_response}")

        messages.append({"role": "assistant", "content": full_response})

    avg_ttft = sum(latencies) / len(latencies) if latencies else 0.0

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    record_path = os.path.join(OUTPUT_DIR, f"{scenario_label}_{timestamp}.txt")

    with open(record_path, "w", encoding="utf-8") as f:
        f.write(f"EVAL RECORD — {scenario_label}\n")
        f.write(f"Model: {LLM_MODEL} ({'Groq' if USE_GROQ else 'Gateway'})\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"{'='*55}\n\n")
        f.write("\n\n".join(transcript_lines))
        f.write(f"\n\n{'='*55}\n")
        f.write("LATENCY (auto-measured, per rubric)\n")
        f.write(f"{'='*55}\n")
        for i, t in enumerate(latencies, start=1):
            f.write(f"Turn {i} TTFT: {t:.2f}s\n")
        f.write(f"Average TTFT: {avg_ttft:.2f}s\n")
        latency_score = 5 if avg_ttft < 1 else 3 if avg_ttft < 2 else 1
        f.write(f"Latency score (auto per rubric): {latency_score}/5\n")
        f.write(f"\n{'='*55}\n")
        f.write("HUMAN SCORES (fill in after listening/reading, see eval_rubric.md)\n")
        f.write(f"{'='*55}\n")
        f.write("Naturalness (1-5):    \n")
        f.write("Persuasiveness (1-5): \n")
        f.write("Fluency (1-5):        \n")
        f.write("Conversation Flow (1-5): \n")
        f.write("Notes: \n")

    print(f"\n{'='*55}")
    print(f"Record saved: {record_path}")
    print(f"Average TTFT: {avg_ttft:.2f}s (auto latency score: {latency_score}/5)")
    print("Open the file and fill in the four human-scored fields.")
    print(f"{'='*55}")


if __name__ == "__main__":
    run_and_record(CALLER_TURNS, scenario_label="conversation_flow")