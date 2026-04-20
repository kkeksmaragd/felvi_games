"""Data models for the felvételi quiz application."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from felvi_games.db import FeladatRecord


class Fazis(str, Enum):
    VALASZTAS = "valasztas"
    KERDES = "kerdes"
    EREDMENY = "eredmeny"


@dataclass(frozen=True)
class Feladat:
    id: str
    neh: int
    szint: str
    kerdes: str
    helyes_valasz: str
    hint: str
    magyarazat: str
    # --- exam provenance ---
    targy: str = ""
    pdf_source: str | None = None        # feladatlap PDF filename (e.g. M8_2025_1_fl.pdf)
    ut_source: str | None = None         # útmutató PDF filename  (e.g. M8_2025_1_ut.pdf)
    ev: int | None = None                # exam year (e.g. 2025)
    valtozat: int | None = None          # variant within year (1 or 2)
    feladat_sorszam: str | None = None   # position in exam (e.g. "1a", "2b", "3")
    # --- compiled assets (optional, cached after first use) ---
    tts_kerdes: bytes | None = None      # pre-rendered TTS for the question
    tts_magyarazat: bytes | None = None  # pre-rendered TTS for the explanation

    @classmethod
    def from_dict(cls, d: dict, targy: str = "") -> "Feladat":
        ev_raw = d.get("ev")
        val_raw = d.get("valtozat")
        return cls(
            id=d["id"],
            neh=d["neh"],
            szint=d["szint"],
            kerdes=d["kerdes"],
            helyes_valasz=d["helyes_valasz"],
            hint=d["hint"],
            magyarazat=d["magyarazat"],
            targy=targy or d.get("targy", ""),
            pdf_source=d.get("pdf_source"),
            ut_source=d.get("ut_source"),
            ev=int(ev_raw) if ev_raw is not None else None,
            valtozat=int(val_raw) if val_raw is not None else None,
            feladat_sorszam=d.get("feladat_sorszam"),
        )

    @classmethod
    def from_record(cls, r: "FeladatRecord") -> "Feladat":
        return cls(
            id=r.id,
            neh=r.neh,
            szint=r.szint,
            kerdes=r.kerdes,
            helyes_valasz=r.helyes_valasz,
            hint=r.hint,
            magyarazat=r.magyarazat,
            targy=r.targy,
            pdf_source=r.pdf_source,
            ut_source=r.ut_source,
            ev=r.ev,
            valtozat=r.valtozat,
            feladat_sorszam=r.feladat_sorszam,
            tts_kerdes=r.tts_kerdes,
            tts_magyarazat=r.tts_magyarazat,
        )

    def with_assets(
        self,
        tts_kerdes: bytes | None = None,
        tts_magyarazat: bytes | None = None,
    ) -> "Feladat":
        """Return a new Feladat with updated asset fields (frozen → copy)."""
        return Feladat(
            id=self.id,
            neh=self.neh,
            szint=self.szint,
            kerdes=self.kerdes,
            helyes_valasz=self.helyes_valasz,
            hint=self.hint,
            magyarazat=self.magyarazat,
            targy=self.targy,
            pdf_source=self.pdf_source,
            ut_source=self.ut_source,
            ev=self.ev,
            valtozat=self.valtozat,
            feladat_sorszam=self.feladat_sorszam,
            tts_kerdes=tts_kerdes if tts_kerdes is not None else self.tts_kerdes,
            tts_magyarazat=tts_magyarazat if tts_magyarazat is not None else self.tts_magyarazat,
        )

    def neh_csillag(self) -> str:
        return "⭐" * self.neh + "☆" * (3 - self.neh)

    def tts_szoveg(self) -> str:
        return self.kerdes

    def eredmeny_tts_szoveg(self, visszajelzes: str) -> str:
        return (
            f"{visszajelzes} "
            f"A helyes válasz: {self.helyes_valasz}. "
            f"{self.magyarazat}"
        )


@dataclass(frozen=True)
class Ertekeles:
    helyes: bool
    visszajelzes: str
    pont: int

    @classmethod
    def from_dict(cls, d: dict) -> "Ertekeles":
        return cls(
            helyes=bool(d.get("helyes", False)),
            visszajelzes=str(d.get("visszajelzes", "")),
            pont=int(d.get("pont", 0)),
        )

    @classmethod
    def hiba(cls) -> "Ertekeles":
        return cls(helyes=False, visszajelzes="Nem sikerült értékelni.", pont=0)


@dataclass
class GameState:
    pont: int = 0
    streak: int = 0
    max_streak: int = 0
    megoldott_ids: set[str] = field(default_factory=set)
    aktualis: Feladat | None = None
    targy: str = "matek"
    szint: str = "mind"
    fazis: Fazis = Fazis.VALASZTAS
    atiras: str = ""
    ertekeles: Ertekeles | None = None
    tts_audio: bytes | None = None

    def record_answer(self, feladat: Feladat, ertekeles: Ertekeles) -> None:
        self.megoldott_ids.add(feladat.id)
        self.ertekeles = ertekeles
        if ertekeles.helyes:
            self.pont += ertekeles.pont
            self.streak += 1
            self.max_streak = max(self.streak, self.max_streak)
        else:
            self.streak = 0

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]
