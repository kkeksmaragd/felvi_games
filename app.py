import json
import os
import random
import tempfile

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
)
MODEL = os.getenv("LLM_MODEL", "gpt-4o")

# ---------------------------------------------------------------------------
# Feladatbank betöltése
# ---------------------------------------------------------------------------

@st.cache_data
def load_feladatok():
    path = os.path.join(os.path.dirname(__file__), "feladatok.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# OpenAI segédfüggvények
# ---------------------------------------------------------------------------

def text_to_speech(szoveg: str) -> bytes:
    """Szöveg hangos felolvasása, visszatér a nyers MP3 byteszel."""
    response = client.audio.speech.create(
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
        with open(tmp_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="hu",
            )
        return transcript.text
    finally:
        os.unlink(tmp_path)


def check_answer(kerdes: str, helyes: str, adott: str, magyarazat: str) -> dict:
    """
    GPT értékeli a választ.
    Visszatér: {"helyes": bool, "visszajelzes": str, "pont": int}
    """
    prompt = f"""Magyar felvételi kvíz értékelő vagy.

Feladat: {kerdes}
Helyes válasz: {helyes}
Tanuló válasza: {adott}
Magyarázat: {magyarazat}

Értékeld a tanuló válaszát röviden (max 2 mondat), buzdítóan.
Majd add meg JSON-ban:
{{
  "helyes": true/false,
  "visszajelzes": "...",
  "pont": 0-10
}}

Csak a JSON-t add vissza, semmi mást."""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {"helyes": False, "visszajelzes": "Nem sikerült értékelni.", "pont": 0}


# ---------------------------------------------------------------------------
# Session state inicializálás
# ---------------------------------------------------------------------------

def init_state():
    defaults = {
        "pont": 0,
        "streak": 0,
        "max_streak": 0,
        "megoldott": [],
        "aktualis": None,
        "targy": "matek",
        "szint": "mind",
        "fazis": "valasztas",   # valasztas | kerdes | eredmeny
        "atiras": "",
        "ertekeles": None,
        "tts_audio": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# UI segédfüggvények
# ---------------------------------------------------------------------------

def uj_feladat(feladatok: dict):
    targy = st.session_state.targy
    szint = st.session_state.szint
    keszlet = feladatok.get(targy, [])
    if szint != "mind":
        keszlet = [f for f in keszlet if f["szint"] == szint]
    megoldott_ids = {f["id"] for f in st.session_state.megoldott}
    maradek = [f for f in keszlet if f["id"] not in megoldott_ids]
    if not maradek:
        # Ha minden megoldott, újraindítjuk
        st.session_state.megoldott = []
        maradek = keszlet
    if not maradek:
        return None
    return random.choice(maradek)


def neh_csillag(neh: int) -> str:
    return "⭐" * neh + "☆" * (3 - neh)


# ---------------------------------------------------------------------------
# Fő alkalmazás
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="Felvételi Kvíz",
        page_icon="🎯",
        layout="centered",
    )
    init_state()
    feladatok = load_feladatok()

    # --- Fejléc ---
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.title("🎯 Felvételi Kvíz")
    with col2:
        st.metric("Pont", st.session_state.pont)
    with col3:
        streak = st.session_state.streak
        st.metric("Sorozat", f"{'🔥' * min(streak, 5)} {streak}")

    st.divider()

    # =========================================================
    # FÁZIS 1: Feladat választás / beállítások
    # =========================================================
    if st.session_state.fazis == "valasztas":

        col_t, col_s = st.columns(2)
        with col_t:
            targy = st.radio(
                "Tárgy",
                options=["matek", "magyar"],
                format_func=lambda x: "📐 Matematika" if x == "matek" else "📖 Magyar",
                index=["matek", "magyar"].index(st.session_state.targy),
                horizontal=True,
            )
            st.session_state.targy = targy

        with col_s:
            szintek = ["mind", "6 osztályos", "8 osztályos"]
            szint = st.radio(
                "Szint",
                options=szintek,
                format_func=lambda x: "🌟 Mind" if x == "mind" else x,
                index=szintek.index(st.session_state.szint),
                horizontal=True,
            )
            st.session_state.szint = szint

        st.markdown("")
        if st.button("🚀 Következő feladat!", use_container_width=True, type="primary"):
            feladat = uj_feladat(feladatok)
            if feladat:
                st.session_state.aktualis = feladat
                st.session_state.fazis = "kerdes"
                st.session_state.atiras = ""
                st.session_state.ertekeles = None
                st.session_state.tts_audio = None
                st.rerun()
            else:
                st.warning("Nincs több feladat ebben a kategóriában.")

        # Haladás megjelenítése
        keszlet = feladatok.get(st.session_state.targy, [])
        if st.session_state.szint != "mind":
            keszlet = [f for f in keszlet if f["szint"] == st.session_state.szint]
        megoldott_itt = sum(
            1 for f in st.session_state.megoldott if f["id"] in {k["id"] for k in keszlet}
        )
        if keszlet:
            st.progress(megoldott_itt / len(keszlet), text=f"Megoldott: {megoldott_itt}/{len(keszlet)}")

    # =========================================================
    # FÁZIS 2: Kérdés megjelenítése és válaszadás
    # =========================================================
    elif st.session_state.fazis == "kerdes":
        feladat = st.session_state.aktualis

        # Fejléc
        badge = "📐" if st.session_state.targy == "matek" else "📖"
        st.subheader(f"{badge} {feladat['szint']} — {neh_csillag(feladat['neh'])}")

        # Kérdés kártya
        st.info(f"**{feladat['kerdes']}**")

        # TTS gomb
        col_tts, col_hint = st.columns(2)
        with col_tts:
            if st.button("🔊 Feladat felolvasása"):
                with st.spinner("Hangszintézis..."):
                    st.session_state.tts_audio = text_to_speech(feladat["kerdes"])

        with col_hint:
            if st.button("💡 Tipp"):
                st.toast(feladat["hint"], icon="💡")

        # TTS lejátszás
        if st.session_state.tts_audio:
            st.audio(st.session_state.tts_audio, format="audio/mp3", autoplay=True)

        st.markdown("---")
        st.markdown("### Válaszolj:")

        # Mikrofon input
        audio_input = st.audio_input("🎤 Kattints és mondj egy választ")

        if audio_input:
            with st.spinner("Átírás (Whisper)..."):
                szoveg = speech_to_text(audio_input.getvalue())
            st.session_state.atiras = szoveg

        # Szöveges input (fallback)
        szoveges = st.text_input(
            "✍️ Vagy írj ide:",
            value=st.session_state.atiras,
            placeholder="pl. 32",
        )

        valasz = szoveges.strip()

        if st.session_state.atiras and not szoveges:
            valasz = st.session_state.atiras

        if valasz:
            st.caption(f"Felismert/beírt válasz: **{valasz}**")

        col_ok, col_vissza = st.columns(2)
        with col_ok:
            if st.button("✅ Ellenőrzés", disabled=not valasz, use_container_width=True, type="primary"):
                with st.spinner("GPT értékel..."):
                    ert = check_answer(
                        feladat["kerdes"],
                        feladat["helyes_valasz"],
                        valasz,
                        feladat["magyarazat"],
                    )
                st.session_state.ertekeles = ert
                st.session_state.megoldott.append(feladat)
                if ert.get("helyes"):
                    pont = ert.get("pont", 5)
                    st.session_state.pont += pont
                    st.session_state.streak += 1
                    st.session_state.max_streak = max(
                        st.session_state.streak, st.session_state.max_streak
                    )
                else:
                    st.session_state.streak = 0
                st.session_state.fazis = "eredmeny"
                st.rerun()

        with col_vissza:
            if st.button("↩ Vissza", use_container_width=True):
                st.session_state.fazis = "valasztas"
                st.rerun()

    # =========================================================
    # FÁZIS 3: Eredmény megjelenítése
    # =========================================================
    elif st.session_state.fazis == "eredmeny":
        feladat = st.session_state.aktualis
        ert = st.session_state.ertekeles

        helyes = ert.get("helyes", False)
        visszajelzes = ert.get("visszajelzes", "")
        pont = ert.get("pont", 0)

        if helyes:
            st.success(f"## 🎉 Helyes! +{pont} pont")
            streak = st.session_state.streak
            if streak >= 3:
                st.balloons()
                st.success(f"🔥 {streak} helyes válasz egymás után!")
        else:
            st.error("## ❌ Nem egészen...")

        st.markdown(f"**Visszajelzés:** {visszajelzes}")

        with st.expander("📚 Részletes magyarázat"):
            st.write(feladat["magyarazat"])
            st.markdown(f"**Helyes válasz:** `{feladat['helyes_valasz']}`")

        # TTS visszajelzés felolvasása
        if st.button("🔊 Visszajelzés felolvasása"):
            felolvasando = f"{visszajelzes} A helyes válasz: {feladat['helyes_valasz']}. {feladat['magyarazat']}"
            with st.spinner("Hangszintézis..."):
                audio = text_to_speech(felolvasando)
            st.audio(audio, format="audio/mp3", autoplay=True)

        st.divider()

        col_next, col_home = st.columns(2)
        with col_next:
            if st.button("➡️ Következő feladat", use_container_width=True, type="primary"):
                feladat = uj_feladat(feladatok)
                if feladat:
                    st.session_state.aktualis = feladat
                    st.session_state.fazis = "kerdes"
                    st.session_state.atiras = ""
                    st.session_state.ertekeles = None
                    st.session_state.tts_audio = None
                    st.rerun()
                else:
                    st.success("🏆 Minden feladatot megoldottál!")
                    st.session_state.fazis = "valasztas"
                    st.rerun()

        with col_home:
            if st.button("🏠 Főmenü", use_container_width=True):
                st.session_state.fazis = "valasztas"
                st.rerun()

    # ---------------------------------------------------------------------------
    # Oldalsáv – statisztika
    # ---------------------------------------------------------------------------
    with st.sidebar:
        st.header("📊 Statisztika")
        st.metric("Összes pont", st.session_state.pont)
        st.metric("Jelenlegi sorozat", st.session_state.streak)
        st.metric("Legjobb sorozat", st.session_state.max_streak)
        st.metric("Megoldott feladatok", len(st.session_state.megoldott))

        st.divider()
        if st.button("🔄 Újraindítás", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

        st.divider()
        st.caption("Felvételi Kvíz v0.1\nOpenAI TTS + Whisper + GPT")


if __name__ == "__main__":
    main()
