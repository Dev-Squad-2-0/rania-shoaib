"""
llm_client.py
Single place that decides which LLM backend the whole voice agent talks
to, and builds the client + picks the model name accordingly. Both
agent_graph.py and query_agent.py import `client` and `MODEL` from here
instead of each building their own — so a backend switch or a timeout
fix only has to happen once.

Company gateway (default): shared llm.netixsol.com/v1 gateway, model
alias "smart". Has a usage cap — when it's capped/down, every LLM call
in the app (intent detection, slot extraction, criteria extraction,
grounded answers) fails or hangs identically, which is what took down
/converse entirely just now.

Groq fallback: set USE_GROQ=true in .env (and GROQ_API_KEY) to route
every one of those same calls straight to Groq instead, with zero
changes anywhere else in the codebase.

Env vars:
    USE_GROQ         "true"/"1" to use Groq instead of the gateway. Default: false.
    GATEWAY_API_KEY  required when USE_GROQ is false.
    GROQ_API_KEY     required when USE_GROQ is true.
    GROQ_MODEL       optional, default "llama-3.1-8b-instant".
                      "llama-3.3-70b-versatile" is the other common Groq
                      option if a given call needs more quality than
                      instant gives (e.g. earlier A/B testing found
                      instant weaker than the gateway's Gemini model on
                      ambiguous references / gender agreement) — swap
                      it here via env var, not per-file.
"""

import os
from dotenv import load_dotenv, find_dotenv
from openai import OpenAI

load_dotenv(find_dotenv())

USE_GROQ = os.environ.get("USE_GROQ", "false").strip().lower() in ("1", "true", "yes")

GATEWAY_BASE_URL = "https://llm.netixsol.com/v1"
GATEWAY_MODEL = "smart"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# Every LLM call in the app should time out and raise instead of hanging
# forever if the backend is capped/unresponsive — an unbounded client
# call is exactly what turned a capped gateway into a /converse request
# that never returned at all.
LLM_TIMEOUT_SECONDS = 20.0

if USE_GROQ:
    _api_key = os.environ.get("GROQ_API_KEY")
    if not _api_key:
        raise RuntimeError("USE_GROQ is set but GROQ_API_KEY is missing from the environment")
    BASE_URL = GROQ_BASE_URL
    MODEL = GROQ_MODEL
else:
    _api_key = os.environ.get("GATEWAY_API_KEY")
    if not _api_key:
        raise RuntimeError(
            "GATEWAY_API_KEY is missing from the environment "
            "(or set USE_GROQ=true and GROQ_API_KEY to use Groq instead)"
        )
    BASE_URL = GATEWAY_BASE_URL
    MODEL = GATEWAY_MODEL

client = OpenAI(base_url=BASE_URL, api_key=_api_key, timeout=LLM_TIMEOUT_SECONDS)

print(f"[llm_client] using {'Groq (' + MODEL + ')' if USE_GROQ else 'gateway (' + MODEL + ')'} "
      f"at {BASE_URL}, timeout={LLM_TIMEOUT_SECONDS}s")