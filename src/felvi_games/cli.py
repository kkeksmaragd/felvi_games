"""
cli.py
------
Parancssori felületek a felvi_games eszközökhöz (typer).

Belépési pont:
  felvi          →  app()
    felvi info     – Konfiguráció, PDF-ek és DB állapot kiírása
    felvi scrape   – PDF-ek letöltése
    felvi parse    – PDF-ek feldolgozása DB-be
    felvi review   – AI review futtatása egy vagy több feladaton
"""
from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer

from felvi_games.config import setup_logging

# Ensure stdout/stderr can handle all Unicode characters when redirected on Windows.
# Set the console output code page to UTF-8 first so PowerShell interprets the pipe correctly.
# if sys.platform == "win32":
#     try:
#         import ctypes
#         # isolation level 1
#         ctypes.windll.kernel32.SetConsoleOutputCP()
#     except Exception:
#         pass

# for _stream in (sys.stdout, sys.stderr):
#     if hasattr(_stream, "reconfigure"):
#         try:
#             _stream.reconfigure(encoding="utf-8", errors="replace")
#         except Exception:
#             pass

setup_logging()

app = typer.Typer(
    name="felvi",
    help="Felvételi feladatsor eszközök",
    add_completion=False,
)


class EvfolyamKulcs(str, Enum):
    negy = "4"
    hat = "6"
    nyolc = "8"


class Targy(str, Enum):
    matek = "matek"
    magyar = "magyar"


# ---------------------------------------------------------------------------
# felvi info
# ---------------------------------------------------------------------------

@app.command()
def info(
    szint: Annotated[
        Optional[EvfolyamKulcs], typer.Option("--szint", help="Csak egy évfolyam: 4, 6 vagy 8")
    ] = None,
) -> None:
    """Konfiguráció, letöltött PDF-ek és DB állapot áttekintése."""
    from felvi_games.status import run as _run

    _run(szint=szint.value if szint else None)


# ---------------------------------------------------------------------------
# felvi scrape
# ---------------------------------------------------------------------------

@app.command()
def scrape(
    zip_mode: Annotated[
        bool, typer.Option("--zip", help="Bulk ZIP letöltés (gyors, minden évet egyszerre)")
    ] = False,
    years: Annotated[
        int, typer.Option("--years", help="Csak az utolsó N év (0 = mind)")
    ] = 0,
    only: Annotated[
        Optional[EvfolyamKulcs], typer.Option("--only", help="Csak egy évfolyam: 4, 6 vagy 8")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Csak listáz, nem tölt le semmit")
    ] = False,
    output: Annotated[
        Optional[Path], typer.Option("--output", help="Kimeneti mappa (alap: FELVI_EXAMS env)")
    ] = None,
) -> None:
    """Letölti a feladatsorokat az oktatas.hu-ról."""
    from felvi_games.scraper import run as _run

    _run(
        zip_mode=zip_mode,
        years=years,
        only=only.value if only else None,
        dry_run=dry_run,
        output=output,
    )


# ---------------------------------------------------------------------------
# felvi parse
# ---------------------------------------------------------------------------

@app.command()
def parse(
    year: Annotated[
        Optional[int], typer.Option("--year", help="Csak ebből az évből")
    ] = None,
    targy: Annotated[
        Optional[Targy], typer.Option("--targy", help="Tantárgy szűrő")
    ] = None,
    szint: Annotated[
        Optional[EvfolyamKulcs], typer.Option("--szint", help="Évfolyam szűrő (4/6/8)")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Ne mentse DB-be")
    ] = False,
    review: Annotated[
        bool, typer.Option("--review", help="CLI review futtatása kinyerés után")
    ] = False,
    model: Annotated[
        Optional[str], typer.Option("--model", help="LLM modell neve")
    ] = None,
    exams_dir: Annotated[
        Optional[Path], typer.Option("--exams-dir", help="PDF mappa (alap: FELVI_EXAMS env)")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="Max feldolgozandó pár (0 = mind)")
    ] = 0,
) -> None:
    """PDF párokat dolgoz fel és menti a feladatokat DB-be."""
    from felvi_games.pdf_parser import run as _run

    _run(
        year=year,
        targy=targy.value if targy else None,
        szint=szint.value if szint else None,
        dry_run=dry_run,
        review=review,
        model=model,
        exams_dir=exams_dir,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# felvi usage
# ---------------------------------------------------------------------------

@app.command("usage")
def usage(
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    user: Annotated[
        Optional[str], typer.Option("--user", help="Csak egy felhasználó adatai")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="Max. kilistázott menetszám felhasználónként")
    ] = 5,
) -> None:
    """Felhasználói aktivitás és haladás riport a játék DB-ből."""
    from sqlalchemy import case, func, select
    from sqlalchemy.orm import Session

    from felvi_games.config import get_db_path
    from felvi_games.db import (
        FelhasznaloRecord,
        MegoldasRecord,
        MenetRecord,
        get_engine,
    )

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)
    if limit < 1:
        typer.echo("[!] A --limit értéke legalább 1 legyen.")
        raise typer.Exit(code=2)

    engine = get_engine(db_path)
    with Session(engine) as sess:
        total_users = sess.scalar(select(func.count()).select_from(FelhasznaloRecord)) or 0
        total_sessions = sess.scalar(select(func.count()).select_from(MenetRecord)) or 0
        total_attempts = sess.scalar(select(func.count()).select_from(MegoldasRecord)) or 0

        attempt_rows = sess.execute(
            select(
                MegoldasRecord.felhasznalo_nev,
                func.count().label("attempts"),
                func.sum(case((MegoldasRecord.helyes.is_(True), 1), else_=0)).label("correct"),
                func.avg(MegoldasRecord.elapsed_sec).label("avg_sec"),
            )
            .where(MegoldasRecord.felhasznalo_nev != "")
            .group_by(MegoldasRecord.felhasznalo_nev)
        ).all()
        attempt_map = {
            r.felhasznalo_nev: {
                "attempts": int(r.attempts or 0),
                "correct": int(r.correct or 0),
                "avg_sec": float(r.avg_sec) if r.avg_sec is not None else None,
            }
            for r in attempt_rows
        }

        session_stmt = (
            select(
                MenetRecord.felhasznalo_nev,
                func.count(MenetRecord.id).label("sessions"),
                func.sum(MenetRecord.megoldott).label("solved"),
                func.sum(MenetRecord.feladat_limit).label("planned"),
                func.sum(MenetRecord.pont).label("points"),
                func.sum(case((MenetRecord.ended_at.is_not(None), 1), else_=0)).label("closed"),
                func.max(MenetRecord.started_at).label("last_started"),
            )
            .group_by(MenetRecord.felhasznalo_nev)
            .order_by(MenetRecord.felhasznalo_nev)
        )
        if user:
            session_stmt = session_stmt.where(MenetRecord.felhasznalo_nev == user)
        session_rows = sess.execute(session_stmt).all()

        typer.echo("\n=== Usage Report ===")
        typer.echo(f"DB: {db_path}")
        typer.echo(
            f"Users: {total_users} | Sessions: {total_sessions} | Attempts: {total_attempts}"
        )

        if not session_rows:
            if user:
                typer.echo(f"\nNincs session adat ehhez a felhasználóhoz: {user}")
            else:
                typer.echo("\nNincs session adat a DB-ben.")
            return

        typer.echo("\nPer-user summary:")
        for row in session_rows:
            solved = int(row.solved or 0)
            planned = int(row.planned or 0)
            points = int(row.points or 0)
            sessions = int(row.sessions or 0)
            closed = int(row.closed or 0)
            progress_pct = (100.0 * solved / planned) if planned else 0.0

            a = attempt_map.get(row.felhasznalo_nev, {"attempts": 0, "correct": 0, "avg_sec": None})
            attempts = a["attempts"]
            correct = a["correct"]
            accuracy = (100.0 * correct / attempts) if attempts else 0.0
            avg_sec = a["avg_sec"]
            avg_sec_text = f"{avg_sec:.1f}s" if avg_sec is not None else "-"

            typer.echo(
                "- "
                f"{row.felhasznalo_nev}: "
                f"sessions={sessions}, closed={closed}, "
                f"progress={solved}/{planned} ({progress_pct:.1f}%), "
                f"points={points}, attempts={attempts}, accuracy={accuracy:.1f}%, avg_time={avg_sec_text}, "
                f"last_started={row.last_started}"
            )

            details = sess.execute(
                select(
                    MenetRecord.id,
                    MenetRecord.targy,
                    MenetRecord.szint,
                    MenetRecord.megoldott,
                    MenetRecord.feladat_limit,
                    MenetRecord.pont,
                    MenetRecord.started_at,
                    MenetRecord.ended_at,
                )
                .where(MenetRecord.felhasznalo_nev == row.felhasznalo_nev)
                .order_by(MenetRecord.started_at.desc())
                .limit(limit)
            ).all()

            for d in details:
                done_flag = "done" if d.ended_at else "open"
                typer.echo(
                    "    "
                    f"#{d.id} [{done_flag}] {d.targy}/{d.szint} "
                    f"{d.megoldott}/{d.feladat_limit} pont={d.pont} "
                    f"start={d.started_at}"
                )

        typer.echo()


# ---------------------------------------------------------------------------
# felvi medals
# ---------------------------------------------------------------------------

@app.command("medals")
def medals(
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    user: Annotated[
        Optional[str], typer.Option("--user", help="Szűrés egy felhasználóra")
    ] = None,
    list_all: Annotated[
        bool, typer.Option("--list", help="Az összes lehetséges érem katalógusának kiírása")
    ] = False,
    include_expired: Annotated[
        bool, typer.Option("--expired", help="Lejárt ideiglenes érmek megjelenítése is")
    ] = False,
    dynamic: Annotated[
        bool, typer.Option("--dynamic", help="Csak a dinamikus (LLM-generált) éremszabályok listázása, időrend szerint")
    ] = False,
    conditions: Annotated[
        bool, typer.Option("--conditions", help="Dinamikus feltételek listázása (felhasználóra szűrhető)")
    ] = False,
    today: Annotated[
        bool, typer.Option("--today", help="--conditions esetén csak a ma létrehozott dinamikus érmek")
    ] = False,
    generate_dry_run: Annotated[
        bool, typer.Option("--generate-dry-run", help="Új dinamikus éremjavaslat generálása mentés nélkül")
    ] = False,
    generate: Annotated[
        bool, typer.Option("--generate", help="Új dinamikus érem generálása és mentése a katalógusba")
    ] = False,
    generator_inputs: Annotated[
        bool, typer.Option("--generator-inputs", help="A dinamikus éremgenerátornak átadott bemeneti adatok kiírása")
    ] = False,
    window_hours: Annotated[
        int, typer.Option("--window-hours", help="Dry-run javaslat időablaka órában (1-18)")
    ] = 18,
    delete_id: Annotated[
        Optional[str], typer.Option("--delete-id", help="Érem törlése az id alapján (csak dinamikus/privát érmekre)")
    ] = None,
) -> None:
    """Érmek / achievements: katalógus és felhasználói haladás."""
    import re
    from datetime import datetime, timezone

    from felvi_games.achievements import (
        EREM_KATALOGUS,
        _eval_dynamic_condition,
        _count_dynamic_condition,
        get_all_medals_for_user,
    )
    from felvi_games.ai import generate_daily_insight
    from felvi_games.config import get_db_path
    from felvi_games.db import FelhasznaloRecord, FeladatRepository, get_engine
    from felvi_games.models import Erem
    from felvi_games.progress_check import estimate_close_medals, get_user_stats
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session
    import json as _json

    def _collect_generator_inputs(
        repo: FeladatRepository,
        player: str,
        hours: int,
    ) -> dict[str, object]:
        stats = get_user_stats(player, repo)
        close_medals = estimate_close_medals(player, repo, stats)
        earned_count = len(repo.get_eremek(player, include_expired=True))
        close_payload = [
            {
                "id": cm.erem.id,
                "nev": cm.erem.nev,
                "ikon": cm.erem.ikon,
                "kategoria": cm.erem.kategoria,
                "progress": round(cm.progress, 3),
                "progress_pct": int(cm.progress * 100),
                "hint": cm.hint,
            }
            for cm in close_medals
        ]
        return {
            "user": player,
            "window_hours": hours,
            "earned_count": earned_count,
            "stats": stats,
            "close_medals": close_payload,
        }

    if generate and generate_dry_run:
        typer.echo("[!] A --generate és --generate-dry-run együtt nem használható.")
        raise typer.Exit(code=2)

    if delete_id:
        db_path = db or get_db_path()
        if not db_path.exists():
            typer.echo(f"[!] DB nem található: {db_path}")
            raise typer.Exit(code=1)
        engine = get_engine(db_path)
        with Session(engine) as s:
            row = s.execute(
                text("SELECT id, nev, ikon, condition_json FROM eremek WHERE id = :eid"),
                {"eid": delete_id},
            ).first()
            if not row:
                typer.echo(f"[!] Nem található érem ezzel az id-vel: {delete_id}")
                raise typer.Exit(code=1)
            if not row.condition_json:
                typer.echo(f"[!] Ez nem dinamikus érem (nincs condition_json), törlés megtagadva: {delete_id}")
                raise typer.Exit(code=1)
            s.execute(text("DELETE FROM eremek WHERE id = :eid"), {"eid": delete_id})
            s.commit()
        typer.echo(f"✅ Törölve: {row.ikon}  {row.nev}  (id={delete_id})")
        return

    if generator_inputs:
        if not user:
            typer.echo("[!] A --generator-inputs használatához add meg a --user opciót.")
            raise typer.Exit(code=2)
        if window_hours < 1 or window_hours > 18:
            typer.echo("[!] A --window-hours értéke 1 és 18 közé essen.")
            raise typer.Exit(code=2)

        db_path = db or get_db_path()
        if not db_path.exists():
            typer.echo(f"[!] DB nem található: {db_path}")
            raise typer.Exit(code=1)

        repo = FeladatRepository(db_path)
        payload = _collect_generator_inputs(repo, user, window_hours)

        typer.echo(f"\n=== Dinamikus generátor bemenet  (DB: {db_path}) ===\n")
        typer.echo(_json.dumps(payload, ensure_ascii=False, indent=2))
        typer.echo("\nMegjegyzés: ez a strukturált payload megy a dinamikus éremgenerátorhoz a prompt részeként.\n")
        return

    if generate_dry_run or generate:
        if not user:
            typer.echo("[!] A --generate/--generate-dry-run használatához add meg a --user opciót.")
            raise typer.Exit(code=2)
        if window_hours < 1 or window_hours > 18:
            typer.echo("[!] A --window-hours értéke 1 és 18 közé essen.")
            raise typer.Exit(code=2)

        db_path = db or get_db_path()
        if not db_path.exists():
            typer.echo(f"[!] DB nem található: {db_path}")
            raise typer.Exit(code=1)

        repo = FeladatRepository(db_path)
        generator_payload = _collect_generator_inputs(repo, user, window_hours)
        stats = generator_payload["stats"]
        close = estimate_close_medals(user, repo, stats)
        earned_count = int(generator_payload["earned_count"])

        insight = generate_daily_insight(
            user,
            stats,
            close,
            earned_count,
            window_hours=window_hours,
        )

        title = "Dinamikus érem generálás" if generate else "Dinamikus érem dry-run"
        typer.echo(f"\n=== {title}  (DB: {db_path}) ===\n")
        typer.echo(f"👤 Felhasználó: {user}")
        typer.echo(f"🕒 Időablak:    {window_hours} óra")
        typer.echo(f"\nÜzenet:\n  {insight.get('greeting', '-')}")

        nm = insight.get("new_medal")
        if not nm:
            typer.echo("\nJavasolt új érem: nincs (new_medal = null)\n")
            return

        cond = nm.get("condition") if isinstance(nm, dict) else None
        typer.echo("\nJavasolt új érem:")
        typer.echo(f"  Név:       {nm.get('nev', '-')}")
        typer.echo(f"  Ikon:      {nm.get('ikon', '🏅')}")
        typer.echo(f"  Kategória: {nm.get('kategoria', '-')}")
        typer.echo(f"  Leírás:    {nm.get('leiras', '-')}")
        typer.echo(f"  Feltétel:  {_json.dumps(cond, ensure_ascii=False)}")
        # Check if the condition is ALREADY satisfied at creation time (bad – n should require future effort)
        try:
            already_done = _eval_dynamic_condition(user, cond or {}, repo._engine, valid_from=datetime.now(timezone.utc))
            if already_done:
                typer.echo("  ⚠️  Figyelem: a feltétel már most teljesül – nem jó kihívás!")
                if generate:
                    typer.echo("  ❌ Mentés megtagadva: a kihívás már teljesítve.")
                    typer.echo("     Próbáld újra: felvi medals --generate --user ...\n")
                    return
            else:
                typer.echo("  ✅ A feltétel még nem teljesül – jó jövőbeli kihívás.")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"  Ellenőrzés: hiba ({exc})")

        if generate:
            safe_user = re.sub(r"[^a-z0-9]+", "_", user.lower()).strip("_") or "user"
            erem_id = f"dyn_{safe_user}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            erem = Erem(
                id=erem_id,
                nev=str(nm.get("nev", "Napi kihívás")),
                leiras=str(nm.get("leiras", "Időkorlátos napi kihívás")),
                ikon=str(nm.get("ikon", "🏅")),
                kategoria=str(nm.get("kategoria", "teljesitmeny")),
                ideiglenes=True,
                ervenyes_napig=int(nm.get("ervenyes_napig", 1) or 1),
                ismetelheto=False,
                privat=True,
                cel_felhasznalo=user,
                condition=cond if isinstance(cond, dict) else None,
            )
            repo.upsert_erem(erem)
            typer.echo(f"\n✅ Mentve: id={erem.id}")
            typer.echo("   A feltétel mostantól látható a --conditions listában.")
            typer.echo()
        else:
            typer.echo("\n(Mentés nem történt, ez csak dry-run.)\n")
        return

    if conditions:
        db_path = db or get_db_path()
        if not db_path.exists():
            typer.echo(f"[!] DB nem található: {db_path}")
            raise typer.Exit(code=1)

        engine = get_engine(db_path)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        where_parts = ["condition_json IS NOT NULL", "condition_json != ''"]
        params: dict[str, object] = {}
        if user:
            where_parts.append("(cel_felhasznalo IS NULL OR cel_felhasznalo = :u)")
            params["u"] = user
        if today:
            where_parts.append("created_at >= :today_start")
            params["today_start"] = today_start

        where_sql = " AND ".join(where_parts)
        with Session(engine) as s:
            rows = s.execute(
                text(
                    "SELECT id, nev, ikon, kategoria, condition_json, created_at, cel_felhasznalo "
                    f"FROM eremek WHERE {where_sql} ORDER BY created_at DESC"
                ),
                params,
            ).all()

        who = f"  user={user}" if user else ""
        only_today = "  today_only" if today else ""
        typer.echo(f"\n=== Dinamikus feltételek  (DB: {db_path}){who}{only_today}  összesen: {len(rows)} ===\n")
        if not rows:
            typer.echo("  (Nincs találat.)")
            if user:
                typer.echo("  Tipp: próbáld: felvi medals --generate-dry-run --user \"NÉV\"")
                typer.echo("        vagy mentéshez: felvi medals --generate --user \"NÉV\"")
            typer.echo()
            return

        repo = FeladatRepository(db_path)
        for r in rows:
            cel = f" → {r.cel_felhasznalo}" if r.cel_felhasznalo else ""
            typer.echo(f"  {r.created_at}  {r.ikon}  {r.nev}  [{r.kategoria}]{cel}")
            typer.echo(f"    id: {r.id}")
            try:
                cond = _json.loads(r.condition_json)
                typer.echo(f"    condition: {_json.dumps(cond, ensure_ascii=False)}")
                if user:
                    try:
                        from datetime import datetime, timezone as _tz
                        vf = r.created_at
                        if isinstance(vf, str):
                            vf = datetime.fromisoformat(vf)
                        if vf is not None and vf.tzinfo is None:
                            vf = vf.replace(tzinfo=_tz.utc)
                        ok = _eval_dynamic_condition(user, cond, repo._engine, valid_from=vf)
                        cur, target = _count_dynamic_condition(user, cond, repo._engine, valid_from=vf)
                        progress_str = ""
                        if cur is not None and target is not None:
                            bar_filled = min(cur, target)
                            bar = "█" * bar_filled + "░" * max(0, target - bar_filled)
                            progress_str = f"  [{bar}]  {cur}/{target}"
                        status = "✅ teljesítve" if ok else "⏳ folyamatban"
                        typer.echo(f"    haladás({user}): {status}{progress_str}")
                        typer.echo(f"    (számít: {vf} óta)")
                    except Exception as exc:  # noqa: BLE001
                        typer.echo(f"    teljesül({user}): hiba ({exc})")
            except Exception:
                typer.echo(f"    condition_json: {r.condition_json}")
        typer.echo()
        return

    if dynamic:
        db_path = db or get_db_path()
        if not db_path.exists():
            typer.echo(f"[!] DB nem található: {db_path}")
            raise typer.Exit(code=1)
        engine = get_engine(db_path)
        with Session(engine) as s:
            rows = s.execute(
                text(
                    "SELECT id, nev, ikon, kategoria, condition_json, created_at, cel_felhasznalo "
                    "FROM eremek WHERE condition_json IS NOT NULL AND condition_json != '' "
                    "ORDER BY created_at DESC"
                )
            ).all()
        typer.echo(f"\n=== Dinamikus éremszabályok  (DB: {db_path})  összesen: {len(rows)} ===\n")
        if not rows:
            typer.echo("  (Még nem jött létre dinamikus érem.)\n")
            return
        for r in rows:
            cel = f"  → {r.cel_felhasznalo}" if r.cel_felhasznalo else ""
            typer.echo(f"  {r.created_at}  {r.ikon}  {r.nev}  [{r.kategoria}]{cel}")
            typer.echo(f"    id: {r.id}")
            try:
                cond = _json.loads(r.condition_json)
                typer.echo(f"    condition: {_json.dumps(cond, ensure_ascii=False)}")
            except Exception:
                typer.echo(f"    condition_json: {r.condition_json}")
        typer.echo()
        return

    if list_all:
        typer.echo("\n=== Érem katalógus ===")
        by_cat: dict[str, list] = {}
        for e in EREM_KATALOGUS.values():
            by_cat.setdefault(e.kategoria, []).append(e)
        for cat in sorted(by_cat):
            typer.echo(f"\n{cat.upper()}")
            for e in by_cat[cat]:
                flags = []
                if e.ismetelheto:
                    flags.append("ismételhető")
                if e.ideiglenes:
                    flags.append(f"ideiglenes ({e.ervenyes_napig}n)")
                flag_str = f"  [{', '.join(flags)}]" if flags else ""
                typer.echo(f"  {e.ikon}  {e.nev}{flag_str}")
                typer.echo(f"     {e.leiras}")
        typer.echo()
        return

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)
    engine = get_engine(db_path)

    with Session(engine) as sess:
        if user:
            users = [user]
        else:
            users = list(sess.scalars(select(FelhasznaloRecord.nev).order_by(FelhasznaloRecord.nev)))

    typer.echo(f"\n=== Earned Medals  (DB: {db_path}) ===\n")
    for nev in users:
        pairs = get_all_medals_for_user(nev, repo, include_expired=include_expired)
        typer.echo(f"👤 {nev}  ({len(pairs)} érem)")
        if not pairs:
            typer.echo("   (még nincs érem)")
        else:
            for erem, fe in sorted(pairs, key=lambda p: p[0].kategoria):
                szamlalo = f" ×{fe.szamlalo}" if fe.szamlalo > 1 else ""
                lejarat = ""
                if fe.lejarat:
                    from datetime import timezone as _tz
                    from datetime import datetime as _dt
                    days_left = (_dt.now(_tz.utc) - fe.lejarat.replace(tzinfo=_tz.utc) if fe.lejarat.tzinfo is None else fe.lejarat).days
                    lejarat = f"  [lejár: {fe.lejarat.strftime('%Y-%m-%d')}]"
                typer.echo(
                    f"  {erem.ikon}  {erem.nev}{szamlalo}"
                    f"  [{erem.kategoria}]{lejarat}"
                )
                typer.echo(f"      Szerezve: {fe.szerzett.strftime('%Y-%m-%d %H:%M')}")
        typer.echo()


# ---------------------------------------------------------------------------
# felvi medal-assets
# ---------------------------------------------------------------------------

@app.command("medal-assets")
def medal_assets_cmd(
    erem_id: Annotated[
        Optional[str], typer.Option("--erem-id", help="Csak ehhez az éremhez generál")
    ] = None,
    kinds: Annotated[
        str, typer.Option("--kinds", help="Vesszővel elválasztott asset típusok: kep,hang")
    ] = "kep,hang",
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Meglévő asseteket is újra generálja")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Csak listázza, mi hiányzik – nem generál")
    ] = False,
    status: Annotated[
        bool, typer.Option("--status", help="Meglévő asset fájlok állapota")
    ] = False,
) -> None:
    """Medal asset képek és hangok generálása (DALL-E 3 + TTS)."""
    from felvi_games.achievements import EREM_KATALOGUS
    from felvi_games.medal_assets import generate_medal_assets, medal_asset_exists

    kind_list = [k.strip() for k in kinds.split(",") if k.strip()]
    catalog = (
        {erem_id: EREM_KATALOGUS[erem_id]}
        if erem_id and erem_id in EREM_KATALOGUS
        else EREM_KATALOGUS
    )
    if erem_id and erem_id not in EREM_KATALOGUS:
        typer.echo(f"[!] Ismeretlen érem: {erem_id}")
        raise typer.Exit(code=1)

    if status:
        typer.echo("\n=== Medal asset állapot ===\n")
        typer.echo(f"  {'Érem':<28} {'kep':>5}  {'hang':>5}  {'gif':>5}")
        typer.echo("  " + "-" * 50)
        for eid, erem in catalog.items():
            cols = {k: ("✓" if medal_asset_exists(eid, k) else "✗") for k in ("kep", "hang", "gif")}
            typer.echo(f"  {erem.ikon} {erem.nev:<26} {cols['kep']:>5}  {cols['hang']:>5}  {cols['gif']:>5}")
        typer.echo()
        return

    typer.echo(f"\nGenerálandó: {', '.join(kind_list)}")
    typer.echo(f"Érmek: {len(catalog)}  |  overwrite={overwrite}  |  dry_run={dry_run}\n")

    for eid, erem in catalog.items():
        missing = [k for k in kind_list if k != "gif" and (overwrite or not medal_asset_exists(eid, k))]
        if not missing:
            typer.echo(f"  ✓ {erem.ikon} {erem.nev} – már kész")
            continue
        if dry_run:
            typer.echo(f"  ? {erem.ikon} {erem.nev} – hiányzik: {', '.join(missing)}")
            continue
        typer.echo(f"  ⏳ {erem.ikon} {erem.nev} – generálás: {', '.join(missing)} …")
        try:
            saved = generate_medal_assets(erem, kinds=tuple(missing), overwrite=overwrite)
            for k, path in saved.items():
                typer.echo(f"      ✓ {k}: {path}")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"      ✗ hiba: {exc}")

    typer.echo()


# ---------------------------------------------------------------------------
# felvi medal-add  /  medal-edit  /  medal-grant  /  medal-delete
# ---------------------------------------------------------------------------

def _get_repo_for_medals(db: Optional[Path]) -> "FeladatRepository":
    from felvi_games.db import FeladatRepository
    return FeladatRepository(db)


@app.command("medal-add")
def medal_add_cmd(
    db: Annotated[Optional[Path], typer.Option("--db", help="DB fájl útvonala")] = None,
    id: Annotated[str, typer.Option("--id", help="Egyedi slug, pl. 'kivalosag_2026'")] = ...,
    nev: Annotated[str, typer.Option("--nev", help="Magyar megjelenítési név")] = ...,
    leiras: Annotated[str, typer.Option("--leiras", help="Rövid leírás")] = ...,
    ikon: Annotated[str, typer.Option("--ikon", help="Emoji ikon")] = "🏅",
    kategoria: Annotated[str, typer.Option("--kategoria")] = "teljesitmeny",
    ideiglenes: Annotated[bool, typer.Option("--ideiglenes")] = False,
    ervenyes_napig: Annotated[Optional[int], typer.Option("--ervenyes-napig")] = None,
    ismetelheto: Annotated[bool, typer.Option("--ismetelheto")] = False,
    privat: Annotated[bool, typer.Option("--privat", help="Privát érem (csak a célfelhasználónak látható)")] = False,
    cel_felhasznalo: Annotated[Optional[str], typer.Option("--cel-felhasznalo", help="Privát érem célfelhasználója")] = None,
) -> None:
    """Új érem hozzáadása a katalógushoz (azonnal érvényes, újraindítás nélkül)."""
    from felvi_games.models import Erem

    if privat and not cel_felhasznalo:
        typer.echo("[!] Privát éremnél kötelező megadni --cel-felhasznalo-t.")
        raise typer.Exit(code=1)

    repo = _get_repo_for_medals(db)
    catalog = repo.get_erem_katalogus()
    if id in catalog:
        typer.echo(f"[!] Az '{id}' azonosítójú érem már létezik. Használd a medal-edit parancsot.")
        raise typer.Exit(code=1)

    erem = Erem(
        id=id, nev=nev, leiras=leiras, ikon=ikon, kategoria=kategoria,
        ideiglenes=ideiglenes, ervenyes_napig=ervenyes_napig,
        ismetelheto=ismetelheto, privat=privat, cel_felhasznalo=cel_felhasznalo,
    )
    repo.upsert_erem(erem)
    scope = f"privát → {cel_felhasznalo}" if privat else "globális"
    typer.echo(f"✓ Érem hozzáadva: {ikon} {nev}  [{scope}]  (id={id})")


@app.command("medal-edit")
def medal_edit_cmd(
    db: Annotated[Optional[Path], typer.Option("--db")] = None,
    id: Annotated[str, typer.Option("--id", help="Szerkesztendő érem azonosítója")] = ...,
    nev: Annotated[Optional[str], typer.Option("--nev")] = None,
    leiras: Annotated[Optional[str], typer.Option("--leiras")] = None,
    ikon: Annotated[Optional[str], typer.Option("--ikon")] = None,
    kategoria: Annotated[Optional[str], typer.Option("--kategoria")] = None,
    ideiglenes: Annotated[Optional[bool], typer.Option("--ideiglenes/--nem-ideiglenes")] = None,
    ervenyes_napig: Annotated[Optional[int], typer.Option("--ervenyes-napig")] = None,
    ismetelheto: Annotated[Optional[bool], typer.Option("--ismetelheto/--nem-ismetelheto")] = None,
    privat: Annotated[Optional[bool], typer.Option("--privat/--globalis")] = None,
    cel_felhasznalo: Annotated[Optional[str], typer.Option("--cel-felhasznalo")] = None,
) -> None:
    """Meglévő érem metaadatainak szerkesztése (újraindítás nélkül érvényes)."""
    import dataclasses

    from felvi_games.db import EremRecord
    from sqlalchemy.orm import Session as _Session

    repo = _get_repo_for_medals(db)
    with _Session(repo._engine) as s:
        rec = s.get(EremRecord, id)

    if rec is None:
        typer.echo(f"[!] Ismeretlen érem azonosító: '{id}'")
        raise typer.Exit(code=1)

    existing = rec.to_domain()
    updated = dataclasses.replace(
        existing,
        nev=nev if nev is not None else existing.nev,
        leiras=leiras if leiras is not None else existing.leiras,
        ikon=ikon if ikon is not None else existing.ikon,
        kategoria=kategoria if kategoria is not None else existing.kategoria,
        ideiglenes=ideiglenes if ideiglenes is not None else existing.ideiglenes,
        ervenyes_napig=ervenyes_napig if ervenyes_napig is not None else existing.ervenyes_napig,
        ismetelheto=ismetelheto if ismetelheto is not None else existing.ismetelheto,
        privat=privat if privat is not None else existing.privat,
        cel_felhasznalo=cel_felhasznalo if cel_felhasznalo is not None else existing.cel_felhasznalo,
    )
    repo.upsert_erem(updated)
    typer.echo(f"✓ Érem frissítve: {updated.ikon} {updated.nev}  (id={id})")


@app.command("medal-grant")
def medal_grant_cmd(
    db: Annotated[Optional[Path], typer.Option("--db")] = None,
    id: Annotated[str, typer.Option("--id", help="Érem azonosítója")] = ...,
    felhasznalo: Annotated[str, typer.Option("--felhasznalo", help="Felhasználó neve")] = ...,
    ervenyes_napig: Annotated[Optional[int], typer.Option("--ervenyes-napig", help="Lejárat napokban")] = None,
) -> None:
    """Érem manuális odaítélése egy felhasználónak (privát érmekhez hasznos)."""
    from datetime import timedelta
    from felvi_games.db import EremRecord
    from sqlalchemy.orm import Session as _Session

    repo = _get_repo_for_medals(db)
    with _Session(repo._engine) as s:
        rec = s.get(EremRecord, id)
    if rec is None:
        typer.echo(f"[!] Ismeretlen érem azonosító: '{id}'")
        raise typer.Exit(code=1)

    expires_at = None
    if ervenyes_napig:
        from datetime import datetime, timezone
        expires_at = datetime.now(timezone.utc) + timedelta(days=ervenyes_napig)

    fe = repo.grant_erem(felhasznalo, id, lejarat_at=expires_at)
    erem = rec.to_domain()
    typer.echo(f"✓ {erem.ikon} {erem.nev} → {felhasznalo}  (szerzett #{fe.szamlalo})")
    if expires_at:
        typer.echo(f"  Lejárat: {expires_at.strftime('%Y-%m-%d')}")


@app.command("medal-delete")
def medal_delete_cmd(
    db: Annotated[Optional[Path], typer.Option("--db")] = None,
    id: Annotated[str, typer.Option("--id", help="Törlendő érem azonosítója")] = ...,
    force: Annotated[bool, typer.Option("--force", help="Megerősítés kihagyása")] = False,
) -> None:
    """Érem törlése a katalógusból (a kiosztott érmeket NEM törli)."""
    repo = _get_repo_for_medals(db)
    if not force:
        confirm = typer.confirm(f"Biztosan törlöd az '{id}' érmet a katalógusból?")
        if not confirm:
            typer.echo("Megszakítva.")
            raise typer.Exit()
    removed = repo.delete_erem(id)
    if removed:
        typer.echo(f"✓ Érem törölve: {id}")
    else:
        typer.echo(f"[!] Nem található: {id}")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# felvi stats
# ---------------------------------------------------------------------------

@app.command("stats")
def stats_cmd(
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
) -> None:
    """Feladatok és megoldások összefoglaló statisztikája a DB-ből."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from felvi_games.config import get_db_path
    from felvi_games.db import FeladatRecord, MegoldasRecord, get_engine

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    engine = get_engine(db_path)
    with Session(engine) as sess:
        total_feladatok = sess.scalar(select(func.count()).select_from(FeladatRecord)) or 0
        total_attempts = sess.scalar(select(func.count()).select_from(MegoldasRecord)) or 0
        total_correct = sess.scalar(
            select(func.count()).select_from(MegoldasRecord).where(MegoldasRecord.helyes.is_(True))
        ) or 0
        accuracy = round(100.0 * total_correct / total_attempts, 1) if total_attempts else 0.0

        by_targy_szint = sess.execute(
            select(FeladatRecord.targy, FeladatRecord.szint, func.count().label("n"))
            .group_by(FeladatRecord.targy, FeladatRecord.szint)
            .order_by(FeladatRecord.targy, FeladatRecord.szint)
        ).all()

        by_ev = sess.execute(
            select(FeladatRecord.ev, func.count().label("n"))
            .group_by(FeladatRecord.ev)
            .order_by(FeladatRecord.ev)
        ).all()

        reviewed = sess.scalar(
            select(func.count()).select_from(FeladatRecord).where(FeladatRecord.review_elvegezve.is_(True))
        ) or 0

    typer.echo(f"\n=== DB Statistics  ({db_path}) ===\n")
    typer.echo(f"  Feladatok összesen:   {total_feladatok}")
    typer.echo(f"  Felülvizsgált:        {reviewed} / {total_feladatok}")
    typer.echo(f"  Megoldási kísérletek: {total_attempts}")
    typer.echo(f"  Helyes válaszok:      {total_correct}  ({accuracy:.1f}%)")

    if by_targy_szint:
        typer.echo("\n  Tárgy / Szint:")
        for row in by_targy_szint:
            typer.echo(f"    {row.targy:<10} {row.szint:<6}  {row.n} feladat")

    if by_ev:
        typer.echo("\n  Évenkénti bontás:")
        for row in by_ev:
            label = str(row.ev) if row.ev is not None else "(ismeretlen)"
            typer.echo(f"    {label:<10}  {row.n} feladat")

    typer.echo()


# ---------------------------------------------------------------------------
# felvi wrong  – hibásan megoldott feladatok listája
# ---------------------------------------------------------------------------

@app.command("wrong")
def wrong_cmd(
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    user: Annotated[
        Optional[str], typer.Option("--user", help="Szűrés egy felhasználóra")
    ] = None,
    targy: Annotated[
        Optional[Targy], typer.Option("--targy", help="Tantárgy szűrő")
    ] = None,
    szint: Annotated[
        Optional[EvfolyamKulcs], typer.Option("--szint", help="Évfolyam szűrő (4/6/8)")
    ] = None,
    min_hibas: Annotated[
        int, typer.Option("--min-hibas", help="Csak legalább ennyi hibás kísérlettel rendelkező feladatok")
    ] = 1,
    limit: Annotated[
        int, typer.Option("--limit", help="Max. kilistázott feladatok száma (0 = mind)")
    ] = 20,
    detail: Annotated[
        bool, typer.Option("--detail", help="A ténylegesen beírt hibás válaszok is jelenjenek meg")
    ] = False,
    output: Annotated[
        Optional[Path], typer.Option("--output", help="Kimenet fájl útvonala (üres = stdout)")
    ] = None,
) -> None:
    """Feladatok, amelyekre legalább egy hibás választ adtak (legtöbbet rontottak elöl)."""
    from collections import Counter

    from felvi_games.config import get_db_path
    from felvi_games.db import FeladatRepository

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)
    rows = repo.get_wrong_feladatok(
        felhasznalo_nev=user,
        targy=targy.value if targy else None,
        szint=szint.value if szint else None,
        min_hibas=min_hibas,
        limit=limit,
        include_wrong_answers=detail,
    )

    scope = f"  (user={user})" if user else ""
    lines = [f"\n=== Hibásan megoldott feladatok  (DB: {db_path}){scope} ===\n"]

    if not rows:
        lines.append("  Nincs találat (még senki sem rontott el egy feladatot sem ebben a körben).")
        lines.append("")
    else:
        for r in rows:
            ev_label = str(r.ev) if r.ev else "?"
            tipus = r.feladat_tipus or "-"
            kerdes_short = (r.kerdes[:90] + "…") if len(r.kerdes) > 90 else r.kerdes
            helyes_short = (r.helyes_valasz[:50] + "…") if len(r.helyes_valasz) > 50 else r.helyes_valasz

            lines.append(
                f"  [{r.targy}/{r.szint}/{ev_label}] {tipus}  "
                f"hibás: {r.hibas_db}/{r.osszes_db}  ({r.rontas_pct:.0f}% rontás)"
            )
            lines.append(f"    Kérdés:        {kerdes_short}")
            lines.append(f"    Helyes válasz: {helyes_short}")
            lines.append(f"    ID:            {r.feladat_id}")

            if detail and r.hibas_valaszok:
                cnt = Counter(r.hibas_valaszok)
                parts = [f'"{v}"×{c}' if c > 1 else f'"{v}"' for v, c in cnt.most_common()]
                lines.append(f"    Hibás válaszok: {', '.join(parts)}")

            lines.append("")

        lines.append(f"  Összesen: {len(rows)} feladat listázva.\n")

    output_text = "\n".join(lines)
    if output:
        output.write_text(output_text, encoding="utf-8")
        typer.echo(f"✓ Kiírva: {output}")
    else:
        typer.echo(output_text, nl=False)


# ---------------------------------------------------------------------------
# felvi check-answer  – GPT-alapú válaszellenőrzés egy feladatra
# ---------------------------------------------------------------------------

@app.command("check-answer")
def check_answer_cmd(
    feladat_id: Annotated[str, typer.Argument(help="Feladat ID (pl. mag4_2021_3_8_a)")],
    valasz: Annotated[str, typer.Argument(help="Ellenőrizendő válasz")],
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    apply_latest: Annotated[
        bool,
        typer.Option(
            "--apply-latest",
            help="A legfrissebb, tárolt válaszkísérletet újraértékeli ezzel az eredménnyel"
        ),
    ] = False,
    user: Annotated[
        Optional[str],
        typer.Option("--user", help="Felhasználó szűrő --apply-latest használatakor"),
    ] = None,
) -> None:
    """GPT-tel ellenőriz egy választ egy adott feladatra.

    Alapból csak kiírja az eredményt. ``--apply-latest`` esetén a legfrissebb,
    eltárolt megoldásra rá is menti az újraértékelt pontszámot.
    """
    from felvi_games.ai import check_answer
    from felvi_games.config import get_db_path
    from felvi_games.db import FeladatRecord, FeladatRepository
    from sqlalchemy.orm import Session

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)
    with Session(repo._engine) as s:
        f = s.get(FeladatRecord, feladat_id)

    if not f:
        typer.echo(f"[!] Feladat nem található: {feladat_id}")
        raise typer.Exit(code=1)

    typer.echo(f"\n=== GPT válaszellenőrzés ===\n")
    typer.echo(f"  Feladat:       {feladat_id}  [{f.targy}/{f.szint}]")
    typer.echo(f"  Kérdés:        {f.kerdes[:120]}")
    typer.echo(f"  Helyes válasz: {f.helyes_valasz}")

    elfogadott = None
    if f.elfogadott_valaszok:
        import json as _json
        try:
            elfogadott = _json.loads(f.elfogadott_valaszok)
            typer.echo(f"  Elfogadott:    {', '.join(elfogadott[:5])}{'…' if len(elfogadott) > 5 else ''}")
        except Exception:
            pass

    typer.echo(f"  Beküldött:     {valasz}")
    typer.echo("")

    with typer.progressbar(length=1, label="GPT értékel") as progress:
        ert = check_answer(
            f.kerdes,
            f.helyes_valasz,
            valasz,
            f.magyarazat,
            elfogadott_valaszok=elfogadott,
            feladat_tipus=f.feladat_tipus,
            max_pont=f.max_pont,
            reszpontozas=f.reszpontozas,
        )
        progress.update(1)

    eredmeny = "✅ HELYES" if ert.helyes else ("⚠️  RÉSZLEGES" if ert.pont > 0 else "❌ HELYTELEN")
    typer.echo(f"  Eredmény:      {eredmeny}  ({ert.pont}/{f.max_pont} pont)")
    typer.echo(f"  Visszajelzés:  {ert.visszajelzes}")
    typer.echo("")

    if apply_latest:
        megoldas_id = repo.get_latest_megoldas_id(
            feladat_id,
            felhasznalo_nev=user,
            adott_valasz=valasz,
        )
        if megoldas_id is None:
            who = f" user={user}" if user else ""
            typer.echo(f"[!] Nem található eltárolt kísérlet ehhez: {feladat_id}{who}")
            raise typer.Exit(code=1)

        rv = repo.reevaluate_megoldas(
            megoldas_id,
            ertekeles=ert,
            source="cli_check_answer",
            note="Újraértékelés check-answer parancsból",
        )
        typer.echo("=== Újraértékelés mentve ===")
        typer.echo(f"  Megoldás ID:   {rv['megoldas_id']}")
        typer.echo(f"  Pontszám:      {rv['old_pont']} → {rv['new_pont']} / {rv['max_pont']}")
        if rv["deferred_reward"]:
            typer.echo("  Jutalom:       Függőben (következő interakciónál ellenőrizve)")
        else:
            typer.echo("  Jutalom:       Nincs új, függő reevaluation-jutalom")
        typer.echo("")


# ---------------------------------------------------------------------------
# felvi medal-check  – dinamikus érem-feltételek újraértékelése
# ---------------------------------------------------------------------------

@app.command("medal-check")
def medal_check_cmd(
    user: Annotated[str, typer.Argument(help="Felhasználó neve (pl. 'Lóri')")],
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
) -> None:
    """Kiértékeli az összes dinamikus érem-feltételt és kiosztja a teljesített érmeket.

    Hasznos ha az alkalmazás nem osztotta ki automatikusan a megszerzett érmeket.
    """
    from felvi_games.achievements import check_new_medals
    from felvi_games.config import get_db_path
    from felvi_games.db import FeladatRepository

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)
    typer.echo(f"\n=== Érem-feltételek ellenőrzése: {user}  (DB: {db_path}) ===\n")

    earned = check_new_medals(user, None, repo)

    if earned:
        typer.echo(f"  ✅ Kiosztott érmek ({len(earned)} db):")
        for erem in earned:
            typer.echo(f"    • {erem.ikon}  {erem.nev}  [{erem.kategoria}]")
    else:
        typer.echo("  Nincs új teljesített érem.")
    typer.echo("")


# ---------------------------------------------------------------------------
# felvi reeval  – GPT-alapú újraértékelés parancssori eszköz
# ---------------------------------------------------------------------------

@app.command("reeval")
def reeval_cmd(
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    user: Annotated[
        Optional[str], typer.Option("--user", help="Szűrés egy felhasználóra")
    ] = None,
    feladat_id: Annotated[
        Optional[str], typer.Option("--feladat-id", help="Szűrés egy feladatra")
    ] = None,
    megoldas_id: Annotated[
        Optional[int], typer.Option("--id", help="Egy konkrét megoldás újraértékelése ID alapján")
    ] = None,
    pending: Annotated[
        bool, typer.Option("--pending", help="Csak függő jutalom-feldolgozást futtasson (nem küld GPT-nek)")
    ] = False,
    list_cmd: Annotated[
        bool, typer.Option("--list", help="Listázza az újraértékelhető (nyílt válasz) megoldásokat")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", help="Maximum feldolgozandó megoldások száma")
    ] = 10,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Csak kiírja mit csinálna, nem ment")
    ] = False,
) -> None:
    """GPT-alapú újraértékelés: nyílt válaszok pontszámának felülvizsgálata.

    Alap: listázza az újraértékelhető megoldásokat (--list).
    --pending: feldolgozza a függőben lévő jutalmakat (nem kér GPT-t).
    --id N: egy megoldást értékel újra GPT-vel.
    --user / --feladat-id: tömeges újraértékelés (--limit darabot).
    """
    import json as _json

    from felvi_games.ai import check_answer
    from felvi_games.config import get_db_path
    from felvi_games.db import FeladatRecord, FeladatRepository, MegoldasRecord
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)

    # --pending: process deferred rewards only, no GPT calls
    if pending:
        if not user:
            typer.echo("[!] --pending használatához add meg a --user opciót.")
            raise typer.Exit(code=2)
        earned = repo.process_pending_ujraertekeles_jutalom(user, trigger_tipus="cli_reeval")
        if earned:
            typer.echo(f"✅ Jutalmak kiosztva ({user}): {', '.join(earned)}")
        else:
            typer.echo(f"  Nincs függő jutalom ({user}).")
        return

    # Query candidate rows
    with Session(repo._engine) as s:
        stmt = (
            select(
                MegoldasRecord.id,
                MegoldasRecord.felhasznalo_nev,
                MegoldasRecord.feladat_id,
                MegoldasRecord.adott_valasz,
                MegoldasRecord.pont,
                MegoldasRecord.helyes,
                MegoldasRecord.ujraertekelt,
                MegoldasRecord.created_at,
            )
            .where(MegoldasRecord.adott_valasz.is_not(None))
        )
        if megoldas_id is not None:
            stmt = stmt.where(MegoldasRecord.id == megoldas_id)
        else:
            if user:
                stmt = stmt.where(MegoldasRecord.felhasznalo_nev == user)
            if feladat_id:
                stmt = stmt.where(MegoldasRecord.feladat_id == feladat_id)
            # For bulk: prefer not-yet-reevaluated, open-answer tasks
            from felvi_games.db import FeladatRecord as FR
            open_ids = set(s.scalars(
                select(FR.id).where(FR.feladat_tipus == "nyilt_valasz")
            ).all())
            if not feladat_id:
                stmt = stmt.where(MegoldasRecord.feladat_id.in_(open_ids))
            stmt = stmt.order_by(MegoldasRecord.ujraertekelt.asc(), MegoldasRecord.created_at.desc())
            stmt = stmt.limit(limit)

        rows = s.execute(stmt).all()

    if not rows:
        typer.echo("  Nincs újraértékelhető megoldás a feltételek alapján.")
        return

    # --list mode
    if list_cmd or (megoldas_id is None and not user and not feladat_id):
        typer.echo(f"\n=== Újraértékelhető megoldások  (DB: {db_path})  összesen: {len(rows)} ===\n")
        for r in rows:
            flag = "✓" if r.ujraertekelt else " "
            eredmeny = "✅" if r.helyes else "❌"
            typer.echo(
                f"  [{flag}] id={r.id:5d}  {eredmeny} {r.pont}pt  "
                f"{r.feladat_id}  {r.felhasznalo_nev}  "
                f"  válasz: {str(r.adott_valasz or '')[:60]}"
            )
        typer.echo(f"\nTipp: felvi reeval --id <ID>   egy konkrét újraértékeléshez")
        typer.echo(      f"      felvi reeval --user <NÉV>  tömeges újraértékeléshez\n")
        return

    # Reevaluate rows with GPT
    total = len(rows)
    improved = skipped = errors = 0

    typer.echo(f"\n=== GPT újraértékelés  (DB: {db_path})  {'DRY-RUN  ' if dry_run else ''}{total} megoldás ===\n")

    with Session(repo._engine) as s:
        for r in rows:
            f = s.get(FeladatRecord, r.feladat_id)
            if not f:
                typer.echo(f"  [!] Feladat nem található: {r.feladat_id} — kihagyva")
                skipped += 1
                continue

            elfogadott = None
            if f.elfogadott_valaszok:
                try:
                    elfogadott = _json.loads(f.elfogadott_valaszok)
                except Exception:
                    pass

            try:
                ert = check_answer(
                    f.kerdes,
                    f.helyes_valasz,
                    r.adott_valasz or "",
                    f.magyarazat,
                    elfogadott_valaszok=elfogadott,
                    feladat_tipus=f.feladat_tipus,
                    max_pont=f.max_pont,
                    reszpontozas=f.reszpontozas,
                )
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"  [!] GPT hiba  id={r.id}: {exc}")
                errors += 1
                continue

            arrow = f"{r.pont} → {ert.pont}" if ert.pont != r.pont else f"{r.pont} (változatlan)"
            flag = "📈" if ert.pont > r.pont else ("📉" if ert.pont < r.pont else "➡️ ")
            typer.echo(
                f"  {flag} id={r.id:5d}  {r.feladat_id}  {r.felhasznalo_nev}  "
                f"pont: {arrow} / {f.max_pont}   {ert.visszajelzes[:60]}"
            )

            if not dry_run:
                rv = repo.reevaluate_megoldas(
                    r.id,
                    ertekeles=ert,
                    source="cli_reeval",
                    note="Tömeges CLI újraértékelés",
                )
                if rv["new_pont"] > rv["old_pont"]:
                    improved += 1
                    if rv["deferred_reward"]:
                        typer.echo(f"          ⭐ Jutalom függőben ({r.felhasznalo_nev})")

    if not dry_run:
        typer.echo(f"\n  Mentve: {total - skipped - errors} db, javult: {improved}, hiba: {errors}\n")
        # Auto-process pending rewards for targeted user
        if user:
            earned = repo.process_pending_ujraertekeles_jutalom(user, trigger_tipus="cli_reeval")
            if earned:
                typer.echo(f"  ✅ Jutalmak kiosztva: {', '.join(earned)}\n")
    else:
        typer.echo(f"\n  (Dry-run, semmi nem lett mentve.)\n")


# ---------------------------------------------------------------------------
# felvi user-stats
# ---------------------------------------------------------------------------

@app.command("user-stats")
def user_stats_cmd(
    user: Annotated[str, typer.Argument(help="Felhasználó neve (pl. 'Lackó')")],
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    simulate: Annotated[
        bool, typer.Option("--simulate", help="Éremszabályok szimulációja (nem ment semmit)")
    ] = False,
) -> None:
    """Egy felhasználó részletes statisztikája és éremszabály-kiértékelése."""
    from felvi_games.achievements import EREM_KATALOGUS, simulate_medal_rules
    from felvi_games.config import get_db_path
    from felvi_games.db import FeladatRepository, get_engine

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)
    stats = repo.get_user_stats(user)
    if stats is None:
        typer.echo(f"[!] Ismeretlen felhasználó: '{user}'")
        raise typer.Exit(code=1)

    typer.echo(f"\n{'='*60}")
    typer.echo(f"  Felhasználó: {stats.nev}  (id={stats.id})")
    typer.echo(f"  Regisztrált: {stats.created_at}")
    typer.echo(f"{'='*60}")

    typer.echo("\n--- Menetek ---")
    typer.echo(f"  Összes menet:       {stats.menetek_ossz}")
    typer.echo(f"  Befejezett:         {stats.menetek_befejezett}")
    typer.echo(f"  Megoldott feladat:  {stats.megoldott_ossz} / {stats.tervezett_ossz}")
    typer.echo(f"  Összpontszám:       {stats.pont_ossz}")
    typer.echo(f"  Első menet:         {stats.elso_menet}")
    typer.echo(f"  Utolsó menet:       {stats.utolso_menet}")

    typer.echo("\n--- Válaszok ---")
    typer.echo(f"  Összes válasz:      {stats.valaszok_ossz}")
    typer.echo(f"  Helyes:             {stats.helyes_ossz}  ({stats.accuracy_pct:.1f}%)")
    typer.echo(f"  Átlag idő:          {f'{stats.atlag_mp:.1f}s' if stats.atlag_mp else '-'}")
    typer.echo(f"  Leggyorsabb:        {f'{stats.min_mp:.1f}s' if stats.min_mp else '-'}")
    typer.echo(f"  Segítséget kért:    {stats.hint_ossz}")

    typer.echo("\n--- Tárgyak / Szintek ---")
    for targy, szint, n in stats.targy_szint:
        typer.echo(f"  {targy} / {szint}: {n} menet")

    typer.echo(f"\n--- Játéknapok ({len(stats.jateknapok)} különböző nap) ---")
    for nap, n in stats.jateknapok:
        typer.echo(f"  {nap}  ({n} menet)")

    typer.echo(f"\n--- Megszerzett érmek ({len(stats.eremek)}) ---")
    if not stats.eremek:
        typer.echo("  (még nincs)")
    for fe in stats.eremek:
        erem = EREM_KATALOGUS.get(fe.erem_id)
        nev = erem.nev if erem else fe.erem_id
        ikon = erem.ikon if erem else "🏅"
        szamlalo = f" ×{fe.szamlalo}" if fe.szamlalo > 1 else ""
        lejarat = f"  [lejár: {fe.lejarat}]" if fe.lejarat else ""
        typer.echo(f"  {ikon} {nev}{szamlalo}  ({fe.szerzett}){lejarat}")

    if simulate:
        engine = get_engine(db_path)
        earned_ids = {fe.erem_id for fe in stats.eremek}
        sim_results = simulate_medal_rules(user, engine, earned_ids)
        typer.echo(f"\n--- Éremszabály szimuláció ---")
        typer.echo(f"  {'Érem':<32} {'Teljesül':>8}  Megjegyzés")
        typer.echo("  " + "-" * 60)
        for r in sim_results:
            if r.error:
                typer.echo(f"  ❌ {r.nev:<32}    HIBA  {r.error}")
                continue
            if r.result:
                if r.already_earned and not r.ismetelheto:
                    mark, note = "✓", "már megvan"
                elif r.already_earned:
                    mark, note = "✓", "ismételné"
                else:
                    mark, note = "🏅", ">>> ÚJ ÉREM <<<"
            else:
                mark, note = "·", ""
            typer.echo(f"  {mark} {r.nev:<32} {str(r.result):>8}  {note}")
        typer.echo()

    typer.echo()


# ---------------------------------------------------------------------------
# felvi medal-clear  – összes kiosztott érem törlése
# ---------------------------------------------------------------------------

@app.command("medal-clear")
def medal_clear_cmd(
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    user: Annotated[
        Optional[str], typer.Option("--user", help="Csak egy felhasználó érmeinek törlése")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Megerősítés kérése nélkül töröl")
    ] = False,
) -> None:
    """Kiosztott érmek törlése a DB-ből.

    \b
    felvi medal-clear               # minden felhasználó érme
    felvi medal-clear --user Lóri   # csak Lóri érme
    felvi medal-clear --yes         # megerősítés nélkül
    """
    from felvi_games.config import get_db_path
    from felvi_games.db import get_engine
    from sqlalchemy import text

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    engine = get_engine(db_path)
    with engine.connect() as conn:
        if user:
            cnt = conn.execute(
                text("SELECT COUNT(*) FROM felhasznalo_eremek WHERE felhasznalo_nev = :u"),
                {"u": user},
            ).scalar() or 0
        else:
            cnt = conn.execute(text("SELECT COUNT(*) FROM felhasznalo_eremek")).scalar() or 0

    scope = f"'{user}'" if user else "minden felhasználó"
    typer.echo(f"\n{cnt} érem lesz törölve ({scope}).")

    if cnt == 0:
        typer.echo("Nincs mit törölni.")
        return

    if not yes:
        typer.confirm("Folytatod?", abort=True)

    with engine.begin() as conn:
        if user:
            conn.execute(
                text("DELETE FROM felhasznalo_eremek WHERE felhasznalo_nev = :u"),
                {"u": user},
            )
        else:
            conn.execute(text("DELETE FROM felhasznalo_eremek"))

    typer.echo(f"✅ {cnt} érem törölve.\n")


# ---------------------------------------------------------------------------
# felvi medal-recheck  – retroaktív éremkiértékelés minden felhasználóra
# ---------------------------------------------------------------------------

@app.command("medal-recheck")
def medal_recheck_cmd(
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    user: Annotated[
        Optional[str], typer.Option("--user", help="Csak egy felhasználó kiértékelése")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Csak listázza a várható érmeket, nem ment")
    ] = False,
) -> None:
    """Retroaktív éremkiértékelés – minden felhasználóra lefuttatja az összes szabályt.

    Hasznos akkor, ha új érmek kerültek a katalógusba, vagy ha valaki lezáratlan
    menettel rendelkezik, ahol a session-end éremcheck nem futott le.

    \b
    felvi medal-recheck               # minden felhasználó
    felvi medal-recheck --user Lóri   # csak Lóri
    felvi medal-recheck --dry-run     # csak kiírja, nem ment
    """
    from felvi_games.achievements import check_new_medals, simulate_medal_rules
    from felvi_games.config import get_db_path
    from felvi_games.db import FelhasznaloRecord, FeladatRepository, get_engine
    from sqlalchemy import select
    from sqlalchemy.orm import Session as _Session

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)
    engine = get_engine(db_path)

    with _Session(engine) as s:
        if user:
            users = [user]
        else:
            users = list(s.scalars(select(FelhasznaloRecord.nev).order_by(FelhasznaloRecord.nev)))

    typer.echo(f"\n=== Érem újrakiértékelés  (DB: {db_path})  dry_run={dry_run} ===\n")

    total_granted = 0
    for nev in users:
        typer.echo(f"👤 {nev}")
        if dry_run:
            earned_ids = {fe.erem_id for fe in repo.get_eremek(nev, include_expired=True)}
            results = simulate_medal_rules(nev, engine, earned_ids)
            new_pending = [r for r in results if r.result and not r.already_earned]
            would_repeat = [r for r in results if r.result and r.already_earned and r.ismetelheto]
            if new_pending:
                for r in new_pending:
                    typer.echo(f"  🏅 {r.ikon} {r.nev}  → ÚJ")
            if would_repeat:
                for r in would_repeat:
                    typer.echo(f"  🔁 {r.ikon} {r.nev}  → ismételné")
            if not new_pending and not would_repeat:
                typer.echo("  (nincs új érem)")
        else:
            before = {fe.erem_id for fe in repo.get_eremek(nev, include_expired=True)}
            newly = check_new_medals(nev, None, repo)
            if newly:
                for e in newly:
                    typer.echo(f"  ✅ {e.ikon} {e.nev}")
                total_granted += len(newly)
            else:
                typer.echo("  (nincs új érem)")
        typer.echo()

    if not dry_run:
        typer.echo(f"Összesen kiosztva: {total_granted} érem\n")


# ---------------------------------------------------------------------------
# felvi review  – AI review futtatása feladatokon
# ---------------------------------------------------------------------------

@app.command("review")
def review_cmd(
    feladat_id: Annotated[
        Optional[str], typer.Argument(help="Feladat ID, amire review-t futtatunk")
    ] = None,
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    wrong: Annotated[
        bool, typer.Option("--wrong", help="A legtöbbet rontott feladatokat veszi alapul")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", help="Max. feldolgozandó feladatok száma (--wrong esetén)")
    ] = 5,
    megjegyzes: Annotated[
        Optional[str], typer.Option("--megjegyzes", help="Kézi megjegyzés az AI-nak")
    ] = None,
    model: Annotated[
        Optional[str], typer.Option("--model", help="LLM modell neve (alap: LLM_MODEL env)")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Futtatja az AI review-t, de nem ment DB-be")
    ] = False,
) -> None:
    """AI review futtatása egy vagy több feladaton.

    Háromféleképpen hívható:

    \b
    felvi review M8_2023_1_3          # egy konkrét feladat
    felvi review --wrong --limit 3    # top-3 legtöbbet rontott
    felvi review --wrong              # top-5 (alap)
    """
    from felvi_games.config import get_db_path
    from felvi_games.db import FeladatRepository
    from felvi_games.review import run_feladat_review

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)

    # ---- Determine feladat list ----
    feladatok_to_review: list = []

    if feladat_id:
        f = repo.get(feladat_id)
        if f is None:
            typer.echo(f"[!] Feladat nem található: {feladat_id}")
            raise typer.Exit(code=1)
        feladatok_to_review = [f]
    elif wrong:
        rows = repo.get_wrong_feladatok(limit=limit, min_hibas=1)
        if not rows:
            typer.echo("  Nincs hibásan megoldott feladat.")
            return
        ids = [r.feladat_id for r in rows]
        feladatok_to_review = [f for fid in ids if (f := repo.get(fid)) is not None]
    else:
        typer.echo("[!] Adj meg egy feladat ID-t, vagy használd a --wrong flaget.")
        raise typer.Exit(code=1)

    typer.echo(f"\n=== AI Review  ({len(feladatok_to_review)} feladat)  dry_run={dry_run} ===\n")

    for feladat in feladatok_to_review:
        typer.echo(f"  ▶ {feladat.id}  [{feladat.targy}/{feladat.szint}]")
        kerdes_short = (feladat.kerdes[:80] + "…") if len(feladat.kerdes) > 80 else feladat.kerdes
        typer.echo(f"    Kérdés:  {kerdes_short}")
        typer.echo(f"    Helyes:  {feladat.helyes_valasz}")

        typer.echo("    AI review fut…", nl=False)
        try:
            result = run_feladat_review(
                feladat, repo,
                megjegyzes=megjegyzes, model=model, dry_run=dry_run,
            )
        except Exception as exc:
            typer.echo(f" HIBA: {exc}")
            continue
        typer.echo(" kész.")

        for field in result.changed_fields:
            old_s = str(getattr(feladat, field))[:60]
            new_s = str(getattr(result.updated, field))[:60]
            typer.echo(f"    ~ {field}:")
            typer.echo(f"        előtte: {old_s}")
            typer.echo(f"        utána:  {new_s}")

        if result.updated.review_megjegyzes:
            typer.echo(f"    AI megjegyzés: {result.updated.review_megjegyzes}")

        if not result.changed_fields:
            typer.echo("    → Tartalom nem változott.")
        else:
            typer.echo(f"    → Változott mezők: {', '.join(result.changed_fields)}")

        if dry_run:
            typer.echo("    [dry-run] Nem mentve.")
        elif result.versioned:
            typer.echo(f"    ✓ Új verzió: {result.original_id}  →  {result.updated.id}  (archivált: {result.original_id})")
        else:
            typer.echo(f"    ✓ In-place frissítve: {result.updated.id}")

        typer.echo()

    typer.echo("Kész.\n")


# ---------------------------------------------------------------------------
# felvi report  – heti használati riport (markdown + matplotlib PNG-k)
# ---------------------------------------------------------------------------

@app.command("report")
def report_cmd(
    days: Annotated[
        int, typer.Option("--days", help="Hány napra visszamenőleg (alap: 7)")
    ] = 7,
    output_dir: Annotated[
        Optional[Path], typer.Option("--output-dir", help="Kimeneti mappa (alap: ./reports/<dátum>_<napok>d/)")
    ] = None,
    user: Annotated[
        Optional[str], typer.Option("--user", help="Csak egy felhasználó adatai")
    ] = None,
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
    open_report: Annotated[
        bool, typer.Option("--open", help="Megnyitja a kimeneti mappát fájlkezelőben")
    ] = False,
) -> None:
    """Heti (vagy tetszőleges időtartamú) használati riport generálása.

    Kimenet: markdown összefoglaló + matplotlib PNG grafikonok egy mappában.

    \b
    felvi report                          # utolsó 7 nap, minden felhasználó
    felvi report --days 14                # utolsó 14 nap
    felvi report --user Lóri              # csak Lóri adatai
    felvi report --output-dir my_reports  # egyedi kimeneti mappa
    """
    from felvi_games.config import get_db_path
    from felvi_games.report import run as _run_report

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)
    if days < 1:
        typer.echo("[!] A --days értéke legalább 1 legyen.")
        raise typer.Exit(code=2)

    typer.echo(f"\nRiport generálása  (DB: {db_path}  |  {days} nap  |  user={user or 'mind'})\n")

    out_dir = _run_report(
        db_path=db_path,
        days=days,
        output_dir=output_dir,
        user_filter=user,
    )

    files = sorted(out_dir.iterdir())
    typer.echo(f"✅ Riport kész: {out_dir}")
    for f in files:
        typer.echo(f"   {f.name}")

    if open_report:
        import subprocess, sys
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(out_dir)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(out_dir)])
        else:
            subprocess.Popen(["xdg-open", str(out_dir)])

    typer.echo()


# ---------------------------------------------------------------------------
# felvi tts-clear  – TTS szöveg törlése (újrageneráláshoz)
# ---------------------------------------------------------------------------

@app.command("tts-clear")
def tts_clear_cmd(
    feladat_id: Annotated[
        Optional[str], typer.Argument(help="Csak ezt a feladatot érinti")
    ] = None,
    targy: Annotated[
        Optional[Targy], typer.Option("--targy", help="Csak ezt a tárgyat érinti (matek/magyar)")
    ] = None,
    db: Annotated[
        Optional[Path], typer.Option("--db", help="SQLite DB útvonala (alap: FELVI_DB env)")
    ] = None,
) -> None:
    """TTS kérdésszöveg (tts_kerdes_szoveg) törlése DB-ből.

    A törölt rekordok a következő lejátszáskor az aktuális kontextussal
    (csoport-szöveg + kérdés) újragenerálódnak.

    \b
    felvi tts-clear                  # minden feladat
    felvi tts-clear --targy matek    # csak matek
    felvi tts-clear M8_2023_1_3a    # egy feladat
    """
    from felvi_games.config import get_db_path
    from felvi_games.db import FeladatRepository

    db_path = db or get_db_path()
    if not db_path.exists():
        typer.echo(f"[!] DB nem található: {db_path}")
        raise typer.Exit(code=1)

    repo = FeladatRepository(db_path)
    count = repo.clear_tts_szoveg(
        feladat_id=feladat_id,
        targy=targy.value if targy else None,
    )
    scope = feladat_id or (f"targy={targy.value}" if targy else "összes")
    typer.echo(f"✓ {count} feladat tts_kerdes_szoveg törölve ({scope}).")


# ---------------------------------------------------------------------------
# Entry point (pyproject.toml → project.scripts)
# ---------------------------------------------------------------------------

def run() -> None:
    app()
