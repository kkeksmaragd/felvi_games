"""Streamlit UI – Felvételi Kvíz."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from felvi_games.ai import check_answer, speech_to_text, text_to_speech
from felvi_games.config import get_exams_dir, resolve_asset, text_cache_path
from felvi_games.db import FeladatRepository
from felvi_games.models import KATEGORIA_INFO, Ertekeles, Fazis, Feladat, GameState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_TARGYAK = ["matek", "magyar"]
_SZINTEK = ["mind"] + [info.szint_ertek for info in KATEGORIA_INFO.values()]
_SZINT_CIMKEK: dict[str, str] = {"mind": "🌟 Mind"} | {
    info.szint_ertek: info.rovid for info in KATEGORIA_INFO.values()
}

# ---------------------------------------------------------------------------
# Repository (singleton per process)
# ---------------------------------------------------------------------------


@st.cache_resource
def get_repo() -> FeladatRepository:
    repo = FeladatRepository()
    _seed_from_json(repo)
    return repo


def _seed_from_json(repo: FeladatRepository) -> None:
    """Populate DB from feladatok.json if the table is empty."""
    if repo.count() > 0:
        return
    json_path = _DATA_DIR / "feladatok.json"
    if not json_path.exists():
        return
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    feladatok = [
        Feladat.from_dict(f, targy=targy)
        for targy, lista in raw.items()
        for f in lista
    ]
    repo.upsert_many(feladatok)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@st.cache_data
def load_feladatok() -> dict[str, list[Feladat]]:
    repo = get_repo()
    result: dict[str, list[Feladat]] = {}
    for targy in _TARGYAK:
        result[targy] = repo.all(targy=targy)
    return result


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------


def get_state() -> GameState:
    if "gs" not in st.session_state:
        st.session_state.gs = GameState()
    return st.session_state.gs  # type: ignore[return-value]


def next_feladat(feladatok: dict[str, list[Feladat]], gs: GameState) -> Feladat | None:
    keszlet = feladatok.get(gs.targy, [])
    if gs.szint != "mind":
        keszlet = [f for f in keszlet if f.szint == gs.szint]
    maradek = [f for f in keszlet if f.id not in gs.megoldott_ids]
    if not maradek:
        gs.megoldott_ids.clear()
        maradek = keszlet
    return random.choice(maradek) if maradek else None


def start_kerdes(feladat: Feladat, gs: GameState) -> None:
    # Reload from DB so asset paths are current
    fresh = get_repo().get(feladat.id)
    gs.aktualis = fresh if fresh else feladat
    gs.fazis = Fazis.KERDES
    gs.atiras = ""
    gs.ertekeles = None
    gs.tts_audio = None
    gs.kerdes_kezdete = datetime.now(timezone.utc)
    gs.segitseg_kert = False
    gs.hibajelezes = False
    # Auto-load cached TTS from file if available
    if gs.aktualis and gs.aktualis.tts_kerdes_path:
        gs.tts_audio = resolve_asset(gs.aktualis.tts_kerdes_path).read_bytes()


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------


def _render_header(gs: GameState) -> None:
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.title("🎯 Felvételi Kvíz")        if gs.felhasznalo:
            st.caption(f"👤 {gs.felhasznalo}")    with col2:
        st.metric("Pont", gs.pont)
    with col3:
        streak = gs.streak
        st.metric("Sorozat", f"{'🔥' * min(streak, 5)} {streak}")
    st.divider()


def _render_sidebar(gs: GameState) -> None:
    with st.sidebar:
        st.header("📊 Statisztika")
        st.metric("Összes pont", gs.pont)
        st.metric("Jelenlegi sorozat", gs.streak)
        st.metric("Legjobb sorozat", gs.max_streak)
        st.metric("Megoldott feladatok", len(gs.megoldott_ids))

        if gs.menet_id:
            st.divider()
            st.caption("📋 Aktuális menet")
            st.progress(
                min(gs.menet_megoldott / gs.menet_cel, 1.0),
                text=f"Feladat: {gs.menet_megoldott} / {gs.menet_cel}",
            )

        st.divider()
        col_new, col_out = st.columns(2)
        with col_new:
            if st.button("🔄 Új menet", use_container_width=True):
                if gs.menet_id:
                    get_repo().end_menet(gs.menet_id)
                gs.uj_menet()
                st.rerun()
        with col_out:
            if st.button("🚶 Kilépés", use_container_width=True):
                if gs.menet_id:
                    get_repo().end_menet(gs.menet_id)
                gs.reset()
                gs.felhasznalo = ""
                st.rerun()

        if gs.felhasznalo:
            menetek = get_repo().get_menetek(gs.felhasznalo)
            if menetek:
                st.divider()
                with st.expander(f"📜 Korábbi menetek ({len(menetek)})", expanded=False):
                    for m in menetek:
                        status = "✅" if m.lezart else "🔄"
                        st.caption(
                            f"{status} {m.targy} – "
                            f"{m.megoldott}/{m.feladat_limit} feladat, "
                            f"{m.pont} pont, {m.idotartam_perc}"
                        )

        st.divider()
        st.caption("Felvételi Kvíz v0.1\nOpenAI TTS + Whisper + GPT")


def _render_valasztas(
    feladatok: dict[str, list[Feladat]], gs: GameState
) -> None:
    col_t, col_s = st.columns(2)
    with col_t:
        gs.targy = st.radio(
            "Tárgy",
            options=_TARGYAK,
            format_func=lambda x: "📐 Matematika" if x == "matek" else "📖 Magyar",
            index=_TARGYAK.index(gs.targy),
            horizontal=True,
        )
    with col_s:
        gs.szint = st.radio(
            "Szint",
            options=_SZINTEK,
            format_func=lambda x: _SZINT_CIMKEK.get(x, x),
            index=_SZINTEK.index(gs.szint) if gs.szint in _SZINTEK else 0,
            horizontal=True,
        )

    if gs.menet_id is None:
        gs.menet_cel = int(st.number_input(
            "Feladatok száma egy menetben:",
            min_value=5, max_value=50, value=gs.menet_cel, step=5,
        ))
    else:
        st.caption(f"🎯 Menet: {gs.menet_megoldott} / {gs.menet_cel} feladat")

    st.markdown("")
    if st.button("🚀 Következő feladat!", use_container_width=True, type="primary"):
        feladat = next_feladat(feladatok, gs)
        if feladat:
            if gs.menet_id is None:
                gs.menet_id = get_repo().start_menet(
                    gs.felhasznalo, gs.targy, gs.szint, gs.menet_cel
                )
                gs.menet_megoldott = 0
            start_kerdes(feladat, gs)
            st.rerun()
        else:
            st.warning("Nincs több feladat ebben a kategóriában.")

    keszlet = feladatok.get(gs.targy, [])
    if gs.szint != "mind":
        keszlet = [f for f in keszlet if f.szint == gs.szint]
    megoldott_itt = sum(1 for f in keszlet if f.id in gs.megoldott_ids)
    if keszlet:
        st.progress(
            megoldott_itt / len(keszlet),
            text=f"Megoldott: {megoldott_itt}/{len(keszlet)}",
        )


def _render_kerdes(gs: GameState) -> None:
    feladat: Feladat = gs.aktualis  # type: ignore[assignment]
    badge = "📐" if gs.targy == "matek" else "📖"
    st.subheader(f"{badge} {feladat.szint} — {feladat.neh_csillag()}")

    # Shared preamble / context (reading passage, table, figure)
    if feladat.kontextus:
        with st.expander("📌 Közös kontextus (feladat alapja)", expanded=True):
            st.markdown(feladat.kontextus)

    st.info(f"**{feladat.kerdes}**")

    if feladat.abra_van:
        page_hint = f"\n\n📍 Feladat helye: **{feladat.feladat_oldal}. oldal**" if feladat.feladat_oldal else ""
        st.warning(
            "⚠️ Ez a feladat ábrára / grafikonra hivatkozik – "
            f"az alábbi gombbal nyisd meg az eredeti feladatlapot!{page_hint}"
        )
    _render_pdf_button(feladat)

    col_tts, col_hint, col_hiba = st.columns(3)
    with col_tts:
        if st.button("🔊 Feladat felolvasása"):
            if feladat.tts_kerdes_path:
                gs.tts_audio = resolve_asset(feladat.tts_kerdes_path).read_bytes()
            else:
                with st.spinner("Hangszintézis..."):
                    audio = text_to_speech(feladat.tts_szoveg())
                    gs.tts_audio = audio
                    updated = get_repo().save_tts_assets(feladat, tts_kerdes=audio)
                    gs.aktualis = updated
            st.rerun()
    with col_hint:
        if st.button("💡 Tipp"):
            gs.segitseg_kert = True
            st.toast(feladat.hint, icon="💡")
    with col_hiba:
        if st.button("🚩 Hibát jelzek", help="Hibás feladatszöveg bejelentése"):
            gs.hibajelezes = True
            st.toast("Köszönjük a visszajelzést!", icon="🚩")

    if gs.tts_audio:
        st.audio(gs.tts_audio, format="audio/mp3", autoplay=True)

    # Source text inspection
    _render_source_expanders(feladat, show_ut=False)

    st.markdown("---")
    st.markdown("### Válaszolj:")

    audio_input = st.audio_input("🎤 Kattints és mondj egy választ")
    if audio_input:
        with st.spinner("Átírás (Whisper)..."):
            gs.atiras = speech_to_text(audio_input.getvalue())

    szoveges = st.text_input(
        "✍️ Vagy írj ide:",
        value=gs.atiras,
        placeholder="pl. 32",
    )
    valasz = (szoveges or gs.atiras).strip()

    if valasz:
        st.caption(f"Felismert/beírt válasz: **{valasz}**")

    col_ok, col_vissza = st.columns(2)
    with col_ok:
        if st.button("✅ Ellenőrzés", disabled=not valasz, use_container_width=True, type="primary"):
            with st.spinner("GPT értékel..."):
                ert = check_answer(
                    feladat.kerdes,
                    feladat.helyes_valasz,
                    valasz,
                    feladat.magyarazat,
                )
            elapsed = (
                (datetime.now(timezone.utc) - gs.kerdes_kezdete).total_seconds()
                if gs.kerdes_kezdete else None
            )
            gs.record_answer(feladat, ert)
            get_repo().save_megoldas(
                feladat, valasz, ert,
                felhasznalo_nev=gs.felhasznalo,
                menet_id=gs.menet_id,
                elapsed_sec=elapsed,
                segitseg_kert=gs.segitseg_kert,
                hibajelezes=gs.hibajelezes,
            )
            if gs.menet_id:
                get_repo().update_menet_progress(gs.menet_id, gs.menet_megoldott, gs.pont)
                if gs.menet_megoldott >= gs.menet_cel:
                    get_repo().end_menet(gs.menet_id)
                    gs.menet_id = None
            gs.fazis = Fazis.EREDMENY
            st.rerun()
    with col_vissza:
        if st.button("↩ Vissza", use_container_width=True):
            gs.fazis = Fazis.VALASZTAS
            st.rerun()


def _render_eredmeny(feladatok: dict[str, list[Feladat]], gs: GameState) -> None:
    feladat: Feladat = gs.aktualis  # type: ignore[assignment]
    ert: Ertekeles = gs.ertekeles  # type: ignore[assignment]

    if ert.helyes:
        st.success(f"## 🎉 Helyes! +{ert.pont} pont")
        if gs.streak >= 3:
            st.balloons()
            st.success(f"🔥 {gs.streak} helyes válasz egymás után!")
    else:
        st.error("## ❌ Nem egészen...")

    st.markdown(f"**Visszajelzés:** {ert.visszajelzes}")

    with st.expander("📚 Részletes magyarázat"):
        st.write(feladat.magyarazat)
        st.markdown(f"**Helyes válasz:** `{feladat.helyes_valasz}`")

    _render_pdf_button(feladat)
    # Source text inspection (both feladatlap and útmutató)
    _render_source_expanders(feladat, show_ut=True)

    if st.button("🔊 Visszajelzés felolvasása"):
        with st.spinner("Hangszintézis..."):
            audio = text_to_speech(feladat.eredmeny_tts_szoveg(ert.visszajelzes))
        st.audio(audio, format="audio/mp3", autoplay=True)

    if st.button("📚 Magyarázat felolvasása"):
        if feladat.tts_magyarazat_path:
            st.audio(resolve_asset(feladat.tts_magyarazat_path).read_bytes(), format="audio/mp3", autoplay=True)
        else:
            with st.spinner("Hangszintézis..."):
                mag_szoveg = f"A helyes válasz: {feladat.helyes_valasz}. {feladat.magyarazat}"
                audio = text_to_speech(mag_szoveg)
                updated = get_repo().save_tts_assets(feladat, tts_magyarazat=audio)
                gs.aktualis = updated
            st.audio(audio, format="audio/mp3", autoplay=True)

    st.divider()

    # Session completion banner
    if gs.menet_id is None and gs.menet_megoldott > 0 and gs.menet_megoldott >= gs.menet_cel:
        st.success(f"🏆 Menet vége! {gs.menet_megoldott} feladatot oldottál meg, {gs.pont} ponttal.")
        st.info("🔄 Kattints az ‚Új menet’ gombra a bal oldali menüben, vagy folytasd tovább!")

    col_next, col_home = st.columns(2)
    with col_next:
        if st.button("➡️ Következő feladat", use_container_width=True, type="primary"):
            feladat = next_feladat(feladatok, gs)
            if feladat:
                if gs.menet_id is None:
                    gs.menet_id = get_repo().start_menet(
                        gs.felhasznalo, gs.targy, gs.szint, gs.menet_cel
                    )
                    gs.menet_megoldott = 0
                start_kerdes(feladat, gs)
                st.rerun()
            else:
                st.success("🏆 Minden feladatot megoldottál!")
                gs.fazis = Fazis.VALASZTAS
                st.rerun()
    with col_home:
        if st.button("🏠 Főmenü", use_container_width=True):
            gs.fazis = Fazis.VALASZTAS
            st.rerun()


def _render_pdf_button(feladat: Feladat) -> None:
    """Show a download button for the source feladatlap PDF.
    Always visible; shows page number when known."""
    if not feladat.fl_pdf_path:
        return
    pdf_path = get_exams_dir() / feladat.fl_pdf_path
    if not pdf_path.exists():
        return
    page_info = f" – {feladat.feladat_oldal}. oldal" if feladat.feladat_oldal else ""
    st.download_button(
        label=f"📄 Feladatlap PDF{page_info}",
        data=pdf_path.read_bytes(),
        file_name=pdf_path.name,
        mime="application/pdf",
    )


def _render_source_expanders(feladat: Feladat, *, show_ut: bool) -> None:
    """Optionally show the raw extracted PDF text for debugging / context."""
    if feladat.fl_szoveg_path:
        with st.expander("📄 Feladatlap szövege (forrás)"):
            try:
                st.text(resolve_asset(feladat.fl_szoveg_path).read_text(encoding="utf-8"))
            except FileNotFoundError:
                st.caption(f"Fájl nem található: {feladat.fl_szoveg_path}")
    if show_ut and feladat.ut_szoveg_path:
        with st.expander("📋 Javítási útmutató szövege (forrás)"):
            try:
                st.text(resolve_asset(feladat.ut_szoveg_path).read_text(encoding="utf-8"))
            except FileNotFoundError:
                st.caption(f"Fájl nem található: {feladat.ut_szoveg_path}")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def _render_login(gs: GameState) -> None:
    st.title("🎯 Felvételi Kvíz")
    st.markdown("### Kinek szól a játék?")
    nev = st.text_input("Neved:", placeholder="pl. Bence", max_chars=64)
    if st.button("Tovább →", type="primary", disabled=not nev.strip()):
        gs.felhasznalo = nev.strip()
        get_repo().get_or_create_felhasznalo(nev.strip())
        st.rerun()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Felvételi Kvíz", page_icon="🎯", layout="centered")

    gs = get_state()
    feladatok = load_feladatok()

    if not gs.felhasznalo:
        _render_login(gs)
        return

    _render_header(gs)
    _render_sidebar(gs)

    if gs.fazis == Fazis.VALASZTAS:
        _render_valasztas(feladatok, gs)
    elif gs.fazis == Fazis.KERDES:
        _render_kerdes(gs)
    elif gs.fazis == Fazis.EREDMENY:
        _render_eredmeny(feladatok, gs)


if __name__ == "__main__":
    main()
