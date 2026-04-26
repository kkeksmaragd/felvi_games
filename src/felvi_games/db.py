"""Persistence layer – SQLAlchemy 2.x + SQLite."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    or_,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
)

from felvi_games.config import (
    get_assets_dir,
    get_db_path,
    relative_asset_path,
    resolve_asset,
)
from felvi_games.models import (
    Erem,
    Ertekeles,
    Feladat,
    FeladatCsoport,
    FelhasznaloErem,
    Menet,
    _list_to_json,
)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def get_engine(db_path: Path | None = None):
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", echo=False)


# ---------------------------------------------------------------------------
# ORM base & tables
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class FelhasznaloRecord(Base):
    """A registered player."""

    __tablename__ = "felhasznalok"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nev: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    extra_info: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON dict of optional profile fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    menetek: Mapped[list["MenetRecord"]] = relationship(
        back_populates="felhasznalo", cascade="all, delete-orphan",
        foreign_keys="MenetRecord.felhasznalo_id",
    )
    eremek_gyujtemeny: Mapped[list["FelhasznaloEremRecord"]] = relationship(
        back_populates="felhasznalo", cascade="all, delete-orphan",
        foreign_keys="FelhasznaloEremRecord.felhasznalo_id",
    )


class MenetRecord(Base):
    """A single playing session."""

    __tablename__ = "menetek"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    felhasznalo_id: Mapped[int | None] = mapped_column(
        ForeignKey("felhasznalok.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Denormalised name kept for backward-compatible queries in achievements / progress_check.
    # Will be removed in a future migration once all query sites use felhasznalo_id.
    felhasznalo_nev: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    targy: Mapped[str] = mapped_column(String(16), nullable=False)
    szint: Mapped[str] = mapped_column(String(32), nullable=False)
    feladat_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    megoldott: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pont: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    felhasznalo: Mapped["FelhasznaloRecord"] = relationship(
        back_populates="menetek", foreign_keys=[felhasznalo_id]
    )
    megoldasok: Mapped[list["MegoldasRecord"]] = relationship(back_populates="menet")

    def to_domain(self) -> Menet:
        return Menet(
            id=self.id,
            felhasznalo=self.felhasznalo_nev,
            targy=self.targy,
            szint=self.szint,
            feladat_limit=self.feladat_limit,
            megoldott=self.megoldott,
            pont=self.pont,
            started_at=self.started_at,
            ended_at=self.ended_at,
        )


class FeladatCsoportRecord(Base):
    """Összetartozó részfeladatok csoportja (pl. 3a, 3b, 3c)."""

    __tablename__ = "feladat_csoportok"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    targy: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    szint: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    feladat_sorszam: Mapped[str] = mapped_column(String(16), nullable=False)
    ev: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    valtozat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kontextus: Mapped[str | None] = mapped_column(Text, nullable=True)
    abra_van: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    feladat_oldal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fl_pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ut_pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fl_szoveg_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ut_szoveg_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sorrend_kotelezo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_pont_ossz: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_domain(self) -> FeladatCsoport:
        return FeladatCsoport.from_record(self)


class FeladatRecord(Base):
    """Persisted feladat with compiled assets."""

    __tablename__ = "feladatok"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    targy: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    neh: Mapped[int] = mapped_column(Integer, nullable=False)
    szint: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kerdes: Mapped[str] = mapped_column(Text, nullable=False)
    helyes_valasz: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[str] = mapped_column(Text, nullable=False)
    magyarazat: Mapped[str] = mapped_column(Text, nullable=False)

    # Source tracking
    ev: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    valtozat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feladat_sorszam: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Group membership (no FK constraint – SQLite compatible)
    csoport_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    csoport_sorrend: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Task type & scoring
    feladat_tipus: Mapped[str | None] = mapped_column(String(32), nullable=True)
    elfogadott_valaszok: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    valaszlehetosegek: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON list
    max_pont: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reszpontozas: Mapped[str | None] = mapped_column(Text, nullable=True)
    ertekeles_megjegyzes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Compiled TTS assets – relative paths to MP3 files under assets_dir
    tts_kerdes_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tts_magyarazat_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Extraction context
    kontextus: Mapped[str | None] = mapped_column(Text, nullable=True)
    abra_van: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    feladat_oldal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fl_szoveg_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ut_szoveg_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fl_pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ut_pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    review_elvegezve: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_megjegyzes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationship to user attempts
    megoldasok: Mapped[list["MegoldasRecord"]] = relationship(
        back_populates="feladat", cascade="all, delete-orphan"
    )

    def to_domain(self) -> Feladat:
        return Feladat.from_record(self)


class MegoldasRecord(Base):
    """A single user attempt at a Feladat."""

    __tablename__ = "megoldasok"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feladat_id: Mapped[str] = mapped_column(
        ForeignKey("feladatok.id", ondelete="CASCADE"), index=True
    )
    menet_id: Mapped[int | None] = mapped_column(
        ForeignKey("menetek.id"), nullable=True, index=True
    )
    felhasznalo_nev: Mapped[str] = mapped_column(String(64), nullable=False, default="")  # kept for legacy queries
    felhasznalo_id: Mapped[int | None] = mapped_column(
        ForeignKey("felhasznalok.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    adott_valasz: Mapped[str] = mapped_column(Text, nullable=False)
    helyes: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pont: Mapped[int] = mapped_column(Integer, nullable=False)
    visszajelzes: Mapped[str] = mapped_column(Text, nullable=False)
    elapsed_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    segitseg_kert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hibajelezes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    feladat: Mapped["FeladatRecord"] = relationship(back_populates="megoldasok")
    menet: Mapped["MenetRecord | None"] = relationship(back_populates="megoldasok")


class EremRecord(Base):
    """Medal/achievement catalog entry – one row per possible medal."""

    __tablename__ = "eremek"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nev: Mapped[str] = mapped_column(String(128), nullable=False)
    leiras: Mapped[str] = mapped_column(Text, nullable=False)
    ikon: Mapped[str] = mapped_column(String(16), nullable=False)
    kategoria: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ideiglenes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ervenyes_napig: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ismetelheto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kep_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    hang_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    gif_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    privat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    cel_felhasznalo: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_domain(self) -> Erem:
        return Erem(
            id=self.id,
            nev=self.nev,
            leiras=self.leiras,
            ikon=self.ikon,
            kategoria=self.kategoria,
            ideiglenes=self.ideiglenes,
            ervenyes_napig=self.ervenyes_napig,
            ismetelheto=self.ismetelheto,
            kep_url=self.kep_url,
            hang_url=self.hang_url,
            gif_url=self.gif_url,
            privat=self.privat,
            cel_felhasznalo=self.cel_felhasznalo,
        )


class InterakcioRecord(Base):
    """Fine-grained player behaviour event log."""

    __tablename__ = "interakciok"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    felhasznalo_nev: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # kept for legacy queries
    felhasznalo_id: Mapped[int | None] = mapped_column(
        ForeignKey("felhasznalok.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tipus: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    targy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    szint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    feladat_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    menet_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON freeform
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class FelhasznaloEremRecord(Base):
    """Earned medal/achievement row per user."""

    __tablename__ = "felhasznalo_eremek"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    felhasznalo_nev: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # kept for legacy queries
    felhasznalo_id: Mapped[int | None] = mapped_column(
        ForeignKey("felhasznalok.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    erem_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    szerzett_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    lejarat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    szamlalo: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    felhasznalo: Mapped["FelhasznaloRecord | None"] = relationship(
        back_populates="eremek_gyujtemeny", foreign_keys=[felhasznalo_id]
    )

    def to_domain(self) -> FelhasznaloErem:
        return FelhasznaloErem(
            id=self.id,
            felhasznalo=self.felhasznalo_nev,
            erem_id=self.erem_id,
            szerzett=self.szerzett_at,
            lejarat=self.lejarat_at,
            szamlalo=self.szamlalo,
        )


def init_db(db_path: Path | None = None) -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(get_engine(db_path))


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class FeladatRepository:
    """CRUD + asset operations for Feladat persistence."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._engine = get_engine(db_path)
        init_db(db_path)
        self.seed_erem_katalogus()

    # --- Feladat CRUD ---

    def upsert(self, feladat: Feladat) -> None:
        """Insert or update a Feladat (domain model → DB record)."""
        with Session(self._engine) as session:
            existing = session.get(FeladatRecord, feladat.id)
            if existing:
                existing.targy = feladat.targy
                existing.neh = feladat.neh
                existing.szint = feladat.szint
                existing.kerdes = feladat.kerdes
                existing.helyes_valasz = feladat.helyes_valasz
                existing.hint = feladat.hint
                existing.magyarazat = feladat.magyarazat
                existing.ev = feladat.ev
                existing.valtozat = feladat.valtozat
                existing.feladat_sorszam = feladat.feladat_sorszam
                existing.csoport_id = feladat.csoport_id
                existing.csoport_sorrend = feladat.csoport_sorrend
                existing.feladat_tipus = feladat.feladat_tipus
                existing.elfogadott_valaszok = _list_to_json(feladat.elfogadott_valaszok)
                existing.valaszlehetosegek = _list_to_json(feladat.valaszlehetosegek)
                existing.max_pont = feladat.max_pont
                existing.reszpontozas = feladat.reszpontozas
                existing.ertekeles_megjegyzes = feladat.ertekeles_megjegyzes
                if feladat.tts_kerdes_path is not None:
                    existing.tts_kerdes_path = feladat.tts_kerdes_path
                if feladat.tts_magyarazat_path is not None:
                    existing.tts_magyarazat_path = feladat.tts_magyarazat_path
                existing.kontextus = feladat.kontextus
                existing.abra_van = feladat.abra_van
                existing.feladat_oldal = feladat.feladat_oldal
                if feladat.fl_szoveg_path is not None:
                    existing.fl_szoveg_path = feladat.fl_szoveg_path
                if feladat.ut_szoveg_path is not None:
                    existing.ut_szoveg_path = feladat.ut_szoveg_path
                if feladat.fl_pdf_path is not None:
                    existing.fl_pdf_path = feladat.fl_pdf_path
                if feladat.ut_pdf_path is not None:
                    existing.ut_pdf_path = feladat.ut_pdf_path
                existing.review_elvegezve = feladat.review_elvegezve
                if feladat.review_megjegyzes is not None:
                    existing.review_megjegyzes = feladat.review_megjegyzes
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(FeladatRecord(
                    id=feladat.id,
                    targy=feladat.targy,
                    neh=feladat.neh,
                    szint=feladat.szint,
                    kerdes=feladat.kerdes,
                    helyes_valasz=feladat.helyes_valasz,
                    hint=feladat.hint,
                    magyarazat=feladat.magyarazat,
                    ev=feladat.ev,
                    valtozat=feladat.valtozat,
                    feladat_sorszam=feladat.feladat_sorszam,
                    csoport_id=feladat.csoport_id,
                    csoport_sorrend=feladat.csoport_sorrend,
                    feladat_tipus=feladat.feladat_tipus,
                    elfogadott_valaszok=_list_to_json(feladat.elfogadott_valaszok),
                    valaszlehetosegek=_list_to_json(feladat.valaszlehetosegek),
                    max_pont=feladat.max_pont,
                    reszpontozas=feladat.reszpontozas,
                    ertekeles_megjegyzes=feladat.ertekeles_megjegyzes,
                    tts_kerdes_path=feladat.tts_kerdes_path,
                    tts_magyarazat_path=feladat.tts_magyarazat_path,
                    kontextus=feladat.kontextus,
                    abra_van=feladat.abra_van,
                    feladat_oldal=feladat.feladat_oldal,
                    fl_szoveg_path=feladat.fl_szoveg_path,
                    ut_szoveg_path=feladat.ut_szoveg_path,
                    fl_pdf_path=feladat.fl_pdf_path,
                    ut_pdf_path=feladat.ut_pdf_path,
                    review_elvegezve=feladat.review_elvegezve,
                    review_megjegyzes=feladat.review_megjegyzes,
                ))
            session.commit()

    def upsert_many(self, feladatok: list[Feladat]) -> None:
        """Bulk upsert – more efficient than calling upsert() in a loop."""
        with Session(self._engine) as session:
            existing_ids = {
                row[0]
                for row in session.execute(
                    select(FeladatRecord.id).where(
                        FeladatRecord.id.in_([f.id for f in feladatok])
                    )
                )
            }
            now = datetime.now(timezone.utc)
            for f in feladatok:
                if f.id in existing_ids:
                    session.merge(FeladatRecord(
                        id=f.id, targy=f.targy, neh=f.neh, szint=f.szint,
                        kerdes=f.kerdes, helyes_valasz=f.helyes_valasz,
                        hint=f.hint, magyarazat=f.magyarazat,
                        ev=f.ev,
                        valtozat=f.valtozat,
                        feladat_sorszam=f.feladat_sorszam,
                        csoport_id=f.csoport_id,
                        csoport_sorrend=f.csoport_sorrend,
                        feladat_tipus=f.feladat_tipus,
                        elfogadott_valaszok=_list_to_json(f.elfogadott_valaszok),
                        valaszlehetosegek=_list_to_json(f.valaszlehetosegek),
                        max_pont=f.max_pont,
                        reszpontozas=f.reszpontozas,
                        ertekeles_megjegyzes=f.ertekeles_megjegyzes,
                        tts_kerdes_path=f.tts_kerdes_path,
                        tts_magyarazat_path=f.tts_magyarazat_path,
                        kontextus=f.kontextus,
                        abra_van=f.abra_van,
                        feladat_oldal=f.feladat_oldal,
                        fl_szoveg_path=f.fl_szoveg_path,
                        ut_szoveg_path=f.ut_szoveg_path,
                        fl_pdf_path=f.fl_pdf_path,
                        ut_pdf_path=f.ut_pdf_path,
                        review_elvegezve=f.review_elvegezve,
                        review_megjegyzes=f.review_megjegyzes,
                        updated_at=now,
                    ))
                else:
                    session.add(FeladatRecord(
                        id=f.id, targy=f.targy, neh=f.neh, szint=f.szint,
                        kerdes=f.kerdes, helyes_valasz=f.helyes_valasz,
                        hint=f.hint, magyarazat=f.magyarazat,
                        ev=f.ev,
                        valtozat=f.valtozat,
                        feladat_sorszam=f.feladat_sorszam,
                        csoport_id=f.csoport_id,
                        csoport_sorrend=f.csoport_sorrend,
                        feladat_tipus=f.feladat_tipus,
                        elfogadott_valaszok=_list_to_json(f.elfogadott_valaszok),
                        valaszlehetosegek=_list_to_json(f.valaszlehetosegek),
                        max_pont=f.max_pont,
                        reszpontozas=f.reszpontozas,
                        ertekeles_megjegyzes=f.ertekeles_megjegyzes,
                        tts_kerdes_path=f.tts_kerdes_path,
                        tts_magyarazat_path=f.tts_magyarazat_path,
                        kontextus=f.kontextus,
                        abra_van=f.abra_van,
                        feladat_oldal=f.feladat_oldal,
                        fl_szoveg_path=f.fl_szoveg_path,
                        ut_szoveg_path=f.ut_szoveg_path,
                        fl_pdf_path=f.fl_pdf_path,
                        ut_pdf_path=f.ut_pdf_path,
                        review_elvegezve=f.review_elvegezve,
                        review_megjegyzes=f.review_megjegyzes,
                    ))
            session.commit()

    def get(self, feladat_id: str) -> Feladat | None:
        with Session(self._engine) as session:
            record = session.get(FeladatRecord, feladat_id)
            return record.to_domain() if record else None

    def all(self, targy: str | None = None, szint: str | None = None) -> list[Feladat]:
        with Session(self._engine) as session:
            stmt = select(FeladatRecord)
            if targy:
                stmt = stmt.where(FeladatRecord.targy == targy)
            if szint:
                stmt = stmt.where(FeladatRecord.szint == szint)
            return [r.to_domain() for r in session.scalars(stmt)]

    def count(self) -> int:
        with Session(self._engine) as session:
            return session.query(FeladatRecord).count()

    # --- FeladatCsoport CRUD ---

    def upsert_csoport(self, csoport: FeladatCsoport) -> None:
        """Insert or update a FeladatCsoport record."""
        with Session(self._engine) as session:
            existing = session.get(FeladatCsoportRecord, csoport.id)
            if existing:
                existing.targy = csoport.targy
                existing.szint = csoport.szint
                existing.feladat_sorszam = csoport.feladat_sorszam
                existing.ev = csoport.ev
                existing.valtozat = csoport.valtozat
                existing.kontextus = csoport.kontextus
                existing.abra_van = csoport.abra_van
                existing.feladat_oldal = csoport.feladat_oldal
                existing.fl_pdf_path = csoport.fl_pdf_path
                existing.ut_pdf_path = csoport.ut_pdf_path
                existing.fl_szoveg_path = csoport.fl_szoveg_path
                existing.ut_szoveg_path = csoport.ut_szoveg_path
                existing.sorrend_kotelezo = csoport.sorrend_kotelezo
                existing.max_pont_ossz = csoport.max_pont_ossz
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(FeladatCsoportRecord(
                    id=csoport.id,
                    targy=csoport.targy,
                    szint=csoport.szint,
                    feladat_sorszam=csoport.feladat_sorszam,
                    ev=csoport.ev,
                    valtozat=csoport.valtozat,
                    kontextus=csoport.kontextus,
                    abra_van=csoport.abra_van,
                    feladat_oldal=csoport.feladat_oldal,
                    fl_pdf_path=csoport.fl_pdf_path,
                    ut_pdf_path=csoport.ut_pdf_path,
                    fl_szoveg_path=csoport.fl_szoveg_path,
                    ut_szoveg_path=csoport.ut_szoveg_path,
                    sorrend_kotelezo=csoport.sorrend_kotelezo,
                    max_pont_ossz=csoport.max_pont_ossz,
                ))
            session.commit()

    def upsert_many_csoportok(self, csoportok: list[FeladatCsoport]) -> None:
        """Bulk upsert for FeladatCsoport records."""
        for c in csoportok:
            self.upsert_csoport(c)

    def get_csoport(self, csoport_id: str) -> FeladatCsoport | None:
        with Session(self._engine) as session:
            record = session.get(FeladatCsoportRecord, csoport_id)
            return record.to_domain() if record else None

    def get_feladatok_by_csoport(self, csoport_id: str) -> list[Feladat]:
        """Return all Feladatok belonging to a group, ordered by csoport_sorrend."""
        with Session(self._engine) as session:
            stmt = (
                select(FeladatRecord)
                .where(FeladatRecord.csoport_id == csoport_id)
                .order_by(FeladatRecord.csoport_sorrend)
            )
            return [r.to_domain() for r in session.scalars(stmt)]

    # --- Asset operations ---

    def save_tts_assets(
        self,
        feladat: Feladat,
        tts_kerdes: bytes | None = None,
        tts_magyarazat: bytes | None = None,
    ) -> Feladat:
        """
        Write TTS bytes to files and persist the relative paths in the DB.
        Returns an updated Feladat with the new path fields set.
        """
        with Session(self._engine) as session:
            record = session.get(FeladatRecord, feladat.id)
            if record is None:
                raise KeyError(f"Feladat not found: {feladat.id}")

            new_kerdes_path: str | None = None
            new_magyarazat_path: str | None = None

            if tts_kerdes is not None:
                rel = relative_asset_path(feladat.id, "kerdes", feladat.szint, feladat.ev, feladat.valtozat)
                abs_path = resolve_asset(rel)
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_bytes(tts_kerdes)
                record.tts_kerdes_path = rel
                new_kerdes_path = rel

            if tts_magyarazat is not None:
                rel = relative_asset_path(feladat.id, "magyarazat", feladat.szint, feladat.ev, feladat.valtozat)
                abs_path = resolve_asset(rel)
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_bytes(tts_magyarazat)
                record.tts_magyarazat_path = rel
                new_magyarazat_path = rel

            record.updated_at = datetime.now(timezone.utc)
            session.commit()

        return feladat.with_assets(
            tts_kerdes_path=new_kerdes_path,
            tts_magyarazat_path=new_magyarazat_path,
        )

    def load_tts_bytes(self, relative_path: str) -> bytes:
        """Read TTS MP3 bytes from the asset file."""
        return resolve_asset(relative_path).read_bytes()

    def missing_tts(self, targy: str | None = None) -> Sequence[Feladat]:
        """Return feladatok that have no pre-rendered TTS audio yet."""
        with Session(self._engine) as session:
            stmt = select(FeladatRecord).where(FeladatRecord.tts_kerdes_path.is_(None))
            if targy:
                stmt = stmt.where(FeladatRecord.targy == targy)
            return [r.to_domain() for r in session.scalars(stmt)]

    # --- Megoldas (attempt) tracking ---

    def save_megoldas(
        self,
        feladat: Feladat,
        adott_valasz: str,
        ertekeles: Ertekeles,
        *,
        felhasznalo_nev: str = "",
        menet_id: int | None = None,
        elapsed_sec: float | None = None,
        segitseg_kert: bool = False,
        hibajelezes: bool = False,
    ) -> None:
        user_id = self._get_felhasznalo_id(felhasznalo_nev) if felhasznalo_nev else None
        with Session(self._engine) as session:
            session.add(MegoldasRecord(
                feladat_id=feladat.id,
                menet_id=menet_id,
                felhasznalo_nev=felhasznalo_nev,
                felhasznalo_id=user_id,
                adott_valasz=adott_valasz,
                helyes=ertekeles.helyes,
                pont=ertekeles.pont,
                visszajelzes=ertekeles.visszajelzes,
                elapsed_sec=elapsed_sec,
                segitseg_kert=segitseg_kert,
                hibajelezes=hibajelezes,
            ))
            session.commit()

    def save_review(self, feladat: Feladat, megjegyzes: str | None = None) -> Feladat:
        """Mark a feladat as reviewed, clear all pending hibajelezes flags, return updated domain."""
        with Session(self._engine) as session:
            record = session.get(FeladatRecord, feladat.id)
            if record is None:
                raise KeyError(f"Feladat not found: {feladat.id}")
            record.review_elvegezve = True
            record.review_megjegyzes = megjegyzes
            record.updated_at = datetime.now(timezone.utc)
            # Clear pending error flags on all attempts for this feladat
            session.execute(
                __import__("sqlalchemy").update(MegoldasRecord)
                .where(MegoldasRecord.feladat_id == feladat.id)
                .values(hibajelezes=False)
            )
            session.commit()
            return record.to_domain()

    def stats(self) -> dict:
        """Return aggregate statistics across all attempts."""
        with Session(self._engine) as session:
            total = session.query(MegoldasRecord).count()
            helyes = session.query(MegoldasRecord).filter_by(helyes=True).count()
            return {
                "total_attempts": total,
                "correct": helyes,
                "accuracy": round(helyes / total * 100, 1) if total else 0.0,
            }

    # --- Felhasznalo & Menet ---

    def _get_felhasznalo_id(self, nev: str) -> int | None:
        """Resolve a user name to its integer id.  Returns None if not found."""
        with Session(self._engine) as session:
            return session.scalar(
                select(FelhasznaloRecord.id).where(FelhasznaloRecord.nev == nev)
            )

    def get_or_create_felhasznalo(self, nev: str) -> int:
        """Ensure a player record exists and return the user id."""
        with Session(self._engine) as session:
            rec = session.scalar(
                select(FelhasznaloRecord).where(FelhasznaloRecord.nev == nev)
            )
            if rec is None:
                rec = FelhasznaloRecord(nev=nev)
                session.add(rec)
                session.commit()
                session.refresh(rec)
            return rec.id

    def start_menet(
        self,
        felhasznalo_nev: str,
        targy: str,
        szint: str,
        feladat_limit: int,
    ) -> int:
        """Create a new playing session and return its id."""
        user_id = self._get_felhasznalo_id(felhasznalo_nev)
        with Session(self._engine) as session:
            record = MenetRecord(
                felhasznalo_id=user_id,
                felhasznalo_nev=felhasznalo_nev,
                targy=targy,
                szint=szint,
                feladat_limit=feladat_limit,
            )
            session.add(record)
            session.commit()
            return record.id

    def end_menet(self, menet_id: int) -> None:
        """Mark a session as ended."""
        with Session(self._engine) as session:
            record = session.get(MenetRecord, menet_id)
            if record and record.ended_at is None:
                record.ended_at = datetime.now(timezone.utc)
                session.commit()

    def update_menet_progress(self, menet_id: int, megoldott: int, pont: int) -> None:
        """Persist in-progress counters (task count + score) to the session record."""
        with Session(self._engine) as session:
            record = session.get(MenetRecord, menet_id)
            if record:
                record.megoldott = megoldott
                record.pont = pont
                session.commit()

    def get_menetek(self, felhasznalo_nev: str, limit: int = 10) -> list[Menet]:
        """Return recent sessions for a user, newest first."""
        with Session(self._engine) as session:
            stmt = (
                select(MenetRecord)
                .where(MenetRecord.felhasznalo_nev == felhasznalo_nev)
                .order_by(MenetRecord.started_at.desc())
                .limit(limit)
            )
            return [r.to_domain() for r in session.scalars(stmt)]

    # --- Interaction log ---

    def log_interakcio(
        self,
        felhasznalo_nev: str,
        tipus: str,
        *,
        targy: str | None = None,
        szint: str | None = None,
        feladat_id: str | None = None,
        menet_id: int | None = None,
        meta: dict | None = None,
    ) -> None:
        """Append one raw behaviour event to the interaction log."""
        import json as _json
        user_id = self._get_felhasznalo_id(felhasznalo_nev)
        with Session(self._engine) as session:
            session.add(InterakcioRecord(
                felhasznalo_nev=felhasznalo_nev,
                felhasznalo_id=user_id,
                tipus=tipus,
                targy=targy,
                szint=szint,
                feladat_id=feladat_id,
                menet_id=menet_id,
                meta=_json.dumps(meta, ensure_ascii=False) if meta else None,
            ))
            session.commit()

    def get_interakciok(
        self,
        felhasznalo_nev: str,
        tipus: str | None = None,
        limit: int = 200,
    ) -> list[InterakcioRecord]:
        """Fetch recent interaction events for a user (newest first)."""
        with Session(self._engine) as session:
            stmt = (
                select(InterakcioRecord)
                .where(InterakcioRecord.felhasznalo_nev == felhasznalo_nev)
                .order_by(InterakcioRecord.created_at.desc())
                .limit(limit)
            )
            if tipus:
                stmt = stmt.where(InterakcioRecord.tipus == tipus)
            return list(session.scalars(stmt))

    # --- Medals (earned records) ---

    def grant_erem(
        self,
        felhasznalo_nev: str,
        erem_id: str,
        *,
        lejarat_at: datetime | None = None,
    ) -> FelhasznaloErem:
        """Grant a medal.  For repeatable medals, increments the counter."""
        with Session(self._engine) as session:
            existing = session.execute(
                select(FelhasznaloEremRecord)
                .where(
                    FelhasznaloEremRecord.felhasznalo_nev == felhasznalo_nev,
                    FelhasznaloEremRecord.erem_id == erem_id,
                )
                .limit(1)
            ).scalar_one_or_none()

            if existing:
                existing.szamlalo += 1
                existing.szerzett_at = datetime.now(timezone.utc)
                if lejarat_at is not None:
                    existing.lejarat_at = lejarat_at
                session.commit()
                return existing.to_domain()
            else:
                user_id = self._get_felhasznalo_id(felhasznalo_nev)
                rec = FelhasznaloEremRecord(
                    felhasznalo_nev=felhasznalo_nev,
                    felhasznalo_id=user_id,
                    erem_id=erem_id,
                    lejarat_at=lejarat_at,
                )
                session.add(rec)
                session.commit()
                return rec.to_domain()

    def get_eremek(
        self,
        felhasznalo_nev: str,
        include_expired: bool = False,
    ) -> list[FelhasznaloErem]:
        """Return all (active) medals for a user."""
        with Session(self._engine) as session:
            stmt = (
                select(FelhasznaloEremRecord)
                .where(FelhasznaloEremRecord.felhasznalo_nev == felhasznalo_nev)
                .order_by(FelhasznaloEremRecord.szerzett_at.desc())
            )
            records = [r.to_domain() for r in session.scalars(stmt)]
        if include_expired:
            return records
        return [r for r in records if r.aktiv]

    def has_erem(self, felhasznalo_nev: str, erem_id: str) -> bool:
        """True if the user has an active (non-expired) instance of this medal."""
        return any(r.erem_id == erem_id for r in self.get_eremek(felhasznalo_nev))

    # --- Medal catalog (EremRecord) ---

    def seed_erem_katalogus(self) -> int:
        """Insert any catalog medals from EREM_KATALOGUS not yet in the DB.

        Non-destructive: never overwrites rows that already exist, so admin
        edits made via ``upsert_erem`` are preserved.  Returns the number of
        newly inserted rows.
        """
        from felvi_games.achievements import EREM_KATALOGUS

        with Session(self._engine) as session:
            existing = {row[0] for row in session.execute(select(EremRecord.id))}
            new_count = 0
            for erem_id, erem in EREM_KATALOGUS.items():
                if erem_id not in existing:
                    session.add(EremRecord(
                        id=erem.id,
                        nev=erem.nev,
                        leiras=erem.leiras,
                        ikon=erem.ikon,
                        kategoria=erem.kategoria,
                        ideiglenes=erem.ideiglenes,
                        ervenyes_napig=erem.ervenyes_napig,
                        ismetelheto=erem.ismetelheto,
                        kep_url=erem.kep_url,
                        hang_url=erem.hang_url,
                        gif_url=erem.gif_url,
                        privat=False,
                        cel_felhasznalo=None,
                    ))
                    new_count += 1
            session.commit()
        return new_count

    def get_erem_katalogus(
        self,
        felhasznalo_nev: str | None = None,
    ) -> dict[str, Erem]:
        """Return the medal catalog from DB.

        If *felhasznalo_nev* is given, includes global medals plus private
        medals targeted at that specific user.  Without it, returns only
        global medals.
        """
        with Session(self._engine) as session:
            stmt = select(EremRecord)
            if felhasznalo_nev is not None:
                stmt = stmt.where(
                    or_(
                        EremRecord.privat == False,  # noqa: E712
                        EremRecord.cel_felhasznalo == felhasznalo_nev,
                    )
                )
            else:
                stmt = stmt.where(EremRecord.privat == False)  # noqa: E712
            return {r.id: r.to_domain() for r in session.scalars(stmt)}

    def upsert_erem(self, erem: Erem) -> None:
        """Insert or fully update a medal catalog entry."""
        with Session(self._engine) as session:
            existing = session.get(EremRecord, erem.id)
            now = datetime.now(timezone.utc)
            if existing:
                existing.nev = erem.nev
                existing.leiras = erem.leiras
                existing.ikon = erem.ikon
                existing.kategoria = erem.kategoria
                existing.ideiglenes = erem.ideiglenes
                existing.ervenyes_napig = erem.ervenyes_napig
                existing.ismetelheto = erem.ismetelheto
                existing.kep_url = erem.kep_url
                existing.hang_url = erem.hang_url
                existing.gif_url = erem.gif_url
                existing.privat = erem.privat
                existing.cel_felhasznalo = erem.cel_felhasznalo
                existing.updated_at = now
            else:
                session.add(EremRecord(
                    id=erem.id,
                    nev=erem.nev,
                    leiras=erem.leiras,
                    ikon=erem.ikon,
                    kategoria=erem.kategoria,
                    ideiglenes=erem.ideiglenes,
                    ervenyes_napig=erem.ervenyes_napig,
                    ismetelheto=erem.ismetelheto,
                    kep_url=erem.kep_url,
                    hang_url=erem.hang_url,
                    gif_url=erem.gif_url,
                    privat=erem.privat,
                    cel_felhasznalo=erem.cel_felhasznalo,
                ))
            session.commit()

    def delete_erem(self, erem_id: str) -> bool:
        """Remove a medal definition from the catalog.  Returns True if found."""
        with Session(self._engine) as session:
            rec = session.get(EremRecord, erem_id)
            if rec is None:
                return False
            session.delete(rec)
            session.commit()
            return True

    def get_user_stats(self, user_nev: str) -> "UserStats | None":
        """Return aggregated statistics for *user_nev*, or None if unknown."""
        from sqlalchemy import case, func, select

        with Session(self._engine) as sess:
            user_rec = sess.scalar(
                select(FelhasznaloRecord).where(FelhasznaloRecord.nev == user_nev)
            )
            if user_rec is None:
                return None

            sess_row = sess.execute(
                select(
                    func.count(MenetRecord.id).label("ossz"),
                    func.sum(case((MenetRecord.ended_at.is_not(None), 1), else_=0)).label("befejezett"),
                    func.sum(MenetRecord.megoldott).label("megoldott"),
                    func.sum(MenetRecord.feladat_limit).label("tervezett"),
                    func.sum(MenetRecord.pont).label("pont"),
                    func.min(MenetRecord.started_at).label("elso"),
                    func.max(MenetRecord.started_at).label("utolso"),
                ).where(MenetRecord.felhasznalo_nev == user_nev)
            ).one()

            ans_row = sess.execute(
                select(
                    func.count(MegoldasRecord.id).label("ossz"),
                    func.sum(case((MegoldasRecord.helyes.is_(True), 1), else_=0)).label("helyes"),
                    func.avg(MegoldasRecord.elapsed_sec).label("atlag_mp"),
                    func.min(MegoldasRecord.elapsed_sec).label("min_mp"),
                    func.sum(case((MegoldasRecord.segitseg_kert.is_(True), 1), else_=0)).label("hint"),
                ).where(MegoldasRecord.felhasznalo_nev == user_nev)
            ).one()

            targy_szint = sess.execute(
                select(MenetRecord.targy, MenetRecord.szint, func.count().label("n"))
                .where(MenetRecord.felhasznalo_nev == user_nev)
                .group_by(MenetRecord.targy, MenetRecord.szint)
                .order_by(MenetRecord.targy, MenetRecord.szint)
            ).all()

            nap_rows = sess.execute(
                select(func.date(MenetRecord.started_at).label("nap"), func.count().label("n"))
                .where(MenetRecord.felhasznalo_nev == user_nev)
                .group_by(func.date(MenetRecord.started_at))
                .order_by(func.date(MenetRecord.started_at))
            ).all()

            eremek = self.get_eremek(user_nev, include_expired=True)

        return UserStats(
            id=user_rec.id,
            nev=user_rec.nev,
            created_at=user_rec.created_at,
            menetek_ossz=int(sess_row.ossz or 0),
            menetek_befejezett=int(sess_row.befejezett or 0),
            megoldott_ossz=int(sess_row.megoldott or 0),
            tervezett_ossz=int(sess_row.tervezett or 0),
            pont_ossz=int(sess_row.pont or 0),
            elso_menet=sess_row.elso,
            utolso_menet=sess_row.utolso,
            valaszok_ossz=int(ans_row.ossz or 0),
            helyes_ossz=int(ans_row.helyes or 0),
            atlag_mp=float(ans_row.atlag_mp) if ans_row.atlag_mp is not None else None,
            min_mp=float(ans_row.min_mp) if ans_row.min_mp is not None else None,
            hint_ossz=int(ans_row.hint or 0),
            targy_szint=[(r.targy, r.szint, int(r.n)) for r in targy_szint],
            jateknapok=[(r.nap, int(r.n)) for r in nap_rows],
            eremek=eremek,
        )


# ---------------------------------------------------------------------------
# Stats dataclass (returned by FeladatRepository.get_user_stats)
# ---------------------------------------------------------------------------

@dataclass
class UserStats:
    id: int
    nev: str
    created_at: datetime
    menetek_ossz: int
    menetek_befejezett: int
    megoldott_ossz: int
    tervezett_ossz: int
    pont_ossz: int
    elso_menet: datetime | None
    utolso_menet: datetime | None
    valaszok_ossz: int
    helyes_ossz: int
    atlag_mp: float | None
    min_mp: float | None
    hint_ossz: int
    targy_szint: list[tuple[str, str, int]] = field(default_factory=list)
    jateknapok: list[tuple[str, int]] = field(default_factory=list)
    eremek: list[FelhasznaloErem] = field(default_factory=list)

    @property
    def accuracy_pct(self) -> float:
        return (100.0 * self.helyes_ossz / self.valaszok_ossz) if self.valaszok_ossz else 0.0
