import requests
import json

resp = requests.post(
    "http://localhost:8000/chat/completions",
    json={
        "model": "voice-agent",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True
    }
)
for line in resp.iter_lines():
    if line:
        print(line.decode('utf-8'))
