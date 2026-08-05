"""
test_hybrid_script.py
Day 3, Task 2 — Natural Speech Behaviors: hybrid script test (Option B)

3-way comparison per phrase:
  1. Roman-Urdu (current approach) — already tested, has known issues
     (e.g. "main" read as English, "budget" misread)
  2. Full native Urdu script — already tested, handles Urdu words
     correctly but loses nothing on loanwords either, per your last
     test — but worth re-confirming side by side with #3
  3. HYBRID — Urdu words in native Nastaliq script, English/loanwords
     (property terms, "DHA", "apartment", "budget", "gym" etc.) left
     in Latin script. Generated via a transliteration LLM call rather
     than a fixed dictionary, since there's no reliable deterministic
     Roman-Urdu -> Nastaliq mapping for arbitrary vocabulary.

IMPORTANT: the hybrid step adds one extra LLM round-trip (transliterate
Roman -> hybrid script) before TTS. That's an added latency cost on
top of an already-over-budget pipeline (see benchmark_tts.py notes).
Fine for this A/B quality test; if hybrid wins on quality, the
latency tradeoff needs a real decision before wiring it into the live
pipeline — e.g. whether the main conversational LLM can be prompted to
output hybrid script directly instead, skipping the second call.

Output: hybrid_comparison/ folder, three files per phrase:
  01_roman_....mp3
  01_urdu_....mp3
  01_hybrid_....mp3

Run with venv active, from inside the voice-agent project folder:
    python test_hybrid_script.py
"""

import os
import re
import time
from dotenv import load_dotenv, find_dotenv
from openai import OpenAI
from fish_audio_sdk import Session, TTSRequest

load_dotenv(find_dotenv())

FISH_API_KEY = os.environ.get("FISH_AUDIO_API_KEY")
FISH_REFERENCE_ID = os.environ.get("FISH_REFERENCE_ID", "16344fa6cc2a46a09825a0871cecc0a6")
OUTPUT_DIR = "hybrid_comparison"

USE_GROQ = os.environ.get("USE_GROQ", "true").lower() == "true"
if USE_GROQ:
    LLM_BASE_URL = "https://api.groq.com/openai/v1"
    LLM_API_KEY = os.environ.get("GROQ_API_KEY")
    LLM_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
else:
    LLM_BASE_URL = "https://llm.netixsol.com/v1"
    LLM_API_KEY = os.environ.get("GATEWAY_API_KEY")
    LLM_MODEL = os.environ.get("GATEWAY_MODEL", "fast")

TRANSLITERATE_SYSTEM_PROMPT = """You convert Roman-Urdu (UrduLish) text into mixed-script text for a
text-to-speech engine. Rules:

1. Convert every genuinely Urdu word into native Urdu (Nastaliq) script.
2. Leave English words and loanwords exactly as they are, in Latin script —
   do NOT transliterate them into Urdu script. This includes property/real
   estate terms like DHA, apartment, budget, gym, property, available, sir,
   and any other English-origin word.
3. Preserve word order and punctuation exactly.
4. Output ONLY the converted text. No explanation, no quotes, no preamble.

Example:
Input: DHA mein teen bedroom apartment available hai
Output: ڈی ایچ اے میں تین بیڈروم apartment available ہے

Example:
Input: Ji zaroor, main abhi dekhati hoon
Output: جی ضرور، میں ابھی دیکھتی ہوں"""

# Same pairs as test_script_comparison.py, so all three approaches are
# tested on identical phrases.
TEST_PHRASES = [
    ("Ji zaroor", "جی ضرور"),
    ("Main abhi dekhati hoon", "میں ابھی دیکھتی ہوں"),
    ("Ji bilkul, ek second sir", "جی بالکل، ایک سیکنڈ سر"),
    ("Acha, samajh gayi main aap ki requirement", "اچھا، سمجھ گئی میں آپ کی ریکوائرمنٹ"),
    ("DHA mein teen bedroom apartment available hai", "ڈی ایچ اے میں تین بیڈروم اپارٹمنٹ available ہے"),
    ("Budget kitna hai aap ka is property ke liye", "بجٹ کتنا ہے آپ کا اس پراپرٹی کے لیے"),
]


def transliterate(roman_text: str, client: OpenAI) -> str:
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": TRANSLITERATE_SYSTEM_PROMPT},
            {"role": "user", "content": roman_text},
        ],
        stream=False,
    )
    return response.choices[0].message.content.strip()


def sanitize_filename(text: str, index: int, tag: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())[:30].strip("_")
    if not slug:
        slug = "text"
    return f"{index:02d}_{tag}_{slug}.mp3"


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
    if not LLM_API_KEY:
        raise RuntimeError("LLM API key not found — check GROQ_API_KEY/GATEWAY_API_KEY in .env")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=0)

    print(f"Generating 3-way comparison for {len(TEST_PHRASES)} phrases into ./{OUTPUT_DIR}/\n")

    for i, (roman_text, urdu_text) in enumerate(TEST_PHRASES, start=1):
        # Get the hybrid version via transliteration LLM call
        print(f"[{i:02d}] Transliterating: \"{roman_text}\"")
        try:
            hybrid_text = transliterate(roman_text, client)
            print(f"     -> hybrid: \"{hybrid_text}\"")
        except Exception as e:
            print(f"     TRANSLITERATION FAILED: {e}")
            hybrid_text = None

        variants = [("roman", roman_text), ("urdu", urdu_text)]
        if hybrid_text:
            variants.append(("hybrid", hybrid_text))

        for tag, text in variants:
            filename = sanitize_filename(text, i, tag)
            out_path = os.path.join(OUTPUT_DIR, filename)
            try:
                size = synthesize(text, out_path)
                print(f"     [{tag}] -> {filename} ({round(size/1024, 1)} KB)")
            except Exception as e:
                print(f"     [{tag}] FAILED: {e}")
            time.sleep(0.5)

        print()

    print("=" * 55)
    print(f"Done. In {OUTPUT_DIR}/, compare each numbered trio:")
    print("  01_roman_... / 01_urdu_... / 01_hybrid_...")
    print("Focus on the mixed-language phrases (05, 06) — does hybrid")
    print("get BOTH the Urdu words right (like native did) AND the")
    print("English/loanwords right (like roman did)? That's the goal.")
    print("=" * 55)


if __name__ == "__main__":
    main()