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
_CHEAP_MODEL = os.getenv("LLM_CHEAP_MODEL", "gpt-4o")

_TTS_PREP_SYSTEM = (
    "Felolvasáshoz előkészítő asszisztens vagy. "
    "A feladatod KIZÁRÓLAG formázási átalakítás – NE válaszolj a kérdésre, NE magyarázz, "
    "NE értékelj. Csak az eredeti szöveget alakítsd át felolvasható formára. "
    "Kapod a feladat markdown szövegét, és visszaadsz egy természetesen felolvasható "
    "magyar szöveget, amely UGYANAZT a tartalmat közvetíti. Szabályok: "
    "- Távolítsd el az összes markdown formázást (**, *, #, backtick, stb.). "
    "- A LaTeX matematikai jelöléseket ($...$ és $$...$$) alakítsd át természetes szóbeli "
    "  megfogalmazássá (pl. $x^2$ → 'x négyzet', $\\frac{a}{b}$ → 'a per b'). "
    "- Táblázatokat, listákat folyó szöveggé fogalmazd át. "
    "- Számokat, egyenleteket, speciális karaktereket is alakítsd át szöveggé (pl. '3', '≥', '∑' → 'három', 'nagyobb vagy egyenlő', 'szumma'). "
    "- Legyen természetes, folyékony, felolvasható szöveg. "
    "- TILOS: válasz adása, magyarázat, értékelés, saját megjegyzés. "
    "- Csak az átalakított szöveget add vissza, semmi mást."
)

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
{reszpontozas_sor}

Értékeld a választ, majd adj vissza CSAK egy JSON objektumot:
{{"visszajelzes": "...", "pont": 0-{max_pont}}}

Megjegyzés: ha az elfogadott válaszok listája nem üres, akkor az adott választ
azokhoz kell hasonlítani (szinonimákat és eltolódásokat is fogadj el).
Igaz/hamis feladatnál csak "igaz" vagy "hamis" szó elfogadható.
Párosítás- és halmaz-típusú feladatoknál (ahol a helyes válasz több elem
kombinációja) az elemek sorrendje ne számítson; részleges egyezésnél adj
részletes visszajelzést arról, mely elemek helyesek.
Ha van részpontozási szabály (lásd fent), alkalmazd pontosan: számítsd ki a
pontot a szabály szerint."""


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


def kerdes_to_tts_szoveg(kerdes_markdown: str) -> str:
    """Convert a markdown question text into natural spoken Hungarian for TTS.

    Uses a cost-efficient model (_CHEAP_MODEL) since this is a simple
    text-transformation task.
    """
    response = _client.chat.completions.create(
        model=_CHEAP_MODEL,
        messages=[
            {"role": "system", "content": _TTS_PREP_SYSTEM},
            {"role": "user", "content": kerdes_markdown},
        ],
        temperature=0,
        max_tokens=512,
    )
    return (response.choices[0].message.content or "").strip()


def check_answer(
    kerdes: str,
    helyes: str,
    adott: str,
    magyarazat: str,
    *,
    elfogadott_valaszok: list[str] | None = None,
    feladat_tipus: str | None = None,
    max_pont: int = 1,
    reszpontozas: str | None = None,
) -> Ertekeles:
    """GPT értékeli a választ. Visszatér egy `Ertekeles` példánnyal."""
    elfogadott_sor = (
        f"Elfogadott válaszok: {', '.join(elfogadott_valaszok)}"
        if elfogadott_valaszok
        else ""
    )
    tipus_sor = f"Feladat típusa: {feladat_tipus}" if feladat_tipus else ""
    reszpontozas_sor = f"Részpontozási szabály: {reszpontozas}" if reszpontozas else ""
    prompt = _EVAL_TEMPLATE.format(
        kerdes=kerdes,
        helyes=helyes,
        elfogadott_sor=elfogadott_sor,
        tipus_sor=tipus_sor,
        reszpontozas_sor=reszpontozas_sor,
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
        # Clamp point to valid range; derive helyes from score (GPT no longer returns it)
        clamped = max(0, min(ert.pont, max_pont))
        return Ertekeles(helyes=(clamped == max_pont), visszajelzes=ert.visszajelzes, pont=clamped)
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
    "és esetleg javaslatot teszel egy egyedi időkorlátozott kihívás éremre. "
    "Mindig magyarul válaszolj. Légy lelkesítő, tömör (max 3 mondat az üzenetben)."
)

# Supported condition types for dynamic medals.
# window_hours: the time window within which the condition must be met (1–18h).
_CONDITION_TYPES_DOC = """\
Elérhető condition type értékek (gépileg kiértékelhető):
  feladat_count      – {"type":"feladat_count","n":5,"window_hours":12}
  helyes_count       – {"type":"helyes_count","n":3,"window_hours":8}
  pont_sum           – {"type":"pont_sum","n":20,"window_hours":18}
  streak             – {"type":"streak","n":5,"window_hours":18}  (all-time legjobb sorozat)
  session_count      – {"type":"session_count","n":2,"window_hours":6}
  tokeletes_session  – {"type":"tokeletes_session","window_hours":18}
  feladat_subject    – {"type":"feladat_subject","n":5,"subject":"matek","window_hours":12}
  before_hour        – {"type":"before_hour","n":3,"hour":8,"window_hours":18}
  after_hour         – {"type":"after_hour","n":3,"hour":20,"window_hours":18}
  special_date       – {"type":"special_date","date":"MM-DD","feladat_count":1}

FONTOS: n és window_hours legyen reálisan elérhető a statisztikák alapján.
window_hours: 1–18 között legyen (rövid idejű kihívás).
A "leiras" mező foglalja össze röviden a feltételt (pl. "Oldj meg 5 feladatot 8 órán belül!")."""

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

{condition_types_doc}

Feladatod:
1. Írj egy rövid, személyre szabott motiváló üzenetet (greeting).
2. Javasolj egy privát időkorlátozott kihívás érmet amelyet a felhasználó
   a következő {window_hours} órán belül megszerezhet, HA van erre reális lehetőség.
   Válassz egy gépileg kiértékelhető condition type-ot (lásd fent).
   Ha nincs jó ötlet, hagyj new_medal null-on.

Válaszolj CSAK JSON-ban:
{{
  "greeting": "...",
  "new_medal": {{
    "nev": "...",
    "leiras": "Rövid magyar leírás a feltételről (pl. 5 feladat 8 órán belül)",
    "ikon": "emoji",
    "kategoria": "teljesitmeny|merfoldko|rendszeresseg|felfedezes|kitartas",
    "ervenyes_napig": 1,
    "condition": {{ ...egy condition objektum a fentiek közül... }}
  }} | null
}}"""


def generate_daily_insight(
    user: str,
    stats: dict,
    close_medals: list,
    earned_count: int,
    *,
    window_hours: int = 18,
) -> dict:
    """Ask the LLM for a motivational greeting and an optional new medal suggestion.

    Args:
        user:         Player name.
        stats:        Dict from ``progress_check.get_user_stats()``.
        close_medals: List of ``CloseMedal`` objects.
        earned_count: How many medals the user has earned so far.
        window_hours: Validity window for the dynamic challenge medal (1–18h).

    Returns:
        Dict with ``greeting`` (str) and ``new_medal`` (dict | None).
        ``new_medal`` includes a ``condition`` dict for machine evaluation.
    """
    close_text = "\n".join(
        f"  - {cm.erem.ikon} {cm.erem.nev}: {cm.hint} ({int(cm.progress * 100)}%)"
        for cm in close_medals
    ) or "  (nincs közel lévő érem)"

    prompt = _DAILY_INSIGHT_TEMPLATE.format(
        user=user,
        close_medals_text=close_text,
        earned_count=earned_count,
        condition_types_doc=_CONDITION_TYPES_DOC,
        window_hours=window_hours,
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
        max_tokens=500,
    )
    raw = response.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"greeting": f"Helló {user}! Üdv vissza! 🎉", "new_medal": None}

