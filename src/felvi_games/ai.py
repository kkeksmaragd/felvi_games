"""OpenAI wrapper: TTS, STT, and answer evaluation."""

from __future__ import annotations

import json
import os
import tempfile

from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from felvi_games.models import Ertekeles

load_dotenv(Path(__file__).parent.parent.parent / ".env", override=True)

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
{elfogadott_sor}
{tipus_sor}
Tanuló válasza: {adott}
Magyarázat: {magyarazat}
Max. pontszám: {max_pont}

Értékeld a választ, majd adj vissza CSAK egy JSON objektumot:
{{"helyes": true/false, "visszajelzes": "...", "pont": 0-{max_pont}}}

Megjegyzés: ha az elfogadott válaszok listája nem üres, akkor az adott választ
azokhoz kell hasonlítani (szinonimákat és eltolódásokat is fogadj el).
Igaz/hamis feladatnál csak "igaz" vagy "hamis" szó elfogadható.
Párosítás- és halmaz-típusú feladatoknál (ahol a helyes válasz több elem
kombinációja) az elemek sorrendje ne számítson; részleges egyezésnél adj
részletes visszajelzést arról, mely elemek helyesek."""


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


def check_answer(
    kerdes: str,
    helyes: str,
    adott: str,
    magyarazat: str,
    *,
    elfogadott_valaszok: list[str] | None = None,
    feladat_tipus: str | None = None,
    max_pont: int = 1,
) -> Ertekeles:
    """GPT értékeli a választ. Visszatér egy `Ertekeles` példánnyal."""
    elfogadott_sor = (
        f"Elfogadott válaszok: {', '.join(elfogadott_valaszok)}"
        if elfogadott_valaszok
        else ""
    )
    tipus_sor = f"Feladat típusa: {feladat_tipus}" if feladat_tipus else ""
    prompt = _EVAL_TEMPLATE.format(
        kerdes=kerdes,
        helyes=helyes,
        elfogadott_sor=elfogadott_sor,
        tipus_sor=tipus_sor,
        adott=adott,
        magyarazat=magyarazat,
        max_pont=max_pont,
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
        ert = Ertekeles.from_dict(json.loads(response.choices[0].message.content))
        # Clamp point to valid range
        clamped = max(0, min(ert.pont, max_pont))
        if clamped != ert.pont:
            ert = Ertekeles(helyes=ert.helyes, visszajelzes=ert.visszajelzes, pont=clamped)
        return ert
    except Exception:
        return Ertekeles.hiba()


# ---------------------------------------------------------------------------
# Medal asset generation
# ---------------------------------------------------------------------------

_MEDAL_IMAGE_PROMPT = (
    "A vibrant, highly detailed digital award medal for a children's educational game. "
    "The medal should look like a collectible achievement badge: circular, gold/silver metallic rim, "
    "colorful center, with the following theme: {tema}. "
    "Style: colorful flat vector illustration, bold outlines, celebratory feel. "
    "No text on the medal. Transparent or white background. Square canvas."
)

_MEDAL_HANG_TEMPLATE = (
    "Gratulálunk! Megszerezted a(z) {nev} érmet! {leiras}"
)


def generate_medal_image(nev: str, leiras: str, ikon: str) -> bytes:
    """Generate a PNG medal image with DALL-E 3.

    Returns raw PNG bytes (1024×1024).
    """
    tema = f"{nev} – {leiras} (symbol hint: {ikon})"
    prompt = _MEDAL_IMAGE_PROMPT.format(tema=tema)
    response = _client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        n=1,
        size="1024x1024",
        response_format="b64_json",
        quality="standard",
    )
    import base64
    b64 = response.data[0].b64_json
    return base64.b64decode(b64)


def generate_medal_hang(nev: str, leiras: str) -> bytes:
    """Generate an MP3 award announcement with TTS (Nova voice).

    Returns raw MP3 bytes.
    """
    szoveg = _MEDAL_HANG_TEMPLATE.format(nev=nev, leiras=leiras)
    return text_to_speech(szoveg)


# ---------------------------------------------------------------------------
# Daily insight (progress analysis + optional new medal suggestion)
# ---------------------------------------------------------------------------

_DAILY_INSIGHT_SYSTEM = (
    "Magyar felvételi kvíz coach vagy. "
    "A játékos napi belépésekor rövid, személyes motiváló üzenetet írsz, "
    "és esetleg javaslatot teszel egy egyedi napi kihívás éremre. "
    "Mindig magyarul válaszolj. Légy lelkesítő, tömör (max 3 mondat az üzenetben)."
)

_DAILY_INSIGHT_TEMPLATE = """\
Felhasználó: {user}
Statisztikák:
  - Összes megoldott feladat: {total_attempts}
  - Helyes válasz arány: {accuracy_pct}%
  - Lezárt menetek: {completed_sessions}
  - Jelenlegi napi sorozat: {current_streak_days} nap
  - Elmúlt 7 napból aktív napok: {recent_days_7d}
  - Legjobb egymást követő helyes sorozat: {best_correct_streak}
  - Tárgyak amelyeket játszott: {subjects_used}
  - Szintek amelyeket játszott: {levels_used}
  - Megszerzett érmek száma: {earned_count}

Közel lévő érmek (progress 0–1):
{close_medals_text}

Feladatod:
1. Írj egy rövid, személyre szabott motiváló üzenetet (greeting).
2. Opcionálisan javasolj egy privát napi kihívás érmet amelyet a felhasználó
   a KÖVETKEZŐ belépésig megszerezhet, HA van erre reális lehetőség a statisztikák alapján.
   Ha nincs jó ötlet, hagyj new_medal null-on.

Válaszolj CSAK JSON-ban:
{{
  "greeting": "...",
  "new_medal": {{
    "nev": "...",
    "leiras": "Pontosan mit kell elérni (pl. 10 feladatot hibátlanul)",
    "ikon": "emoji",
    "kategoria": "teljesitmeny|merfoldko|rendszeresseg|felfedezes|kitartas",
    "ideiglenes": true,
    "ervenyes_napig": 3
  }} | null
}}"""


def generate_daily_insight(
    user: str,
    stats: dict,
    close_medals: list,
    earned_count: int,
) -> dict:
    """Ask the LLM for a motivational greeting and an optional new medal suggestion.

    Args:
        user:         Player name.
        stats:        Dict from ``progress_check.get_user_stats()``.
        close_medals: List of ``CloseMedal`` objects.
        earned_count: How many medals the user has earned so far.

    Returns:
        Dict with ``greeting`` (str) and ``new_medal`` (dict | None).
    """
    close_text = "\n".join(
        f"  - {cm.erem.ikon} {cm.erem.nev}: {cm.hint} ({int(cm.progress * 100)}%)"
        for cm in close_medals
    ) or "  (nincs közel lévő érem)"

    prompt = _DAILY_INSIGHT_TEMPLATE.format(
        user=user,
        close_medals_text=close_text,
        earned_count=earned_count,
        **{k: stats[k] for k in (
            "total_attempts", "accuracy_pct", "completed_sessions",
            "current_streak_days", "recent_days_7d",
            "best_correct_streak", "subjects_used", "levels_used",
        )},
    )

    response = _client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _DAILY_INSIGHT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.8,
        max_tokens=400,
    )
    raw = response.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"greeting": f"Helló {user}! Üdv vissza! 🎉", "new_medal": None}

