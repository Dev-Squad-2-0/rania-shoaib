"""
webapp/server.py
==================

Tiny local HTTP wrapper around agent.chat() so the browser frontend
(index.html, opened directly as a file) can talk to your existing
LangChain agent. Does not change agent.py/tools.py/system_prompt.py at
all — it just exposes the same `chat()` function you already use in
test_task4_multiturn.py and test_task5_eval.py, over a local API.

Run from the project root (one level up from this file) so agent.py's
own relative imports (system_prompt, tools, data_loader) still resolve:

    cd afl_chat_agent
    python webapp/server.py

Then open webapp/index.html in your browser. It talks to
http://127.0.0.1:5050 by default.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from flask_cors import CORS

from agent import chat, GroundingLogger

app = Flask(__name__)
CORS(app)  # local-only tool; wide-open CORS is fine for a single dev's own machine


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True) or {}
    message = (data.get("message") or "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not message:
        return jsonify({"error": "message is required"}), 400

    logger = GroundingLogger()
    try:
        result = chat(message, session_id=session_id, logger=logger)
    except Exception as exc:  # noqa: BLE001 - surface the real error to the UI while developing
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "answer": result["answer"],
            "session_id": session_id,
            "tool_calls": result["tool_calls"],
        }
    )


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    if not os.environ.get("GATEWAY_API_KEY"):
        print(
            "GATEWAY_API_KEY not set. Add it to a .env file in the project "
            "root (see .env.example) before starting the server."
        )
    app.run(host="127.0.0.1", port=5050, debug=True)