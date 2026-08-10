"""
stt_service.py
Deepgram Speech-to-Text (STT) Service module for RealEstate Hub Voice Agent.

Transcribes incoming caller audio (m4a, wav, mp3, ogg) to text using Deepgram's Nova-3 model
optimized for Urdu / UrduLish speech recognition.
"""

import os
import time
import requests
from typing import Dict, Any
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
DEEPGRAM_URL = os.environ.get(
    "DEEPGRAM_URL",
    "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true&language=ur"
)


def transcribe_audio(audio_bytes: bytes, content_type: str = "audio/m4a") -> Dict[str, Any]:
    """
    Transcribe audio bytes using Deepgram REST API.

    :param audio_bytes: Raw binary audio data.
    :param content_type: MIME type of the audio (e.g., 'audio/m4a', 'audio/wav', 'audio/mp3', 'audio/ogg').
    :return: Dict containing 'transcript', 'confidence', 'processing_time_ms', and 'audio_size_kb'.
    """
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY environment variable is missing. Check your .env file.")

    if not audio_bytes:
        raise ValueError("Audio payload is empty.")

    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": content_type,
    }

    start = time.perf_counter()
    response = requests.post(DEEPGRAM_URL, headers=headers, data=audio_bytes, timeout=15)
    end = time.perf_counter()

    if response.status_code != 200:
        raise RuntimeError(f"Deepgram STT error ({response.status_code}): {response.text}")

    result = response.json()
    try:
        alternative = result["results"]["channels"][0]["alternatives"][0]
        transcript = alternative.get("transcript", "").strip()
        confidence = alternative.get("confidence", 0.0)
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Deepgram payload format: {e}")

    return {
        "transcript": transcript,
        "confidence": confidence,
        "processing_time_ms": round((end - start) * 1000, 1),
        "audio_size_kb": round(len(audio_bytes) / 1024, 1),
    }
