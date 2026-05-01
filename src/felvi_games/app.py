"""Streamlit UI – Felvételi Kvíz."""

from __future__ import annotations

import hashlib
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from felvi_games.ai import check_answer, kerdes_to_tts_szoveg, speech_to_text, text_to_speech
from felvi_games.config import get_exams_dir, resolve_asset, setup_logging, text_cache_path
from felvi_games.db import FeladatRepository
from felvi_games.models import KATEGORIA_INFO, Ertekeles, Fazis, Feladat, GameState, InterakcioTipus

setup_logging()

logger = logging.getLogger(__name__)

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

_NAPOK: list[tuple[str, str]] = [
    ("hetfo", "Hétfő"),
    ("kedd", "Kedd"),
    ("szerda", "Szerda"),
    ("csutortok", "Csütörtök"),
    ("pentek", "Péntek"),
    ("szombat", "Szombat"),
    ("vasarnap", "Vasárnap"),
]
_NAP_CIMKEK: dict[str, str] = dict(_NAPOK)

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


def _least_seen_choice(candidates: list[Feladat], counts: dict[str, int]) -> Feladat:
    """Return a random feladat from the least-attempted tier in *candidates*."""
    min_count = min(counts.get(f.id, 0) for f in candidates)
    pool = [f for f in candidates if counts.get(f.id, 0) == min_count]
    return random.choice(pool)


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
    hátralevo = max(1, gs.menet_cel - gs.pont)

    # Fetch per-feladat attempt counts for the current user (least-seen priority)
    user = gs.felhasznalo
    counts: dict[str, int] = {}
    if user:
        counts = get_repo().get_feladat_attempt_counts(user, [f.id for f in maradek])

    # Prefer standalone tasks when possible to keep group logic clean
    standalone = [f for f in maradek if not f.csoport_id]
    grouped = [f for f in maradek if f.csoport_id]

    # Try to pick a group whose full (unsolved) member list fits the quota.
    # Among eligible groups prefer the one whose members have the lowest total
    # attempt count for this user.
    eligible_groups: list[tuple[int, list[Feladat]]] = []
    seen_csoport: set[str] = set()
    for candidate in grouped:
        cid = candidate.csoport_id
        if cid in seen_csoport:
            continue
        seen_csoport.add(cid)  # type: ignore[arg-type]
        members = _group_members(candidate, keszlet)
        unsolved = [f for f in members if f.id not in gs.megoldott_ids]
        if not unsolved:
            continue
        if len(unsolved) <= hátralevo:
            total_count = sum(counts.get(f.id, 0) for f in unsolved)
            eligible_groups.append((total_count, unsolved))

    if eligible_groups:
        # Pick a random group from the least-attempted tier
        min_total = min(g[0] for g in eligible_groups)
        best_groups = [g[1] for g in eligible_groups if g[0] == min_total]
        chosen_unsolved = random.choice(best_groups)
        gs.feladat_sor = [f.id for f in chosen_unsolved[1:]]
        return chosen_unsolved[0]

    # No group fits → fall back to a standalone task (least-seen first)
    if standalone:
        return _least_seen_choice(standalone, counts)

    # Last resort: force-enqueue the smallest group
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

    return _least_seen_choice(maradek, counts)


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
    # Read widget state directly to avoid the 1-rerun lag before radio widgets
    # assign back to gs (header renders before _render_valasztas).
    targy = st.session_state.get("radio_targy", gs.targy)
    szint = st.session_state.get("radio_szint", gs.szint)
    today_stats = (
        get_repo().get_today_stats(gs.felhasznalo, targy=targy, szint=szint)
        if gs.felhasznalo
        else {"pont": gs.pont, "streak": gs.streak}
    )
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.title("🎯 Felvételi Kvíz")
        if gs.felhasznalo:
            st.caption(f"👤 {gs.felhasznalo}")
    with col2:
        st.metric(
            "Mai pont",
            today_stats["pont"],
            help="Mai pontok a kiválasztott tárgy/szint szűrésben.",
        )
    with col3:
        streak = today_stats["streak"]
        st.metric(
            "Mai sorozat",
            f"{'🔥' * min(streak, 5)} {streak}",
            help="Mai helyes-válasz sorozat a kiválasztott tárgy/szint szűrésben.",
        )
    st.divider()


def _render_sidebar(gs: GameState) -> None:
    with st.sidebar:
        today_stats = (
            get_repo().get_today_stats(gs.felhasznalo)
            if gs.felhasznalo
            else {"pont": gs.pont, "streak": gs.streak, "max_streak": gs.max_streak, "megoldott": len(gs.megoldott_ids)}
        )
        st.header("📊 Statisztika")
        st.metric(
            "Mai pont",
            today_stats["pont"],
            help="Mai összesített pont minden tárgyból és szintből.",
        )
        st.metric(
            "Mai sorozat",
            today_stats["streak"],
            help="Mai aktuális helyes-válasz sorozat minden tárgyból és szintből.",
        )
        st.metric(
            "Mai legjobb sorozat",
            today_stats["max_streak"],
            help="Mai napon elért leghosszabb helyes-válasz sorozat.",
        )
        st.metric(
            "Mai megoldott",
            today_stats["megoldott"],
            help="Mai megoldások száma minden tárgyból és szintből.",
        )

        if gs.menet_id:
            st.divider()
            st.caption("📋 Aktuális menet")
            st.progress(
                min(gs.pont / gs.menet_cel, 1.0),
                text=f"Pont: {gs.pont} / {gs.menet_cel}",
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

        if st.button("⚙️ Beállítások", use_container_width=True):
            st.session_state["_active_page"] = "settings"
            st.rerun()
        if st.session_state.get("_active_page") == "settings":
            if st.button("🎮 Vissza a játékhoz", use_container_width=True):
                st.session_state["_active_page"] = "game"
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


def _render_settings_page(gs: GameState) -> None:
    st.header("⚙️ Saját célok és beállítások")
    st.caption("Itt rögzítheted a céljaidat. A reward rendszer a target típusú rekordokat is használhatja.")

    repo = get_repo()
    tab_targets, tab_flexible = st.tabs(["🎯 Cél beállítása", "🧩 Rugalmas beállítás"])

    with tab_targets:
        with st.form("target_form", clear_on_submit=True):
            st.subheader("Új target record")
            targy = st.selectbox(
                "Tárgy",
                options=["mind", *_TARGYAK],
                format_func=lambda x: "🌐 Mind" if x == "mind" else ("📐 Matematika" if x == "matek" else "📖 Magyar"),
            )
            szint = st.selectbox(
                "Szint",
                options=_SZINTEK,
                format_func=lambda x: _SZINT_CIMKEK.get(str(x), str(x)),
            )
            selected_days = st.multiselect(
                "Kiválasztott napok",
                options=[k for k, _ in _NAPOK],
                format_func=lambda x: _NAP_CIMKEK.get(str(x), str(x)),
                default=["hetfo", "kedd", "szerda", "csutortok", "pentek"],
            )
            target_point = int(st.number_input("Cél pont", min_value=1, max_value=10000, value=100, step=10))
            target_name = st.text_input("Cél neve (opcionális)", placeholder="pl. Heti matek cél")

            submitted = st.form_submit_button("💾 Target mentése", type="primary")
            if submitted:
                base_key = f"{targy}:{szint}:{'-'.join(sorted(selected_days))}:{target_point}"
                setting_key = target_name.strip() or base_key
                payload = {
                    "targy": targy,
                    "szint": szint,
                    "selected_days": selected_days,
                    "target_point": target_point,
                    "name": target_name.strip() or None,
                    "target_type": "score",
                }
                repo.upsert_user_setting(
                    gs.felhasznalo,
                    "target_record",
                    setting_key,
                    payload,
                    enabled=True,
                )
                st.success("A target record elmentve.")

        targets = repo.list_user_settings(gs.felhasznalo, setting_class="target_record")
        st.markdown("### Mentett target rekordok")
        if not targets:
            st.info("Még nincs mentett target rekordod.")
        else:
            for row in targets:
                p = row.get("payload", {})
                days = ", ".join(_NAP_CIMKEK.get(str(d), str(d)) for d in p.get("selected_days", []))
                st.markdown(
                    f"- **{row['setting_key']}** · tárgy: `{p.get('targy', 'mind')}` · "
                    f"szint: `{p.get('szint', 'mind')}` · napok: {days or '-'} · "
                    f"cél pont: **{p.get('target_point', 0)}**"
                )

            ids = [r["id"] for r in targets]
            delete_id = st.selectbox("Törlendő target rekord", options=ids, format_func=lambda x: f"ID {x}")
            if st.button("🗑️ Törlés", key="delete_target_btn"):
                if delete_id is not None and repo.delete_user_setting(gs.felhasznalo, int(delete_id)):
                    st.success("Target rekord törölve.")
                    st.rerun()

    with tab_flexible:
        with st.form("flex_settings_form", clear_on_submit=True):
            st.subheader("Egyedi beállítás mentése")
            setting_class = st.text_input("Data class", value="reward_target")
            setting_key = st.text_input("Azonosító kulcs", placeholder="pl. weekly_accuracy_goal")
            payload_text = st.text_area(
                "Payload (JSON)",
                value='{"metric": "accuracy", "operator": ">=", "value": 0.8}',
                height=140,
            )
            enabled = st.checkbox("Aktív", value=True)
            flex_submit = st.form_submit_button("💾 Egyedi beállítás mentése", type="primary")
            if flex_submit:
                try:
                    payload = json.loads(payload_text)
                    if not isinstance(payload, dict):
                        st.error("A payload csak JSON objektum lehet.")
                    elif not setting_class.strip() or not setting_key.strip():
                        st.error("A data class és az azonosító kulcs kötelező.")
                    else:
                        repo.upsert_user_setting(
                            gs.felhasznalo,
                            setting_class.strip(),
                            setting_key.strip(),
                            payload,
                            enabled=enabled,
                        )
                        st.success("Egyedi beállítás elmentve.")
                except json.JSONDecodeError as exc:
                    st.error(f"Érvénytelen JSON: {exc}")

        all_settings = repo.list_user_settings(gs.felhasznalo)
        st.markdown("### Minden mentett beállítás")
        if not all_settings:
            st.info("Még nincs mentett beállítás.")
        else:
            table_rows = [
                {
                    "id": r["id"],
                    "class": r["setting_class"],
                    "key": r["setting_key"],
                    "enabled": r["enabled"],
                    "payload": json.dumps(r["payload"], ensure_ascii=False),
                }
                for r in all_settings
            ]
            st.dataframe(table_rows, use_container_width=True)


def _render_valasztas(
    feladatok: dict[str, list[Feladat]], gs: GameState
) -> None:
    logger.debug("render_valasztas | targy=%s szint=%s menet_id=%s", gs.targy, gs.szint, gs.menet_id)

    # Seed widget state from gs on the VERY FIRST render only, then let
    # Streamlit own the values (explicit keys prevent positional-key resets).
    if "radio_targy" not in st.session_state:
        st.session_state["radio_targy"] = gs.targy
    if "radio_szint" not in st.session_state:
        st.session_state["radio_szint"] = gs.szint

    col_t, col_s = st.columns(2)
    with col_t:
        gs.targy = st.radio(
            "Tárgy",
            options=_TARGYAK,
            format_func=lambda x: "📐 Matematika" if x == "matek" else "📖 Magyar",
            horizontal=True,
            key="radio_targy",
        )
    with col_s:
        gs.szint = st.radio(
            "Szint",
            options=_SZINTEK,
            format_func=lambda x: _SZINT_CIMKEK.get(x, x),
            horizontal=True,
            key="radio_szint",
        )

    if gs.menet_id is None:
        gs.menet_cel = int(st.number_input(
            "Megszerezhető pontok egy menetben:",
            min_value=5, max_value=50, value=gs.menet_cel, step=5,
        ))
    else:
        st.caption(f"🎯 Menet: {gs.pont} / {gs.menet_cel} pont")

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

    _targy = st.session_state.get("radio_targy", gs.targy)
    _szint = st.session_state.get("radio_szint", gs.szint)
    keszlet = feladatok.get(_targy, [])
    if _szint != "mind":
        keszlet = [f for f in keszlet if f.szint == _szint]

    megoldott_itt = (
        get_repo().count_user_solved_feladatok(gs.felhasznalo, targy=_targy, szint=_szint)
        if gs.felhasznalo
        else sum(1 for f in keszlet if f.id in gs.megoldott_ids)
    )
    if keszlet:
        st.progress(
            min(megoldott_itt / len(keszlet), 1.0),
            text=f"Összesen megoldott ebben a szűrésben: {megoldott_itt}/{len(keszlet)}",
        )
        st.caption("ℹ️ Összesített mutató: a felhasználó által valaha pontot érő megoldással teljesített feladatok száma a kiválasztott tárgy/szint szerint.")


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
            f"  ·  `{feladat.id}`"
        )

    # Shared context: prefer csoport.kontextus, fall back to feladat.kontextus
    kontextus = (csoport.kontextus if csoport else None) or feladat.kontextus
    if kontextus:
        with st.expander("📌 Közös szöveg / kontextus", expanded=True):
            st.markdown(kontextus)


def _render_valasz_input(feladat: Feladat, gs: GameState) -> str:
    """Answer input: radio pre-fills the text box, user can always edit freely."""
    tipus = feladat.feladat_tipus
    ta_key = f"ta_{feladat.id}"

    # Radio for igaz/hamis and többválasztós – selection writes into the text_area's
    # session_state key, but only when the selection *changes*, so subsequent edits
    # in the text area are not overwritten on re-render.
    if tipus == "igaz_hamis":
        radio_key = f"vh_{feladat.id}"
        sel = st.radio(
            "Válaszod:",
            options=["Igaz", "Hamis"],
            index=None,
            horizontal=True,
            key=radio_key,
        )
        applied_key = f"_radio_applied_{feladat.id}"
        if sel and st.session_state.get(applied_key) != sel:
            st.session_state[applied_key] = sel
            st.session_state[ta_key] = sel

    elif tipus == "tobbvalasztos" and feladat.valaszlehetosegek:
        radio_key = f"tv_{feladat.id}"
        sel = st.radio(
            "Válaszd ki a helyes választ:",
            options=feladat.valaszlehetosegek,
            index=None,
            key=radio_key,
        )
        applied_key = f"_radio_applied_{feladat.id}"
        if sel and st.session_state.get(applied_key) != sel:
            st.session_state[applied_key] = sel
            st.session_state[ta_key] = sel

    else:
        # Generic open-answer: speech input
        audio_input = st.audio_input("🎤 Kattints és mondj egy választ")
        if audio_input:
            audio_hash = hash(audio_input.getvalue())
            if st.session_state.get("_stt_hash") != audio_hash:
                st.session_state["_stt_hash"] = audio_hash
                with st.spinner("Átírás (Whisper)..."):
                    gs.atiras = speech_to_text(audio_input.getvalue())
                st.session_state[ta_key] = gs.atiras

    szoveges = st.text_area(
        "✍️ Válasz:",
        placeholder="pl. 32",
        height=80,
        key=ta_key,
    )
    return szoveges.strip()


def _render_kerdes(gs: GameState) -> None:
    feladat: Feladat = gs.aktualis  # type: ignore[assignment]
    badge = "📐" if gs.targy == "matek" else "📖"

    # --- Header: szint, nehézség, feladat típus ---
    tipus_label = _TIPUS_BADGE.get(feladat.feladat_tipus or "", "")
    col_info, col_tipus, col_pont = st.columns([3, 2, 1])
    with col_info:
        st.subheader(f"{badge} {feladat.szint} — {feladat.neh_csillag()}")
    with col_tipus:
        if tipus_label:
            st.caption(" ")
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
        with st.expander("📋 Válaszlehetőségek", expanded=False):
            for opt in feladat.valaszlehetosegek:
                st.markdown(f"- {opt}")

    # --- Ábra figyelmeztetés + PDF gomb ---
    if feladat.abra_van:
        col_abra, col_pdf = st.columns([3, 1])
        with col_abra:
            page_hint = f" · **{feladat.feladat_oldal}. oldal**" if feladat.feladat_oldal else ""
            st.warning(f"⚠️ Ábrára hivatkozik!{page_hint}")
        with col_pdf:
            _render_pdf_button(feladat)
    elif feladat.fl_pdf_path:
        _render_pdf_button(feladat)

    # --- TTS audio (cached) megjelenítése a kérdés közelében ---
    if gs.tts_audio:
        st.audio(gs.tts_audio, format="audio/mp3", autoplay=True)

    # Resolve kontextus for TTS (group shared text must be read before the question)
    _csoport_tts = get_repo().get_csoport(feladat.csoport_id) if feladat.csoport_id else None
    _tts_bemeneti = feladat.kerdes

    # Stale when the SHA256 hash of the current raw input differs from the stored hash.
    _bemeneti_hash = hashlib.sha256(_tts_bemeneti.encode()).hexdigest()[:12]
    _stale = feladat.tts_kerdes_bemenet_hash != _bemeneti_hash

    # --- TTS, Tipp, Hiba gombok ---
    col_tts, col_hint, col_hiba = st.columns(3)
    with col_tts:
        if st.button("🔊 Felolvasás", help=feladat.tts_szoveg()):
            if feladat.tts_kerdes_path and not _stale:
                gs.tts_audio = resolve_asset(feladat.tts_kerdes_path).read_bytes()
            else:
                tts_text = kerdes_to_tts_szoveg(_tts_bemeneti)
                with st.spinner("Hangszintézis..."):
                    audio = text_to_speech(tts_text)
                    gs.tts_audio = audio
                    updated = get_repo().save_tts_assets(
                        feladat,
                        tts_kerdes=audio,
                        tts_kerdes_szoveg=tts_text,          # LLM-processed output (shown in help tooltip)
                        tts_kerdes_bemenet_hash=_bemeneti_hash,  # hash of raw input (stale detection)
                    )
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

    if gs.segitseg_kert:
        st.info(f"💡 **Tipp:** {feladat.hint}")

    # --- Válasz ---
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
                    reszpontozas=feladat.reszpontozas,
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
                    else InterakcioTipus.RESZLEGES_VALASZ if ert.pont > 0
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
                if gs.pont >= gs.menet_cel:
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

    # --- Kevésbé fontos elemek a válaszmező alatt ---
    if feladat.ertekeles_megjegyzes:
        with st.expander("ℹ️ Értékelési feltétel"):
            st.caption(feladat.ertekeles_megjegyzes)

    _render_source_expanders(feladat, show_ut=False)


def _render_score_bar(pont: int, max_pont: int) -> None:
    """Render a colored HTML progress bar for pont / max_pont."""
    if max_pont <= 0:
        return
    ratio = pont / max_pont
    pct = int(ratio * 100)
    if ratio >= 1.0:
        color = "#28a745"  # green
    elif ratio > 0:
        color = "#fd7e14"  # orange
    else:
        color = "#dc3545"  # red
    st.markdown(
        f"""
        <div style="background:#e9ecef;border-radius:8px;overflow:hidden;
                    height:28px;margin:6px 0">
          <div style="width:{pct}%;background:{color};height:100%;
                      display:flex;align-items:center;justify-content:center;
                      color:white;font-weight:bold;font-size:14px;min-width:2rem">
            {pont}&nbsp;/&nbsp;{max_pont}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_eredmeny(feladatok: dict[str, list[Feladat]], gs: GameState) -> None:
    feladat: Feladat = gs.aktualis  # type: ignore[assignment]
    ert: Ertekeles = gs.ertekeles  # type: ignore[assignment]

    ratio = ert.pont / feladat.max_pont if feladat.max_pont > 0 else 0.0
    if ert.helyes:
        st.success(f"## 🎉 Helyes! +{ert.pont} pont")
        if gs.streak >= 3:
            st.balloons()
            st.success(f"🔥 {gs.streak} helyes válasz egymás után!")
    elif ert.pont > 0:
        st.warning(f"## 🟡 Részben helyes! +{ert.pont} / {feladat.max_pont} pont")
    else:
        st.error("## ❌ Nem egészen...")
    _render_score_bar(ert.pont, feladat.max_pont)

    st.markdown(f"**Visszajelzés:** {ert.visszajelzes}")

    # --- A tanuló válasza vs. helyes válasz ---
    col_adott, col_helyes = st.columns(2)
    with col_adott:
        st.markdown(f"**Adott válasz:** {gs.utolso_valasz}")
    with col_helyes:
        st.markdown(f"**Helyes válasz:** {feladat.helyes_valasz}")

    with st.expander("📚 Részletes magyarázat", expanded=ratio < 1.0):
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

    col_tts1, col_tts2 = st.columns(2)
    with col_tts1:
        if st.button("🔊 Visszajelzés felolvasása", use_container_width=True):
            with st.spinner("Hangszintézis..."):
                audio = text_to_speech(feladat.eredmeny_tts_szoveg(ert.visszajelzes))
            st.audio(audio, format="audio/mp3", autoplay=True)
    with col_tts2:
        if st.button("📚 Magyarázat felolvasása", use_container_width=True):
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
    if gs.menet_id is None and gs.menet_megoldott > 0 and gs.pont >= gs.menet_cel:
        st.success(f"🏆 Menet vége! {gs.menet_megoldott} feladatot oldottál meg, {gs.pont} ponttal.")
        st.info("🔄 Kattints az ‚Új menet’ gombra a bal oldali menüben, vagy folytasd tovább!")

    col_next, col_home = st.columns(2)
    with col_next:
        if st.button("➡️ Következő feladat", use_container_width=True, type="primary"):
            kov_feladat = next_feladat(feladatok, gs)
            if kov_feladat:
                if gs.menet_id is None:
                    gs.menet_id = get_repo().start_menet(
                        gs.felhasznalo, gs.targy, gs.szint, gs.menet_cel
                    )
                    gs.menet_megoldott = 0
                start_kerdes(kov_feladat, gs)
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
    from felvi_games.review import run_feladat_review

    with st.spinner("AI ellenőrzi a feladatot…"):
        result = run_feladat_review(feladat, get_repo(), megjegyzes=megjegyzes)

    if result.versioned:
        load_feladatok.clear()  # evict stale cache – next call returns active records only
        gs.feladat_sor = [
            result.updated.id if fid == feladat.id else fid
            for fid in gs.feladat_sor
        ]
        if feladat.id in gs.megoldott_ids:
            gs.megoldott_ids.discard(feladat.id)
            gs.megoldott_ids.add(result.updated.id)
        msg = f"✅ Review kész – új verzió: `{result.updated.id}`"
    else:
        msg = "✅ Review kész – feladat frissítve (tartalom nem változott)."

    gs.aktualis = result.updated
    st.success(msg)
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
        from felvi_games.db import FeladatRepository
        canonical = FeladatRepository.normalize_username(nev)
        get_repo().get_or_create_felhasznalo(canonical)
        gs.felhasznalo = canonical
        st.rerun()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# Medal award dialog
# ---------------------------------------------------------------------------



def _load_active_challenges(user: str) -> list[dict]:
    """Return active (not-yet-earned) dynamic medal challenges for *user*.

    Each item: {id, ikon, nev, leiras, created_at_str, teljesul: bool}
    """
    from datetime import datetime, timezone
    from felvi_games.achievements import _eval_dynamic_condition, _count_dynamic_condition
    from sqlalchemy import text
    from sqlalchemy.orm import Session as _S
    import json as _json

    engine = get_repo()._engine
    earned_ids = {fe.erem_id for fe in get_repo().get_eremek(user)}
    result = []
    with _S(engine) as s:
        rows = s.execute(
            text(
                "SELECT id, nev, ikon, leiras, condition_json, created_at "
                "FROM eremek "
                "WHERE condition_json IS NOT NULL AND condition_json != '' "
                "AND (cel_felhasznalo IS NULL OR cel_felhasznalo = :u) "
                "ORDER BY created_at DESC"
            ),
            {"u": user},
        ).all()
    for r in rows:
        if r.id in earned_ids:
            continue
        try:
            cond = _json.loads(r.condition_json)
            vf = r.created_at
            if isinstance(vf, str):
                vf = datetime.fromisoformat(vf)
            if vf is not None and vf.tzinfo is None:
                vf = vf.replace(tzinfo=timezone.utc)
            teljesul = _eval_dynamic_condition(user, cond, engine, valid_from=vf)
            cur, target = _count_dynamic_condition(user, cond, engine, valid_from=vf)
            result.append({
                "id": r.id,
                "ikon": r.ikon or "🏅",
                "nev": r.nev,
                "leiras": r.leiras or "",
                "created_at_str": vf.strftime("%Y-%m-%d %H:%M") if vf else "-",
                "teljesul": teljesul,
                "current": cur,
                "target": target,
            })
        except Exception:  # noqa: BLE001
            pass
    return result


def _show_daily_insight_dialog(insight_data: dict) -> None:
    """Display the AI-generated daily progress insight."""
    from felvi_games.medal_assets import get_medal_asset

    greeting = insight_data.get("greeting", "")
    if greeting:
        st.markdown(f"### {greeting}")

    # Active challenges — always shown
    challenges = insight_data.get("active_challenges", [])
    if challenges:
        st.markdown("#### 🏆 Aktív kihívásaid:")
        for ch in challenges:
            cur = ch.get("current")
            target = ch.get("target")
            if ch["teljesul"]:
                st.success(f"{ch['ikon']} **{ch['nev']}** — ✅ Teljesítetted!", icon=None)
            else:
                progress_str = ""
                if cur is not None and target is not None and target > 0:
                    pct = min(int(cur / target * 100), 100)
                    progress_str = f"  {cur}/{target} ({pct}%)"
                    st.info(f"{ch['ikon']} **{ch['nev']}**  {progress_str}\n\n{ch['leiras']}", icon="⏳")
                    st.progress(pct / 100)
                else:
                    st.info(f"{ch['ikon']} **{ch['nev']}**\n\n{ch['leiras']}", icon="⏳")
        st.markdown("---")

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
        st.session_state["_napi_insight"] = None
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

    if st.session_state.get("_active_page") == "settings":
        _render_settings_page(gs)
        return

    logger.debug(
        "main rerun | user=%s fazis=%s insight=%s insight_seen=%s uj_eremek=%s",
        gs.felhasznalo,
        gs.fazis,
        "present" if st.session_state.get("_napi_insight") else
            ("None" if "_napi_insight" in st.session_state else "missing"),
        st.session_state.get("_napi_insight_seen"),
        bool(st.session_state.get("_uj_eremek")),
    )

    # Daily insight: trigger once per calendar day (runs in a spinner, non-blocking)
    if "_napi_insight" not in st.session_state:
        logger.debug("daily_check | running for user=%s", gs.felhasznalo)
        with st.spinner("Napi áttekintés betöltése..."):
            try:
                from felvi_games.progress_check import daily_check
                insight = daily_check(gs.felhasznalo, get_repo())
            except Exception:
                insight = None
            active_challenges = _load_active_challenges(gs.felhasznalo)
        if insight is not None:
            logger.debug("daily_check | insight received, storing in session_state")
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
                "active_challenges": active_challenges,
            }
        elif active_challenges:
            # Not first login today, but has active challenges — show them
            logger.debug("daily_check | no insight but %d active challenges", len(active_challenges))
            st.session_state["_napi_insight"] = {
                "greeting": "",
                "close_medals": [],
                "teaser_medal": None,
                "new_medal_created": False,
                "active_challenges": active_challenges,
            }
        else:
            logger.debug("daily_check | no insight, no challenges")
            # Mark as checked so we don't call again this session
            st.session_state["_napi_insight"] = None

    # Daily insight: show as a standalone page (return early) so selection widgets
    # never render at the same time — prevents double-click from layout shift.
    if st.session_state.get("_napi_insight") and not st.session_state.get("_napi_insight_seen"):
        logger.debug("main | showing insight page, returning early")
        st.session_state["_napi_insight_seen"] = True
        _show_daily_insight_dialog(st.session_state["_napi_insight"])
        return

    logger.debug("main | rendering fazis=%s", gs.fazis)
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
