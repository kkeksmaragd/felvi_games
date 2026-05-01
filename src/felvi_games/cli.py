"""
cli.py
------
Parancssori felületek a felvi_games eszközökhöz (typer).

Belépési pont:
  felvi          →  app()
    felvi info     – Konfiguráció, PDF-ek és DB állapot kiírása
    felvi scrape   – PDF-ek letöltése
    felvi parse    – PDF-ek feldolgozása DB-be
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer

from felvi_games.config import setup_logging

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
) -> None:
    """Érmek / achievements: katalógus és felhasználói haladás."""
    from felvi_games.achievements import EREM_KATALOGUS, get_all_medals_for_user
    from felvi_games.config import get_db_path
    from felvi_games.db import FelhasznaloRecord, FeladatRepository, get_engine
    from sqlalchemy import select
    from sqlalchemy.orm import Session

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
    typer.echo(f"\n=== Hibásan megoldott feladatok  (DB: {db_path}){scope} ===\n")

    if not rows:
        typer.echo("  Nincs találat (még senki sem rontott el egy feladatot sem ebben a körben).")
        typer.echo()
        return

    for r in rows:
        ev_label = str(r.ev) if r.ev else "?"
        tipus = r.feladat_tipus or "-"
        kerdes_short = (r.kerdes[:90] + "…") if len(r.kerdes) > 90 else r.kerdes
        helyes_short = (r.helyes_valasz[:50] + "…") if len(r.helyes_valasz) > 50 else r.helyes_valasz

        typer.echo(
            f"  [{r.targy}/{r.szint}/{ev_label}] {tipus}  "
            f"hibás: {r.hibas_db}/{r.osszes_db}  ({r.rontas_pct:.0f}% rontás)"
        )
        typer.echo(f"    Kérdés:        {kerdes_short}")
        typer.echo(f"    Helyes válasz: {helyes_short}")
        typer.echo(f"    ID:            {r.feladat_id}")

        if detail and r.hibas_valaszok:
            cnt = Counter(r.hibas_valaszok)
            parts = [f'"{v}"×{c}' if c > 1 else f'"{v}"' for v, c in cnt.most_common()]
            typer.echo(f"    Hibás válaszok: {', '.join(parts)}")

        typer.echo()

    typer.echo(f"  Összesen: {len(rows)} feladat listázva.\n")


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
# Entry point (pyproject.toml → project.scripts)
# ---------------------------------------------------------------------------

def run() -> None:
    app()
