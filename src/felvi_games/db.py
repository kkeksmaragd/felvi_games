"""Persistence layer – SQLAlchemy 2.x + SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
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
from felvi_games.models import Ertekeles, Feladat

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
    pdf_source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ut_source: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    ev: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    valtozat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feladat_sorszam: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Compiled TTS assets – relative paths to MP3 files under assets_dir
    tts_kerdes_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tts_magyarazat_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Extraction context
    kontextus: Mapped[str | None] = mapped_column(Text, nullable=True)
    fl_szoveg_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ut_szoveg_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

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
    adott_valasz: Mapped[str] = mapped_column(Text, nullable=False)
    helyes: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pont: Mapped[int] = mapped_column(Integer, nullable=False)
    visszajelzes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    feladat: Mapped["FeladatRecord"] = relationship(back_populates="megoldasok")


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
                existing.pdf_source = feladat.pdf_source
                existing.ut_source = feladat.ut_source
                existing.ev = feladat.ev
                existing.valtozat = feladat.valtozat
                existing.feladat_sorszam = feladat.feladat_sorszam
                if feladat.tts_kerdes_path is not None:
                    existing.tts_kerdes_path = feladat.tts_kerdes_path
                if feladat.tts_magyarazat_path is not None:
                    existing.tts_magyarazat_path = feladat.tts_magyarazat_path
                if feladat.kontextus is not None:
                    existing.kontextus = feladat.kontextus
                if feladat.fl_szoveg_path is not None:
                    existing.fl_szoveg_path = feladat.fl_szoveg_path
                if feladat.ut_szoveg_path is not None:
                    existing.ut_szoveg_path = feladat.ut_szoveg_path
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
                    pdf_source=feladat.pdf_source,
                    ut_source=feladat.ut_source,
                    ev=feladat.ev,
                    valtozat=feladat.valtozat,
                    feladat_sorszam=feladat.feladat_sorszam,
                    tts_kerdes_path=feladat.tts_kerdes_path,
                    tts_magyarazat_path=feladat.tts_magyarazat_path,
                    kontextus=feladat.kontextus,
                    fl_szoveg_path=feladat.fl_szoveg_path,
                    ut_szoveg_path=feladat.ut_szoveg_path,
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
                        pdf_source=f.pdf_source,
                        ut_source=f.ut_source,
                        ev=f.ev,
                        valtozat=f.valtozat,
                        feladat_sorszam=f.feladat_sorszam,
                        tts_kerdes_path=f.tts_kerdes_path,
                        tts_magyarazat_path=f.tts_magyarazat_path,
                        kontextus=f.kontextus,
                        fl_szoveg_path=f.fl_szoveg_path,
                        ut_szoveg_path=f.ut_szoveg_path,
                        updated_at=now,
                    ))
                else:
                    session.add(FeladatRecord(
                        id=f.id, targy=f.targy, neh=f.neh, szint=f.szint,
                        kerdes=f.kerdes, helyes_valasz=f.helyes_valasz,
                        hint=f.hint, magyarazat=f.magyarazat,
                        pdf_source=f.pdf_source,
                        ut_source=f.ut_source,
                        ev=f.ev,
                        valtozat=f.valtozat,
                        feladat_sorszam=f.feladat_sorszam,
                        tts_kerdes_path=f.tts_kerdes_path,
                        tts_magyarazat_path=f.tts_magyarazat_path,
                        kontextus=f.kontextus,
                        fl_szoveg_path=f.fl_szoveg_path,
                        ut_szoveg_path=f.ut_szoveg_path,
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
    ) -> None:
        with Session(self._engine) as session:
            session.add(MegoldasRecord(
                feladat_id=feladat.id,
                adott_valasz=adott_valasz,
                helyes=ertekeles.helyes,
                pont=ertekeles.pont,
                visszajelzes=ertekeles.visszajelzes,
            ))
            session.commit()

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
