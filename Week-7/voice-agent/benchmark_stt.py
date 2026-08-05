"""
benchmark_stt.py
Day 3, Task 1 — Streaming Voice Pipeline (Stage 2 of 3: STT)

Sends a pre-recorded audio clip to Deepgram's REST API and measures
processing time + returns the transcript. This is a proxy for true
streaming latency (real-time mic streaming sends audio in chunks and
gets partial results back continuously) — this test tells us how fast
Deepgram's model itself is before we wire up live streaming.

Uses the plain REST API via `requests` rather than the SDK, to avoid
SDK-version syntax mismatches — Deepgram's REST endpoint is stable and
simple enough that this is the more reliable choice for a benchmark.

Run with venv active, from inside the voice-agent project folder:
    python benchmark_stt.py
"""

import os
import time
import requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
AUDIO_FILE_PATH = "Voice-agent-clip.m4a"  # one level up from voice-agent/

# Switched from model=nova-3&language=multi to the dedicated monolingual
# Urdu model (language=ur). `multi` is Nova-3's code-switching mode for
# mixed-language speech, but it appears to default toward Hindi rather
# than Urdu for South Asian audio (they're phonetically close but use
# different scripts) — our first test came back in Devanagari with a
# clearly mis-heard opening phrase. Nova-3 added dedicated monolingual
# Urdu support separately from the multi mode; this should transcribe
# in proper Urdu (Arabic) script and use Urdu-specific acoustic/vocab
# modeling instead of guessing via Hindi.
DEEPGRAM_URL = (
    "https://api.deepgram.com/v1/listen"
    "?model=nova-3&smart_format=true&language=ur"
)


def transcribe_file(file_path: str) -> dict:
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY not found — check your .env file")

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found at: {file_path}")

    with open(file_path, "rb") as f:
        audio_data = f.read()

    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "audio/m4a",
    }

    start = time.perf_counter()
    response = requests.post(DEEPGRAM_URL, headers=headers, data=audio_data)
    end = time.perf_counter()

    if response.status_code != 200:
        raise RuntimeError(f"Deepgram error {response.status_code}: {response.text}")

    result = response.json()
    transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"]
    confidence = result["results"]["channels"][0]["alternatives"][0]["confidence"]

    return {
        "transcript": transcript,
        "confidence": confidence,
        "processing_time_ms": round((end - start) * 1000, 1),
        "audio_size_kb": round(len(audio_data) / 1024, 1),
    }


def main():
    print(f"Transcribing: {AUDIO_FILE_PATH}\n")
    result = transcribe_file(AUDIO_FILE_PATH)

    print(f"Audio size:        {result['audio_size_kb']} KB")
    print(f"Processing time:   {result['processing_time_ms']} ms")
    print(f"Confidence:        {result['confidence']:.2%}")
    print(f"\nTranscript:\n  \"{result['transcript']}\"")

    print("\n" + "=" * 50)
    print("Note: this is REST/file-based processing time, not true streaming")
    print("latency. Real streaming (websocket) sends partial transcripts as")
    print("you speak — that number will be lower. This confirms the model")
    print("itself is fast enough before we wire up live streaming.")
    print("=" * 50)


if __name__ == "__main__":
    main()
