"""
test_pronunciation.py
Day 3, Task 2 — Natural Speech Behaviors: TTS pronunciation check

Standalone TTS-only test. Feeds Fish Audio a list of Roman-Urdu words
and phrases used in Ayesha's system prompt (fillers, acknowledgements,
thinking pauses) and saves each as a separate numbered mp3, so you can
listen through fast and compare spellings side by side without
re-running STT/LLM every time.

Started this because "Ji zaroor" came out sounding like "Jai zaroor" —
Fish Audio reading "Ji" with English phonetics instead of the short
"jee" sound. Testing "Jee" as the fix, plus a few other words that
carry the same risk (short Roman-Urdu spellings that look like English
words/sounds to a model not specifically tuned for Urdu romanization).

Output: pronunciation_tests/ folder, one mp3 per line below, numbered
in order and named from the text itself (truncated, sanitized).

Run with venv active, from inside the voice-agent project folder:
    python test_pronunciation.py
"""

import os
import re
import time
from dotenv import load_dotenv, find_dotenv
from fish_audio_sdk import Session, TTSRequest

load_dotenv(find_dotenv())

FISH_API_KEY = os.environ.get("FISH_AUDIO_API_KEY")
FISH_REFERENCE_ID = os.environ.get("FISH_REFERENCE_ID", "16344fa6cc2a46a09825a0871cecc0a6")  # Saadia
OUTPUT_DIR = "pronunciation_tests"

# ---------------------------------------------------------------
# Test cases — single words/spelling variants first, then full
# phrases (context/momentum from surrounding words can change how a
# word renders vs testing it alone).
# ---------------------------------------------------------------

TEST_CASES = [
    # --- confirm: sentence-initial "mein" (broken) vs led-in "mein" (should be fine) ---
    "Mein aap ko details deti hoon.",
    "Jee, mein aap ko details deti hoon.",
    "Acha, mein abhi check kar rahi hoon.",
    "Ek second sir, mein calendar check kar rahi hoon.",
]


def sanitize_filename(text: str, index: int) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())[:40].strip("_")
    return f"{index:02d}_{slug}.mp3"


def synthesize(text: str, out_path: str):
    session = Session(FISH_API_KEY)
    audio_bytes = b""
    for chunk in session.tts(
        TTSRequest(text=text, reference_id=FISH_REFERENCE_ID),
        backend="s2.1-pro-free",
    ):
        audio_bytes += chunk
    with open(out_path, "wb") as f:
        f.write(audio_bytes)
    return len(audio_bytes)


def main():
    if not FISH_API_KEY:
        raise RuntimeError("FISH_AUDIO_API_KEY not found — check your .env file")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Generating {len(TEST_CASES)} pronunciation test clips into ./{OUTPUT_DIR}/\n")

    for i, text in enumerate(TEST_CASES, start=1):
        filename = sanitize_filename(text, i)
        out_path = os.path.join(OUTPUT_DIR, filename)

        print(f"[{i:02d}/{len(TEST_CASES)}] \"{text}\"")
        try:
            size = synthesize(text, out_path)
            print(f"         -> {filename} ({round(size/1024, 1)} KB)\n")
        except Exception as e:
            print(f"         FAILED: {e}\n")

        # Small delay to avoid hammering the free-tier rate limit
        time.sleep(0.5)

    print("=" * 55)
    print(f"Done. Listen through {OUTPUT_DIR}/ in order — files are")
    print("numbered to match the list above, so pairs like 01_ji vs")
    print("02_jee sit next to each other for direct comparison.")
    print("=" * 55)


if __name__ == "__main__":
    main()