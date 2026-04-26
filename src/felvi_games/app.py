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
from felvi_games.models import KATEGORIA_INFO, Ertekeles, Fazis, Feladat, GameState, InterakcioTipus

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_TARGYAK = ["matek", "magyar"]
_SZINTEK = ["mind"] + [info.szint_ertek for info in KATEGORIA_INFO.values()]
_SZINT_CIMKEK: dict[str, str] = {"mind": "🌟 Mind"} | {
    info.szint_ertek: info.rovid for info in KATEGORIA_INFO.values()
}

_TIPUS_BADGE: dict[str, str] = {
    "nyilt_valasz":  "📝 Nyílt válasz",
    "tobbvalasztos": "🔤 Többválasztós",
    "parositas":     "🔗 Párosítás",
    "igaz_hamis":    "✅ Igaz / Hamis",
    "fogalmazas":    "📄 Fogalmazás",
    "kitoltes":      "✏️ Kitöltés",
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


def _group_members(feladat: Feladat, keszlet: list[Feladat]) -> list[Feladat]:
    """Return all members of feladat's group, sorted by csoport_sorrend."""
    return sorted(
        [f for f in keszlet if f.csoport_id == feladat.csoport_id
         and f.csoport_sorrend is not None],
        key=lambda f: f.csoport_sorrend,  # type: ignore[arg-type]
    )


def next_feladat(feladatok: dict[str, list[Feladat]], gs: GameState) -> Feladat | None:
    keszlet = feladatok.get(gs.targy, [])
    if gs.szint != "mind":
        keszlet = [f for f in keszlet if f.szint == gs.szint]

    # --- drain the pre-built queue first ---
    feladat_by_id = {f.id: f for f in keszlet}
    while gs.feladat_sor:
        fid = gs.feladat_sor.pop(0)
        if fid in feladat_by_id:
            return feladat_by_id[fid]

    maradek = [f for f in keszlet if f.id not in gs.megoldott_ids]
    if not maradek:
        gs.megoldott_ids.clear()
        maradek = keszlet

    if not maradek:
        return None

    # How many questions remain in the current session?
    hátralevo = max(1, gs.menet_cel - gs.menet_megoldott)

    # Prefer standalone tasks when possible to keep group logic clean
    standalone = [f for f in maradek if not f.csoport_id]
    grouped = [f for f in maradek if f.csoport_id]

    # Try to pick a group whose full (unsolved) member list fits the quota
    random.shuffle(grouped)
    for candidate in grouped:
        members = _group_members(candidate, keszlet)
        unsolved = [f for f in members if f.id not in gs.megoldott_ids]
        if not unsolved:
            continue
        if len(unsolved) <= hátralevo:
            # Enqueue the whole group in order; return first immediately
            gs.feladat_sor = [f.id for f in unsolved[1:]]
            return unsolved[0]

    # No group fits → fall back to a standalone task
    if standalone:
        return random.choice(standalone)

    # Last resort: pick any remaining task (or force-enqueue the smallest group)
    if grouped:
        candidate = min(
            grouped,
            key=lambda f: len(_group_members(f, keszlet)),
        )
        members = _group_members(candidate, keszlet)
        unsolved = [f for f in members if f.id not in gs.megoldott_ids]
        if unsolved:
            gs.feladat_sor = [f.id for f in unsolved[1:]]
            return unsolved[0]

    return random.choice(maradek)


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
    st.session_state.pop("_stt_hash", None)
    # Auto-load cached TTS from file if available
    if gs.aktualis and gs.aktualis.tts_kerdes_path:
        gs.tts_audio = resolve_asset(gs.aktualis.tts_kerdes_path).read_bytes()


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------


def _render_header(gs: GameState) -> None:
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.title("🎯 Felvételi Kvíz")
        if gs.felhasznalo:
            st.caption(f"👤 {gs.felhasznalo}")
    with col2:
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
            # Earned medals
            eremek = get_repo().get_eremek(gs.felhasznalo)
            if eremek:
                st.divider()
                from felvi_games.achievements import EREM_KATALOGUS
                from felvi_games.medal_assets import get_medal_asset
                with st.expander(f"🏅 Érmek ({len(eremek)})", expanded=False):
                    for fe in eremek:
                        erem = EREM_KATALOGUS.get(fe.erem_id)
                        if erem is None:
                            continue
                        label = erem.ikon
                        if fe.szamlalo > 1:
                            label += f" ×{fe.szamlalo}"
                        if erem.ideiglenes and fe.lejarat:
                            exp = fe.lejarat.replace(tzinfo=timezone.utc) if fe.lejarat.tzinfo is None else fe.lejarat
                            days_left = max(0, (exp - datetime.now(timezone.utc)).days)
                            label += f"  *(még {days_left} nap)*"
                        st.markdown(f"**{label} {erem.nev}**")
                        # Rich assets — GIF beats static image
                        gif = get_medal_asset(erem, "gif")
                        kep = get_medal_asset(erem, "kep")
                        if gif is not None:
                            if isinstance(gif, bytes):
                                st.image(gif, width=120)
                            else:
                                st.image(gif, width=120)   # URL
                        elif kep is not None:
                            if isinstance(kep, bytes):
                                st.image(kep, width=120)
                            else:
                                st.image(kep, width=120)   # URL
                        st.caption(f"_{erem.leiras}_")
                        hang = get_medal_asset(erem, "hang")
                        if hang is not None:
                            data = hang if isinstance(hang, bytes) else None
                            if data:
                                st.audio(data, format="audio/mp3")
                        st.markdown("---")
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
                if gs.felhasznalo:
                    get_repo().log_interakcio(
                        gs.felhasznalo, InterakcioTipus.MENET_INDUL,
                        targy=gs.targy, szint=gs.szint, menet_id=gs.menet_id,
                    )
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


def _render_csoport_context(feladat: Feladat) -> None:
    """Show group position badge and shared context text."""
    csoport = None
    if feladat.csoport_id:
        csoport = get_repo().get_csoport(feladat.csoport_id)

    # Group position caption
    if csoport and feladat.csoport_sorrend:
        group_feladatok = get_repo().get_feladatok_by_csoport(feladat.csoport_id)  # type: ignore[arg-type]
        total = len(group_feladatok)
        max_ossz = csoport.max_pont_ossz
        sorszam_label = feladat.feladat_sorszam or csoport.feladat_sorszam
        pt_info = f" · összpontszám: {max_ossz}" if max_ossz > 1 else ""
        st.caption(
            f"📋 {sorszam_label}. feladat — "
            f"részfeladat: {feladat.csoport_sorrend} / {total}{pt_info}"
        )

    # Shared context: prefer csoport.kontextus, fall back to feladat.kontextus
    kontextus = (csoport.kontextus if csoport else None) or feladat.kontextus
    if kontextus:
        with st.expander("📌 Közös szöveg / kontextus", expanded=True):
            st.markdown(kontextus)


def _render_valasz_input(feladat: Feladat, gs: GameState) -> str:
    """Smart answer input widget based on feladat_tipus.

    - igaz_hamis → Igaz / Hamis radio
    - tobbvalasztos (with options) → radio from valaszlehetosegek
    - everything else → speech + text input
    """
    tipus = feladat.feladat_tipus

    if tipus == "igaz_hamis":
        sel = st.radio(
            "Válaszod:",
            options=["Igaz", "Hamis"],
            index=None,
            horizontal=True,
            key=f"vh_{feladat.id}",
        )
        return sel.lower() if sel else ""

    if tipus == "tobbvalasztos" and feladat.valaszlehetosegek:
        sel = st.radio(
            "Válaszd ki a helyes választ:",
            options=feladat.valaszlehetosegek,
            index=None,
            key=f"tv_{feladat.id}",
        )
        return sel or ""

    # Generic open-answer: speech + text
    audio_input = st.audio_input("🎤 Kattints és mondj egy választ")
    if audio_input:
        audio_hash = hash(audio_input.getvalue())
        if st.session_state.get("_stt_hash") != audio_hash:
            st.session_state["_stt_hash"] = audio_hash
            with st.spinner("Átírás (Whisper)..."):
                gs.atiras = speech_to_text(audio_input.getvalue())

    szoveges = st.text_area(
        "✍️ Vagy írj ide:",
        value=gs.atiras,
        placeholder="pl. 32",
        height=120,
    )
    return (szoveges or gs.atiras).strip()


def _render_kerdes(gs: GameState) -> None:
    feladat: Feladat = gs.aktualis  # type: ignore[assignment]
    badge = "📐" if gs.targy == "matek" else "📖"

    # --- Header: szint, nehézség, feladat típus ---
    tipus_label = _TIPUS_BADGE.get(feladat.feladat_tipus or "", "")
    col_info, col_pont = st.columns([3, 1])
    with col_info:
        st.subheader(f"{badge} {feladat.szint} — {feladat.neh_csillag()}")
        if tipus_label:
            st.caption(tipus_label)
    with col_pont:
        if feladat.max_pont > 1:
            st.metric("Max. pont", feladat.max_pont)

    # --- Csoport pozíció + közös kontextus ---
    _render_csoport_context(feladat)

    # --- Kérdés (Markdown + LaTeX math renderelés) ---
    st.info(feladat.kerdes)

    # --- Válaszlehetőségek listázva (párosítás / ha nincs widget) ---
    if (
        feladat.valaszlehetosegek
        and feladat.feladat_tipus not in ("tobbvalasztos", "igaz_hamis")
    ):
        with st.expander("📋 Válaszlehetőségek", expanded=True):
            for opt in feladat.valaszlehetosegek:
                st.markdown(f"- {opt}")

    # --- Értékelési megjegyzés ---
    if feladat.ertekeles_megjegyzes:
        with st.expander("ℹ️ Értékelési feltétel"):
            st.caption(feladat.ertekeles_megjegyzes)

    # --- Ábra figyelmeztetés + PDF gomb ---
    if feladat.abra_van:
        page_hint = f"\n\n📍 Feladat helye: **{feladat.feladat_oldal}. oldal**" if feladat.feladat_oldal else ""
        st.warning(
            "⚠️ Ez a feladat ábrára / grafikonra hivatkozik – "
            f"az alábbi gombbal nyisd meg az eredeti feladatlapot!{page_hint}"
        )
    _render_pdf_button(feladat)

    # --- TTS, Tipp, Hiba gombok ---
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
            if gs.felhasznalo:
                get_repo().log_interakcio(
                    gs.felhasznalo, InterakcioTipus.TTS_LEJATSZO,
                    targy=gs.targy, szint=gs.szint,
                    feladat_id=feladat.id, menet_id=gs.menet_id,
                )
            st.rerun()
    with col_hint:
        if st.button("💡 Tipp"):
            gs.segitseg_kert = True
            if gs.felhasznalo:
                get_repo().log_interakcio(
                    gs.felhasznalo, InterakcioTipus.SEGITSEG_KERT,
                    targy=gs.targy, szint=gs.szint,
                    feladat_id=feladat.id, menet_id=gs.menet_id,
                )
            st.rerun()
    with col_hiba:
        if st.button("🚩 Hibát jelzek", help="Hibás feladatszöveg bejelentése"):
            gs.hibajelezes = True
            st.toast("Köszönjük a visszajelzést!", icon="🚩")

    if gs.tts_audio:
        st.audio(gs.tts_audio, format="audio/mp3", autoplay=True)

    if gs.segitseg_kert:
        st.info(f"💡 **Tipp:** {feladat.hint}")

    # --- Forrásszöveg (debug / kontextus) ---
    _render_source_expanders(feladat, show_ut=False)

    st.markdown("---")
    st.markdown("### Válaszolj:")

    valasz = _render_valasz_input(feladat, gs)

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
                    elfogadott_valaszok=feladat.elfogadott_valaszok_vagy_helyes(),
                    feladat_tipus=feladat.feladat_tipus,
                    max_pont=feladat.max_pont,
                )
            elapsed = (
                (datetime.now(timezone.utc) - gs.kerdes_kezdete).total_seconds()
                if gs.kerdes_kezdete else None
            )
            gs.utolso_valasz = valasz
            gs.record_answer(feladat, ert)
            get_repo().save_megoldas(
                feladat, valasz, ert,
                felhasznalo_nev=gs.felhasznalo,
                menet_id=gs.menet_id,
                elapsed_sec=elapsed,
                segitseg_kert=gs.segitseg_kert,
                hibajelezes=gs.hibajelezes,
            )
            # --- interaction log ---
            if gs.felhasznalo:
                ev_tipus = (
                    InterakcioTipus.HELYES_VALASZ if ert.helyes
                    else InterakcioTipus.HELYTELEN_VALASZ
                )
                get_repo().log_interakcio(
                    gs.felhasznalo, ev_tipus,
                    targy=gs.targy, szint=gs.szint,
                    feladat_id=feladat.id, menet_id=gs.menet_id,
                    meta={"pont": ert.pont, "elapsed_sec": elapsed},
                )
            # --- session progress + medal checks ---
            if gs.menet_id:
                get_repo().update_menet_progress(gs.menet_id, gs.menet_megoldott, gs.pont)
                if gs.menet_megoldott >= gs.menet_cel:
                    get_repo().end_menet(gs.menet_id)
                    if gs.felhasznalo:
                        get_repo().log_interakcio(
                            gs.felhasznalo, InterakcioTipus.MENET_VEGZETT,
                            targy=gs.targy, szint=gs.szint, menet_id=gs.menet_id,
                        )
                        from felvi_games.achievements import check_new_medals
                        uj_eremek = check_new_medals(gs.felhasznalo, gs.menet_id, get_repo())
                        if uj_eremek:
                            st.session_state["_uj_eremek"] = [e.id for e in uj_eremek]
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

    # --- A tanuló válasza vs. helyes válasz ---
    col_adott, col_helyes = st.columns(2)
    with col_adott:
        st.markdown(f"**Adott válasz:** {gs.utolso_valasz}")
    with col_helyes:
        st.markdown(f"**Helyes válasz:** {feladat.helyes_valasz}")

    with st.expander("📚 Részletes magyarázat", expanded=not ert.helyes):
        st.markdown(feladat.magyarazat)
        st.markdown(f"**Helyes válasz:** {feladat.helyes_valasz}")

        # Show all accepted answers if there are multiple
        if feladat.elfogadott_valaszok and len(feladat.elfogadott_valaszok) > 1:
            st.markdown("**Elfogadható válaszok:** " + ", ".join(
                feladat.elfogadott_valaszok
            ))

        # Partial scoring rule
        if feladat.reszpontozas:
            st.caption(f"📊 Részpontozás: {feladat.reszpontozas}")

        # Grader note
        if feladat.ertekeles_megjegyzes:
            st.caption(f"ℹ️ {feladat.ertekeles_megjegyzes}")

        # Max points for this sub-task
        if feladat.max_pont > 1:
            st.caption(f"Max. pont: {feladat.max_pont}")

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

    # Review section
    if not feladat.review_elvegezve:
        st.divider()
        with st.expander("🔍 Feladat review kérése"):
            st.caption(
                "Ha úgy érzed, hogy a feladat vagy a helyes válasz hibás, "
                "kérhetsz egy AI-alapú felülvizsgálatot."
            )
            megjegyzes = st.text_area(
                "Megjegyzés (opcionális):",
                placeholder="pl. A helyes válasz nem stimmel, mert…",
                key=f"review_megjegyzes_{feladat.id}",
            )
            if st.button("🤖 AI Review indítása", key=f"review_btn_{feladat.id}"):
                _run_ai_review(feladat, megjegyzes.strip() or None, gs)

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


def _run_ai_review(feladat: Feladat, megjegyzes: str | None, gs: GameState) -> None:
    """Trigger an AI review pass, persist results, and update GameState."""
    from felvi_games.review import review_feladat_ai

    fl_text = ""
    if feladat.fl_szoveg_path:
        try:
            fl_text = resolve_asset(feladat.fl_szoveg_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            pass

    with st.spinner("AI ellenőrzi a feladatot…"):
        reviewed = review_feladat_ai(feladat, fl_text, megjegyzes)

    updated = get_repo().save_review(reviewed, megjegyzes)
    gs.aktualis = updated
    st.success("✅ Review kész – feladat frissítve.")
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
                text = resolve_asset(feladat.fl_szoveg_path).read_text(encoding="utf-8")
                st.markdown(
                    f'<div style="max-height:50vh;overflow-y:auto;white-space:pre-wrap;'
                    f'font-family:monospace;font-size:0.85em">{text}</div>',
                    unsafe_allow_html=True,
                )
            except FileNotFoundError:
                st.caption(f"Fájl nem található: {feladat.fl_szoveg_path}")



# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def _render_login(gs: GameState) -> None:
    st.title("🎯 Felvételi Kvíz")
    st.markdown("### Add meg a neved:")
    nev = st.text_input("Neved:", placeholder="pl. Jani", max_chars=64)
    if st.button("Tovább →", type="primary", disabled=not nev.strip()):
        gs.felhasznalo = nev.strip()
        get_repo().get_or_create_felhasznalo(nev.strip())
        st.rerun()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# Medal award dialog
# ---------------------------------------------------------------------------


@st.dialog("� Napi áttekintő")
def _show_daily_insight_dialog(insight_data: dict) -> None:
    """Display the AI-generated daily progress insight."""
    from felvi_games.medal_assets import get_medal_asset

    st.markdown(f"### {insight_data['greeting']}")

    close = insight_data.get("close_medals", [])
    if close:
        st.markdown("#### 🎯 Hamarosan megszerezheted:")
        for cm in close:
            pct = int(cm["progress"] * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            st.markdown(f"{cm['ikon']} **{cm['nev']}** `{bar}` {pct}%")
            st.caption(cm["hint"])

    teaser = insight_data.get("teaser_medal")
    if teaser:
        st.markdown("---")
        new_flag = " 🆕" if insight_data.get("new_medal_created") else ""
        st.markdown(f"#### ⭐ Következő cél{new_flag}")
        st.markdown(f"{teaser['ikon']} **{teaser['nev']}**")
        st.caption(teaser["leiras"])
        # Show image if available — but we need the Erem object; use id from dict
        if teaser.get("id"):
            from felvi_games.db import EremRecord
            from felvi_games.models import Erem
            from sqlalchemy.orm import Session as _S
            with _S(get_repo()._engine) as _sess:
                rec = _sess.get(EremRecord, teaser["id"])
            if rec:
                erem_obj = rec.to_domain()
                kep = get_medal_asset(erem_obj, "kep")
                if kep:
                    st.image(kep if isinstance(kep, bytes) else kep, width=160)

    if st.button("💪 Rajta, nézzük!", use_container_width=True, type="primary"):
        st.session_state.pop("_napi_insight", None)
        st.rerun()


# ---------------------------------------------------------------------------
# Medal award dialog
# ---------------------------------------------------------------------------


@st.dialog("�🏅 Új érem!")
def _show_medal_dialog(erem_ids: list[str]) -> None:
    """Rich modal shown when one or more medals are awarded."""
    from felvi_games.achievements import EREM_KATALOGUS
    from felvi_games.medal_assets import get_medal_asset

    for erem_id in erem_ids:
        erem = EREM_KATALOGUS.get(erem_id)
        if erem is None:
            continue

        st.markdown(f"## {erem.ikon}  {erem.nev}")
        st.markdown(f"*{erem.leiras}*")

        gif = get_medal_asset(erem, "gif")
        kep = get_medal_asset(erem, "kep")

        if gif is not None:
            st.image(gif if isinstance(gif, bytes) else gif, use_container_width=True)
        elif kep is not None:
            st.image(kep if isinstance(kep, bytes) else kep, use_container_width=True)
        else:
            st.markdown(
                f"<div style='font-size:120px;text-align:center'>{erem.ikon}</div>",
                unsafe_allow_html=True,
            )

        hang = get_medal_asset(erem, "hang")
        if hang is not None and isinstance(hang, bytes):
            st.audio(hang, format="audio/mp3", autoplay=True)

        if len(erem_ids) > 1:
            st.markdown("---")

    if st.button("🎉 Szuper, köszönöm!", use_container_width=True, type="primary"):
        st.session_state.pop("_uj_eremek", None)
        st.rerun()


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

    # Daily insight: trigger once per calendar day (runs in a spinner, non-blocking)
    if "_napi_insight" not in st.session_state:
        with st.spinner("Napi áttekintés betöltése..."):
            try:
                from felvi_games.progress_check import daily_check
                insight = daily_check(gs.felhasznalo, get_repo())
            except Exception:
                insight = None
        if insight is not None:
            # serialise to a plain dict so Streamlit can keep it in session_state
            st.session_state["_napi_insight"] = {
                "greeting": insight.greeting,
                "close_medals": [
                    {"ikon": cm.erem.ikon, "nev": cm.erem.nev,
                     "hint": cm.hint, "progress": cm.progress}
                    for cm in insight.close_medals
                ],
                "teaser_medal": (
                    {"id": insight.teaser_medal.id,
                     "ikon": insight.teaser_medal.ikon,
                     "nev": insight.teaser_medal.nev,
                     "leiras": insight.teaser_medal.leiras}
                    if insight.teaser_medal else None
                ),
                "new_medal_created": insight.new_medal_created,
            }
        else:
            # Mark as checked so we don't call again this session
            st.session_state["_napi_insight"] = None

    if st.session_state.get("_napi_insight"):
        _show_daily_insight_dialog(st.session_state["_napi_insight"])

    # Award modal: triggered after a session ends with new medals
    if st.session_state.get("_uj_eremek"):
        _show_medal_dialog(st.session_state["_uj_eremek"])

    if gs.fazis == Fazis.VALASZTAS:
        _render_valasztas(feladatok, gs)
    elif gs.fazis == Fazis.KERDES:
        _render_kerdes(gs)
    elif gs.fazis == Fazis.EREDMENY:
        _render_eredmeny(feladatok, gs)


if __name__ == "__main__":
    main()
