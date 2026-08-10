"""
tts_service.py
Fish Audio Text-to-Speech (TTS) Service module for RealEstate Hub Voice Agent.

Synthesizes response text (Roman UrduLish) into fluent, natural-sounding audio MP3 bytes.
Supports both Fish Audio SDK and direct REST fallback.
"""

import os
import time
import requests
from typing import Dict, Any
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

FISH_API_KEY = os.environ.get("FISH_AUDIO_API_KEY")
FISH_REFERENCE_ID = os.environ.get("FISH_REFERENCE_ID", "16344fa6cc2a46a09825a0871cecc0a6")

try:
    from fish_audio_sdk import Session, TTSRequest
    HAS_SDK = True
except ImportError:
    HAS_SDK = False


def synthesize_speech(text: str, reference_id: str = None) -> Dict[str, Any]:
    """
    Synthesizes input Roman UrduLish text into MP3 audio bytes using Fish Audio.

    :param text: Text string to synthesize.
    :param reference_id: Fish Audio voice model reference ID (optional override).
    :return: Dict containing 'audio_bytes', 'ttfa_ms', 'total_ms', and 'audio_size_kb'.
    """
    if not FISH_API_KEY:
        raise RuntimeError("FISH_AUDIO_API_KEY environment variable is missing. Check your .env file.")

    if not text or not text.strip():
        raise ValueError("Text input for TTS synthesis cannot be empty.")

    voice_id = reference_id or FISH_REFERENCE_ID
    start = time.perf_counter()
    first_chunk_time = None
    audio_bytes = b""

    if HAS_SDK:
        session = Session(FISH_API_KEY)
        for chunk in session.tts(
            TTSRequest(text=text, reference_id=voice_id),
            backend="s2.1-pro-free",
        ):
            if first_chunk_time is None:
                first_chunk_time = time.perf_counter()
            audio_bytes += chunk
    else:
        # REST API fallback
        url = "https://api.fish.audio/v1/tts"
        headers = {
            "Authorization": f"Bearer {FISH_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "reference_id": voice_id,
            "format": "mp3",
            "latency": "normal",
        }
        response = requests.post(url, json=payload, headers=headers, stream=True, timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f"Fish Audio TTS error ({response.status_code}): {response.text}")

        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                if first_chunk_time is None:
                    first_chunk_time = time.perf_counter()
                audio_bytes += chunk

    end = time.perf_counter()

    return {
        "audio_bytes": audio_bytes,
        "ttfa_ms": round((first_chunk_time - start) * 1000, 1) if first_chunk_time else round((end - start) * 1000, 1),
        "total_ms": round((end - start) * 1000, 1),
        "audio_size_kb": round(len(audio_bytes) / 1024, 1),
    }
