"""OpenAI wrapper: TTS, STT, and answer evaluation."""

from __future__ import annotations

import json
import os
import tempfile

from dotenv import load_dotenv
from openai import OpenAI

from felvi_games.models import Ertekeles

load_dotenv()

_client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
)
MODEL = os.getenv("LLM_MODEL", "gpt-4o")

_EVAL_SYSTEM = (
    "Magyar felvételi kvíz értékelő vagy. "
    "Röviden (max 2 mondat), buzdítóan értékeld a tanuló válaszát."
)

_EVAL_TEMPLATE = """\
Feladat: {kerdes}
Helyes válasz: {helyes}
Tanuló válasza: {adott}
Magyarázat: {magyarazat}

Értékeld a választ, majd adj vissza CSAK egy JSON objektumot:
{{"helyes": true/false, "visszajelzes": "...", "pont": 0-10}}"""


def text_to_speech(szoveg: str) -> bytes:
    """TTS – visszatér nyers MP3 byte-okkal."""
    response = _client.audio.speech.create(
        model="tts-1",
        voice="nova",
        input=szoveg,
    )
    return response.content


def speech_to_text(audio_bytes: bytes) -> str:
    """Whisper STT – visszatér az átírt szöveggel."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        with open(tmp_path, "rb") as f:
            transcript = _client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="hu",
            )
        return transcript.text
    finally:
        os.unlink(tmp_path)


def check_answer(kerdes: str, helyes: str, adott: str, magyarazat: str) -> Ertekeles:
    """GPT értékeli a választ. Visszatér egy `Ertekeles` példánnyal."""
    prompt = _EVAL_TEMPLATE.format(
        kerdes=kerdes, helyes=helyes, adott=adott, magyarazat=magyarazat
    )
    try:
        response = _client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _EVAL_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        return Ertekeles.from_dict(json.loads(response.choices[0].message.content))
    except Exception:
        return Ertekeles.hiba()
