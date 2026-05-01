"""
achievements.py
---------------
Medal/achievement catalog and rule engine.

Design:
  - EREM_KATALOGUS   – static dict of all possible medals (id → Erem)
  - check_new_medals – run after every session; returns medals to award
  - Rules are plain functions querying megoldasok / menetek / interakciok

Icon strategy
  Default : emoji  (works in terminal + Streamlit, zero dependencies)
  Better  : SVG from game-icons.net  (CC BY 3.0, pip install requests)
  Premium : AI-generated PNG via DALL-E 3 – hook in ai.py
            ai.generate_medal_ikon(erem_id: str, leiras: str) → bytes

Adding a new medal:
  1. Add an Erem entry to EREM_KATALOGUS
  2. Write a _rule_<id>(user, session_id, engine) → bool function below
  3. Register it in SZABALY_REGISTRY at the bottom of this file
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from felvi_games.models import Erem, FelhasznaloErem, InterakcioTipus

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from felvi_games.db import FeladatRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Medal catalog
# ---------------------------------------------------------------------------

EREM_KATALOGUS: dict[str, Erem] = {
    # ── Mérföldkövek ─────────────────────────────────────────────────────────
    "elso_menet": Erem(
        id="elso_menet",
        nev="Első lépés",
        leiras="Teljesítettél egy egész menetet.",
        ikon="🏁",
        kategoria="merfoldko",
    ),
    "szaz_feladat": Erem(
        id="szaz_feladat",
        nev="Centurion",
        leiras="100 feladatot oldottál meg.",
        ikon="💯",
        kategoria="merfoldko",
    ),
    "otszaz_feladat": Erem(
        id="otszaz_feladat",
        nev="Veterán",
        leiras="500 feladatot oldottál meg.",
        ikon="🏆",
        kategoria="merfoldko",
    ),
    "ezer_feladat": Erem(
        id="ezer_feladat",
        nev="Legenda",
        leiras="1 000 feladatot oldottál meg.",
        ikon="🌟",
        kategoria="merfoldko",
    ),

    # ── Teljesítmény ─────────────────────────────────────────────────────────
    "tokeletes_menet": Erem(
        id="tokeletes_menet",
        nev="Tökéletes menet",
        leiras="100%-os pontszámot értél el egy menetben.",
        ikon="💎",
        kategoria="teljesitmeny",
        ismetelheto=True,
    ),
    "sorozat_5": Erem(
        id="sorozat_5",
        nev="5-ös sorozat",
        leiras="5 egymást követő helyes válasz.",
        ikon="🔥",
        kategoria="teljesitmeny",
    ),
    "sorozat_10": Erem(
        id="sorozat_10",
        nev="10-es sorozat",
        leiras="10 egymást követő helyes válasz.",
        ikon="🔥🔥",
        kategoria="teljesitmeny",
    ),
    "sorozat_20": Erem(
        id="sorozat_20",
        nev="20-as sorozat",
        leiras="20 egymást követő helyes válasz.",
        ikon="⚡",
        kategoria="teljesitmeny",
    ),
    "villam": Erem(
        id="villam",
        nev="Villámsebességű",
        leiras="Helyes választ adtál 10 másodpercen belül.",
        ikon="⚡",
        kategoria="teljesitmeny",
        ismetelheto=True,
    ),
    "hint_nelkul_20": Erem(
        id="hint_nelkul_20",
        nev="Független gondolkodó",
        leiras="20 egymást követő feladatot tipp nélkül oldottál meg.",
        ikon="🧠",
        kategoria="teljesitmeny",
    ),
    "magas_pontossag": Erem(
        id="magas_pontossag",
        nev="Precíz",
        leiras="Legalább 80%-os pontosság 50+ kísérlet után.",
        ikon="🎯",
        kategoria="teljesitmeny",
    ),

    # ── Rendszeresség ─────────────────────────────────────────────────────────
    "het_egymas_utan": Erem(
        id="het_egymas_utan",
        nev="Egy hetes sorozat",
        leiras="7 egymást követő napon játszottál.",
        ikon="📅",
        kategoria="rendszeresseg",
        ismetelheto=True,
    ),
    "harom_het_egymas_utan": Erem(
        id="harom_het_egymas_utan",
        nev="Három hetes sorozat",
        leiras="21 egymást követő napon játszottál.",
        ikon="🗓️",
        kategoria="rendszeresseg",
    ),
    "pentek_matek_honap": Erem(
        id="pentek_matek_honap",
        nev="Pénteki matekes",
        leiras="Minden pénteken matekot oldottál meg egy naptári hónapban.",
        ikon="📐",
        kategoria="rendszeresseg",
        ismetelheto=True,
    ),
    "heti_haromszor": Erem(
        id="heti_haromszor",
        nev="Szorgalmas",
        leiras="Egy héten belül legalább 3 különböző napon játszottál.",
        ikon="📆",
        kategoria="rendszeresseg",
        ismetelheto=True,
    ),
    "reggeli_tanulas": Erem(
        id="reggeli_tanulas",
        nev="Korai madár",
        leiras="Reggel 8 előtt oldottál meg feladatot.",
        ikon="🌅",
        kategoria="rendszeresseg",
        ismetelheto=True,
    ),

    # ── Felfedezés ────────────────────────────────────────────────────────────
    "mindket_targy": Erem(
        id="mindket_targy",
        nev="Sokoldalú",
        leiras="Matekot és magyart is gyakoroltál.",
        ikon="🌈",
        kategoria="felfedezes",
    ),
    "minden_szint": Erem(
        id="minden_szint",
        nev="Mindentudó",
        leiras="Mindhárom szinten (4, 6, 8 osztályos) oldottál meg feladatot.",
        ikon="🎓",
        kategoria="felfedezes",
    ),
    "minden_feladattipus": Erem(
        id="minden_feladattipus",
        nev="Változatos",
        leiras="Minden feladattípusból legalább egyet megoldottál.",
        ikon="🔮",
        kategoria="felfedezes",
    ),

    # ── Mérföldkövek (közbülső) ───────────────────────────────────────────────
    "tiz_feladat": Erem(
        id="tiz_feladat",
        nev="Tíz feladat",
        leiras="10 feladatot oldottál meg.",
        ikon="🔟",
        kategoria="merfoldko",
    ),
    "huszonot_feladat": Erem(
        id="huszonot_feladat",
        nev="Negyedszázad",
        leiras="25 feladatot oldottál meg.",
        ikon="🥈",
        kategoria="merfoldko",
    ),
    "otven_feladat": Erem(
        id="otven_feladat",
        nev="Félszázad",
        leiras="50 feladatot oldottál meg.",
        ikon="🥇",
        kategoria="merfoldko",
    ),

    # ── Teljesítmény (új) ─────────────────────────────────────────────────────
    "szaz_pont": Erem(
        id="szaz_pont",
        nev="Százpontos",
        leiras="Összesen 100 pontot gyűjtöttél.",
        ikon="💰",
        kategoria="teljesitmeny",
    ),
    "otszaz_pont": Erem(
        id="otszaz_pont",
        nev="Pontgyűjtő",
        leiras="Összesen 500 pontot gyűjtöttél.",
        ikon="💎",
        kategoria="teljesitmeny",
    ),
    "esti_tanulas": Erem(
        id="esti_tanulas",
        nev="Éjjeli bagoly",
        leiras="22:00 után oldottál meg feladatot.",
        ikon="🦉",
        kategoria="rendszeresseg",
        ismetelheto=True,
    ),

    # ── Kitartás ──────────────────────────────────────────────────────────────
    "visszatero": Erem(
        id="visszatero",
        nev="Visszatérő",
        leiras="Legalább 3 különböző napon játszottál összesen.",
        ikon="🔄",
        kategoria="kitartas",
    ),
    "visszatero_tiz": Erem(
        id="visszatero_tiz",
        nev="Hűséges tanuló",
        leiras="Legalább 10 különböző napon játszottál.",
        ikon="🏅",
        kategoria="kitartas",
    ),
    "maraton": Erem(
        id="maraton",
        nev="Maraton",
        leiras="Egy menetben 30 vagy több feladatot teljesítettél.",
        ikon="🏃",
        kategoria="kitartas",
    ),

    # ── Ideiglenes (temporary streak shields) ────────────────────────────────
    "heti_bajnok": Erem(
        id="heti_bajnok",
        nev="Heti bajnok",
        leiras="Ezen a héten legalább 5 napot játszottál – csak a hétig érvényes!",
        ikon="🥇",
        kategoria="rendszeresseg",
        ideiglenes=True,
        ervenyes_napig=7,
        ismetelheto=True,
    ),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SZINTEK_OSSZ = {"4 osztályos", "6 osztályos", "8 osztályos"}
_FELADAT_TIPUSOK_OSSZ = {"nyilt_valasz", "tobbvalasztos", "parositas", "igaz_hamis", "fogalmazas", "kitoltes"}


def _nap(dt: datetime) -> datetime:
    """Truncate to calendar date (UTC)."""
    d = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _distinct_play_days(session: Session, user: str, from_dt: datetime | None = None) -> list[datetime]:
    from felvi_games.db import MenetRecord
    stmt = (
        select(MenetRecord.started_at)
        .where(MenetRecord.felhasznalo_nev == user)
        .order_by(MenetRecord.started_at)
    )
    if from_dt:
        stmt = stmt.where(MenetRecord.started_at >= from_dt)
    rows = session.scalars(stmt).all()
    seen: set[str] = set()
    days: list[datetime] = []
    for dt in rows:
        key = _nap(dt).strftime("%Y-%m-%d")
        if key not in seen:
            seen.add(key)
            days.append(_nap(dt))
    return sorted(days)


def _consecutive_days(days: list[datetime]) -> int:
    """Return the longest streak of consecutive calendar days."""
    if not days:
        return 0
    best = current = 1
    for i in range(1, len(days)):
        if (days[i] - days[i - 1]).days == 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def _current_streak(days: list[datetime]) -> int:
    """Days in the current trailing streak (must include today or yesterday)."""
    if not days:
        return 0
    today = _nap(datetime.now(timezone.utc))
    streak = 0
    prev = today
    for d in reversed(days):
        if (prev - d).days <= 1:
            streak += 1
            prev = d
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# Rules
# (Each rule is: rule_fn(user, session_id, engine) → bool)
# ---------------------------------------------------------------------------

def _rule_elso_menet(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MenetRecord
    with Session(engine) as s:
        cnt = s.scalar(
            select(func.count()).select_from(MenetRecord)
            .where(MenetRecord.felhasznalo_nev == user,
                   MenetRecord.ended_at.is_not(None))
        ) or 0
    return cnt >= 1


def _rule_szaz_feladat(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        cnt = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return cnt >= 100


def _rule_otszaz_feladat(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        cnt = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return cnt >= 500


def _rule_ezer_feladat(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        cnt = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return cnt >= 1000


def _rule_tokeletes_menet(user: str, session_id: int | None, engine: "Engine") -> bool:
    """True when the current session completed all tasks fully correctly."""
    from felvi_games.db import MegoldasRecord, MenetRecord
    if session_id is None:
        return False
    with Session(engine) as s:
        rec = s.get(MenetRecord, session_id)
        if rec is None or rec.feladat_limit <= 0 or rec.megoldott < rec.feladat_limit:
            return False
        total = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.menet_id == session_id)
        ) or 0
        helyes_cnt = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.menet_id == session_id,
                   MegoldasRecord.helyes == True)  # noqa: E712
        ) or 0
    return total > 0 and total == helyes_cnt == rec.feladat_limit


def _rule_sorozat_5(user: str, session_id: int | None, engine: "Engine") -> bool:
    return _max_helyes_sorozat(user, engine) >= 5


def _rule_sorozat_10(user: str, session_id: int | None, engine: "Engine") -> bool:
    return _max_helyes_sorozat(user, engine) >= 10


def _rule_sorozat_20(user: str, session_id: int | None, engine: "Engine") -> bool:
    return _max_helyes_sorozat(user, engine) >= 20


def _max_helyes_sorozat(user: str, engine: "Engine") -> int:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        rows = s.scalars(
            select(MegoldasRecord.helyes)
            .where(MegoldasRecord.felhasznalo_nev == user)
            .order_by(MegoldasRecord.created_at)
        ).all()
    best = cur = 0
    for h in rows:
        if h:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _rule_villam(user: str, session_id: int | None, engine: "Engine") -> bool:
    """Any answer that scored points (including partial) within 10 seconds."""
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        cnt = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(
                MegoldasRecord.felhasznalo_nev == user,
                MegoldasRecord.pont > 0,
                MegoldasRecord.elapsed_sec.is_not(None),
                MegoldasRecord.elapsed_sec <= 10.0,
            )
        ) or 0
    return cnt >= 1


def _rule_hint_nelkul_20(user: str, session_id: int | None, engine: "Engine") -> bool:
    """Last 20 answers (any outcome) without asking for a hint."""
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        rows = s.scalars(
            select(MegoldasRecord.segitseg_kert)
            .where(MegoldasRecord.felhasznalo_nev == user)
            .order_by(MegoldasRecord.created_at.desc())
            .limit(20)
        ).all()
    return len(rows) == 20 and not any(rows)


def _rule_magas_pontossag(user: str, session_id: int | None, engine: "Engine") -> bool:
    """At least 80% of total possible points earned across 50+ attempts."""
    from felvi_games.db import FeladatRecord, MegoldasRecord
    with Session(engine) as s:
        total = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
        if total < 50:
            return False
        earned = s.scalar(
            select(func.sum(MegoldasRecord.pont))
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
        max_possible = s.scalar(
            select(func.sum(FeladatRecord.max_pont))
            .join(MegoldasRecord, MegoldasRecord.feladat_id == FeladatRecord.id)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return max_possible > 0 and (earned / max_possible) >= 0.80


def _rule_het_egymas_utan(user: str, session_id: int | None, engine: "Engine") -> bool:
    with Session(engine) as s:
        days = _distinct_play_days(s, user)
    return _current_streak(days) >= 7


def _rule_harom_het_egymas_utan(user: str, session_id: int | None, engine: "Engine") -> bool:
    with Session(engine) as s:
        days = _distinct_play_days(s, user)
    return _consecutive_days(days) >= 21


def _rule_pentek_matek_honap(user: str, session_id: int | None, engine: "Engine") -> bool:
    """All Fridays of the *previous* calendar month were covered with matek sessions."""
    from felvi_games.db import MenetRecord
    now = datetime.now(timezone.utc)
    # previous month
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_prev = first_this - timedelta(seconds=1)
    first_prev = last_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # find all Fridays in that month
    fridays: set[str] = set()
    d = first_prev
    while d <= last_prev:
        if d.weekday() == 4:  # Friday
            fridays.add(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    if not fridays:
        return False

    with Session(engine) as s:
        rows = s.scalars(
            select(MenetRecord.started_at)
            .where(
                MenetRecord.felhasznalo_nev == user,
                MenetRecord.targy == "matek",
                MenetRecord.started_at >= first_prev,
                MenetRecord.started_at <= last_prev,
            )
        ).all()

    played_fridays = {_nap(dt).strftime("%Y-%m-%d") for dt in rows if _nap(dt).weekday() == 4}
    return fridays.issubset(played_fridays)


def _rule_heti_haromszor(user: str, session_id: int | None, engine: "Engine") -> bool:
    """At least 3 distinct days in the most recent 7-day window."""
    with Session(engine) as s:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        days = _distinct_play_days(s, user, from_dt=cutoff)
    return len(days) >= 3


def _rule_reggeli_tanulas(user: str, session_id: int | None, engine: "Engine") -> bool:
    """Any answer submitted before 08:00 local time (timestamps stored as naive local)."""
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        rows = s.scalars(
            select(MegoldasRecord.created_at)
            .where(
                MegoldasRecord.felhasznalo_nev == user,
                func.strftime("%H", MegoldasRecord.created_at) < "08",
            )
        ).all()
    return len(rows) > 0


def _rule_mindket_targy(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MenetRecord
    with Session(engine) as s:
        targyek = set(s.scalars(
            select(MenetRecord.targy).where(MenetRecord.felhasznalo_nev == user)
        ).all())
    return {"matek", "magyar"}.issubset(targyek)


def _rule_minden_szint(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MenetRecord
    with Session(engine) as s:
        szintek = set(s.scalars(
            select(MenetRecord.szint).where(MenetRecord.felhasznalo_nev == user)
        ).all())
    return _SZINTEK_OSSZ.issubset(szintek)


def _rule_minden_feladattipus(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import FeladatRecord, MegoldasRecord
    with Session(engine) as s:
        rows = s.scalars(
            select(FeladatRecord.feladat_tipus)
            .join(MegoldasRecord, MegoldasRecord.feladat_id == FeladatRecord.id)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ).all()
    return _FELADAT_TIPUSOK_OSSZ.issubset({r for r in rows if r})


def _rule_visszatero(user: str, session_id: int | None, engine: "Engine") -> bool:
    with Session(engine) as s:
        days = _distinct_play_days(s, user)
    return len(days) >= 3


def _rule_maraton(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MenetRecord
    if session_id is None:
        return False
    with Session(engine) as s:
        rec = s.get(MenetRecord, session_id)
        if rec is None:
            return False
        return rec.feladat_limit >= 30 and rec.megoldott >= 30


def _rule_tiz_feladat(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        cnt = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return cnt >= 10


def _rule_huszonot_feladat(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        cnt = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return cnt >= 25


def _rule_otven_feladat(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        cnt = s.scalar(
            select(func.count()).select_from(MegoldasRecord)
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return cnt >= 50


def _rule_szaz_pont(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        total = s.scalar(
            select(func.sum(MegoldasRecord.pont))
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return total >= 100


def _rule_otszaz_pont(user: str, session_id: int | None, engine: "Engine") -> bool:
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        total = s.scalar(
            select(func.sum(MegoldasRecord.pont))
            .where(MegoldasRecord.felhasznalo_nev == user)
        ) or 0
    return total >= 500


def _rule_esti_tanulas(user: str, session_id: int | None, engine: "Engine") -> bool:
    """Any answer submitted at or after 22:00 local time (timestamps stored as naive local)."""
    from felvi_games.db import MegoldasRecord
    with Session(engine) as s:
        rows = s.scalars(
            select(MegoldasRecord.created_at)
            .where(
                MegoldasRecord.felhasznalo_nev == user,
                func.strftime("%H", MegoldasRecord.created_at) >= "22",
            )
        ).all()
    return len(rows) > 0


def _rule_visszatero_tiz(user: str, session_id: int | None, engine: "Engine") -> bool:
    with Session(engine) as s:
        days = _distinct_play_days(s, user)
    return len(days) >= 10


def _rule_heti_bajnok(user: str, session_id: int | None, engine: "Engine") -> bool:
    """5+ distinct play days in the current week (Mon–Sun)."""
    now = datetime.now(timezone.utc)
    start_of_week = _nap(now) - timedelta(days=now.weekday())
    with Session(engine) as s:
        days = _distinct_play_days(s, user, from_dt=start_of_week)
    return len(days) >= 5


# ---------------------------------------------------------------------------
# Rule registry
# Each entry: (rule_fn, permanent_only=False|True)
# permanent_only=True  → only award once; never re-check once earned
# repeatable medals    → use Erem.ismetelheto flag
# ---------------------------------------------------------------------------

RuleFn = Callable[[str, int | None, "Engine"], bool]

SZABALY_REGISTRY: dict[str, RuleFn] = {
    "elso_menet": _rule_elso_menet,
    "tiz_feladat": _rule_tiz_feladat,
    "huszonot_feladat": _rule_huszonot_feladat,
    "otven_feladat": _rule_otven_feladat,
    "szaz_feladat": _rule_szaz_feladat,
    "otszaz_feladat": _rule_otszaz_feladat,
    "ezer_feladat": _rule_ezer_feladat,
    "tokeletes_menet": _rule_tokeletes_menet,
    "sorozat_5": _rule_sorozat_5,
    "sorozat_10": _rule_sorozat_10,
    "sorozat_20": _rule_sorozat_20,
    "villam": _rule_villam,
    "hint_nelkul_20": _rule_hint_nelkul_20,
    "magas_pontossag": _rule_magas_pontossag,
    "het_egymas_utan": _rule_het_egymas_utan,
    "harom_het_egymas_utan": _rule_harom_het_egymas_utan,
    "pentek_matek_honap": _rule_pentek_matek_honap,
    "heti_haromszor": _rule_heti_haromszor,
    "reggeli_tanulas": _rule_reggeli_tanulas,
    "esti_tanulas": _rule_esti_tanulas,
    "mindket_targy": _rule_mindket_targy,
    "minden_szint": _rule_minden_szint,
    "minden_feladattipus": _rule_minden_feladattipus,
    "visszatero": _rule_visszatero,
    "visszatero_tiz": _rule_visszatero_tiz,
    "maraton": _rule_maraton,
    "szaz_pont": _rule_szaz_pont,
    "otszaz_pont": _rule_otszaz_pont,
    "heti_bajnok": _rule_heti_bajnok,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_new_medals(
    user: str,
    session_id: int | None,
    repo: "FeladatRepository",
) -> list[Erem]:
    """Evaluate all rules and grant any newly earned medals.

    Loads the catalog from DB (global medals + private medals targeted at
    *user*) so new medals can be added mid-game without a restart.
    Returns the list of Erem objects that were freshly awarded this call.
    """
    engine = repo._engine
    newly_earned: list[Erem] = []
    now = datetime.now(timezone.utc)

    catalog = repo.get_erem_katalogus(user)

    logger.info(
        "check_new_medals start | user=%s session=%s catalog_size=%d",
        user, session_id, len(catalog),
    )

    skipped_already_has = 0
    skipped_no_rule = 0
    rule_errors: list[str] = []

    for erem_id, erem in catalog.items():
        # Non-repeatable + already earned → skip
        if not erem.ismetelheto and repo.has_erem(user, erem_id):
            skipped_already_has += 1
            logger.debug("skip already_earned | user=%s medal=%s", user, erem_id)
            continue

        # No rule registered → manual-grant only, skip auto-check
        rule_fn = SZABALY_REGISTRY.get(erem_id)
        if rule_fn is None:
            skipped_no_rule += 1
            logger.debug("skip no_rule | user=%s medal=%s", user, erem_id)
            continue

        try:
            earned = rule_fn(user, session_id, engine)
        except Exception as exc:  # noqa: BLE001 – rules must not crash the game
            rule_errors.append(erem_id)
            logger.warning(
                "rule_error | user=%s medal=%s error=%s",
                user, erem_id, exc, exc_info=True,
            )
            continue

        logger.debug(
            "rule_result | user=%s medal=%s session=%s result=%s",
            user, erem_id, session_id, earned,
        )

        if earned:
            expires_at: datetime | None = None
            if erem.ideiglenes and erem.ervenyes_napig:
                expires_at = now + timedelta(days=erem.ervenyes_napig)
            repo.grant_erem(user, erem_id, lejarat_at=expires_at)
            newly_earned.append(erem)
            logger.info(
                "medal_granted | user=%s medal=%s nev=%r session=%s expires=%s",
                user, erem_id, erem.nev, session_id,
                expires_at.isoformat() if expires_at else None,
            )

    logger.info(
        "check_new_medals done | user=%s session=%s granted=%d "
        "skipped_owned=%d skipped_no_rule=%d errors=%d",
        user, session_id, len(newly_earned),
        skipped_already_has, skipped_no_rule, len(rule_errors),
    )
    if rule_errors:
        logger.warning("rule_errors detail | user=%s medals=%s", user, rule_errors)

    return newly_earned


def get_all_medals_for_user(
    user: str,
    repo: "FeladatRepository",
    include_expired: bool = False,
) -> list[tuple[Erem, FelhasznaloErem]]:
    """Return (catalog_entry, earned_record) pairs for a user.

    Catalog is loaded from DB so it reflects any runtime additions.
    """
    earned = repo.get_eremek(user, include_expired=include_expired)
    catalog = repo.get_erem_katalogus(user)
    result: list[tuple[Erem, FelhasznaloErem]] = []
    for fe in earned:
        erem = catalog.get(fe.erem_id)
        if erem is not None:
            result.append((erem, fe))
    return result


# ---------------------------------------------------------------------------
# Rule simulation (dry-run, no DB writes)
# ---------------------------------------------------------------------------

from dataclasses import dataclass as _dataclass
from typing import Optional as _Optional


@_dataclass
class RuleSimResult:
    erem_id: str
    nev: str
    ikon: str
    result: bool
    already_earned: bool
    ismetelheto: bool
    error: _Optional[str] = None


def simulate_medal_rules(
    user: str,
    engine: "Engine",
    earned_erem_ids: set[str],
) -> list[RuleSimResult]:
    """Evaluate every registered rule for *user* without awarding anything.

    Returns one RuleSimResult per registered rule.
    """
    results: list[RuleSimResult] = []
    for erem_id, rule_fn in SZABALY_REGISTRY.items():
        erem = EREM_KATALOGUS.get(erem_id)
        nev = erem.nev if erem else erem_id
        ikon = erem.ikon if erem else "🏅"
        ismetelheto = erem.ismetelheto if erem else False
        try:
            rule_result = rule_fn(user, None, engine)
            error = None
        except Exception as exc:
            rule_result = False
            error = str(exc)
        results.append(RuleSimResult(
            erem_id=erem_id,
            nev=nev,
            ikon=ikon,
            result=bool(rule_result),
            already_earned=erem_id in earned_erem_ids,
            ismetelheto=ismetelheto,
            error=error,
        ))
    return results
