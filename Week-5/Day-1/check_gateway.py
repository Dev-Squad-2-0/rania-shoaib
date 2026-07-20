"""
Quick diagnostic: figure out what protocol your internship's gateway
speaks, and what model name it expects, before touching the real agent
script. Run this locally (I can't reach the gateway from my sandbox).

    pip install openai
    export GATEWAY_API_KEY=your-key-here
    python check_gateway.py
"""

import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_URL = "https://llm.netixsol.com/v1"
API_KEY = os.environ.get("GATEWAY_API_KEY")

# These are the gateway's own aliases, not raw provider model names.
# Try "smart" first; if it errors or behaves oddly, try the others.
CANDIDATE_MODEL_NAMES = [
    "smart",
    "reasoner",
    "smart-lite",
    "fast",
    "coder",
    "fast-small",
]

if not API_KEY:
    print("Set GATEWAY_API_KEY first:\n    export GATEWAY_API_KEY=your-key-here")
    raise SystemExit(1)

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

for model_name in CANDIDATE_MODEL_NAMES:
    print(f"\nTrying model name: {model_name}")
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Reply with just the word OK."}],
            max_tokens=10,
        )
        print("SUCCESS. Response:", response.choices[0].message.content)
        print("--> Use this exact model name string in the agent script.")
        break
    except Exception as e:
        print("FAILED:", e)
