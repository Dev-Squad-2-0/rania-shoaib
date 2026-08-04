"""
benchmark_llm_streaming.py
Day 3, Task 1 — Streaming Voice Pipeline (Stage 1 of 3: LLM)

Measures time-to-first-token (TTFT) and total generation time for the
gateway LLM, streaming the response instead of waiting for it to finish.
This isolates the LLM's contribution to the 2-second latency budget
before STT or TTS are added into the loop.

Run with venv active, from inside the voice-agent project folder
(so find_dotenv() picks up the local .env):
    python benchmark_llm_streaming.py
"""

import os
import time
from dotenv import load_dotenv, find_dotenv
from openai import OpenAI

load_dotenv(find_dotenv())

# Toggle between the company gateway and a direct Groq connection via env var:
#   USE_GROQ=true  -> hits Groq directly (isolates Groq's real speed from
#                      gateway routing/quota issues)
#   unset/false     -> hits the company gateway as before
USE_GROQ = os.environ.get("USE_GROQ", "false").lower() == "true"

if USE_GROQ:
    API_BASE_URL = "https://api.groq.com/openai/v1"
    API_KEY = os.environ.get("GROQ_API_KEY")
    MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
else:
    API_BASE_URL = "https://llm.netixsol.com/v1"
    API_KEY = os.environ.get("GATEWAY_API_KEY")
    MODEL = os.environ.get("GATEWAY_MODEL", "fast")

SYSTEM_PROMPT = (
    "You are a real estate assistant speaking to a customer over the phone. "
    "Reply in Roman-script UrduLish only, no Nastaliq script, no emojis. "
    "Keep responses short and natural, like a real phone conversation."
)

TEST_QUERIES = [
    "Assalam-o-Alaikum, mujhe DHA mein 3 bed apartment chahiye budget 3 crore tak.",
    "Kya installment plan available hai is property par?",
    "Theek hai, Saturday 4 baje visit book kar dein.",
    "Bahria Town mein kya options hain 2 crore budget mein?",
    "Rent pe 2 bed apartment chahiye Gulberg mein.",
    "Security deposit kitna lagta hai?",
]


def stream_and_measure(query: str) -> dict:
    # max_retries=0: by default the OpenAI SDK silently retries 429s with
    # backoff, which hides real rate-limit errors inside what looks like
    # normal latency (this is exactly what caused the 8-12s "TTFT" spikes
    # seen in earlier runs — those were retry backoff sleeps, not real
    # model latency). Disabling retries here means a 429 raises immediately
    # and visibly instead of silently inflating the timing.
    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY, max_retries=0)

    start = time.perf_counter()
    first_token_time = None
    full_response = ""

    stream = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            if first_token_time is None:
                first_token_time = time.perf_counter()
            full_response += delta

    end = time.perf_counter()

    return {
        "query": query,
        "ttft_ms": round((first_token_time - start) * 1000, 1) if first_token_time else None,
        "total_ms": round((end - start) * 1000, 1),
        "response": full_response,
    }


def main():
    print(f"Benchmarking model '{MODEL}' via {'Groq direct' if USE_GROQ else 'company gateway'} — streaming TTFT\n")

    # Untimed warm-up call: absorbs connection/TLS handshake cost so the
    # timed runs below reflect steady-state latency, which is what a real
    # phone call experiences after the first turn.
    print("Warming up connection (not counted in results)...")
    try:
        stream_and_measure(TEST_QUERIES[0])
        print("Warm-up done.\n")
    except Exception as e:
        print(f"Warm-up failed (continuing anyway): {e}\n")

    results = []

    for i, query in enumerate(TEST_QUERIES, 1):
        try:
            result = stream_and_measure(query)
        except Exception as e:
            print(f"[{i}/{len(TEST_QUERIES)}] FAILED: {e}\n")
            continue
        results.append(result)
        print(f"[{i}/{len(TEST_QUERIES)}] TTFT={result['ttft_ms']}ms | total={result['total_ms']}ms")
        print(f"    Q: {query}")
        print(f"    A: {result['response'][:100]}...\n")
        time.sleep(2)  # small gap between requests to stay under Groq's free-tier RPM limit

    ttfts = [r["ttft_ms"] for r in results if r["ttft_ms"] is not None]
    avg_ttft = sum(ttfts) / len(ttfts) if ttfts else None

    print("=" * 50)
    print(f"Average time-to-first-token: {avg_ttft:.1f}ms" if avg_ttft else "No successful runs")
    print(f"Budget check: LLM stage should leave room for STT (~300ms) + TTS (~300ms)")
    print(f"  -> LLM TTFT should ideally stay under ~1000ms to keep total pipeline under 2s")
    print("=" * 50)


if __name__ == "__main__":
    main()