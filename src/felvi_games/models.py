"""Data models for the felvételi quiz application."""

from __future__ import annotations

import dataclasses
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
    tts_kerdes_path: str | None = None      # relative path to TTS MP3 for the question
    tts_magyarazat_path: str | None = None  # relative path to TTS MP3 for the explanation
    # --- extraction context ---
    kontextus: str | None = None            # shared preamble/table/figure text (GPT-extracted)
    fl_szoveg_path: str | None = None       # relative path to cached feladatlap plain text
    ut_szoveg_path: str | None = None       # relative path to cached útmutató plain text

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
            kontextus=d.get("kontextus"),
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
            tts_kerdes_path=r.tts_kerdes_path,
            tts_magyarazat_path=r.tts_magyarazat_path,
            kontextus=r.kontextus,
            fl_szoveg_path=r.fl_szoveg_path,
            ut_szoveg_path=r.ut_szoveg_path,
        )

    def with_assets(
        self,
        tts_kerdes_path: str | None = None,
        tts_magyarazat_path: str | None = None,
    ) -> "Feladat":
        """Return a new Feladat with updated asset path fields (frozen → copy)."""
        return dataclasses.replace(
            self,
            tts_kerdes_path=tts_kerdes_path if tts_kerdes_path is not None else self.tts_kerdes_path,
            tts_magyarazat_path=tts_magyarazat_path if tts_magyarazat_path is not None else self.tts_magyarazat_path,
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


# ---------------------------------------------------------------------------
# Felvételi kategóriák nevezéktana
# ---------------------------------------------------------------------------

class KategoriaKulcs(Enum):
    """A három felvételi típus belső azonosítója (= mappa neve)."""
    OSZTALY_6 = "6_osztaly"
    OSZTALY_8 = "8_osztaly"
    EVFOLYAM_9 = "9_evfolyam"


@dataclass(frozen=True)
class KategoriaNevezektan:
    iskola_tipusa: str  # pl. "6 osztályos gimnázium"
    cel_evfolyam: str   # ahova a tanuló belép, pl. "7. osztály"
    szint_ertek: str    # Feladat.szint szűrőérték, pl. "6 osztályos"
    cli_kulcs: str      # --only parancssori érték, pl. "6", "8", "4"
    rovid: str          # rövid megnevezés a UI-hoz
    teljes: str         # teljes hivatalos megnevezés
    # A mappa neve mindig a kulcs enum .value-ja – nem duplikáljuk.


KATEGORIA_INFO: dict[KategoriaKulcs, KategoriaNevezektan] = {
    KategoriaKulcs.OSZTALY_6: KategoriaNevezektan(
        iskola_tipusa="6 osztályos gimnázium",
        cel_evfolyam="7. osztály",
        szint_ertek="6 osztályos",
        cli_kulcs="6",
        rovid="6 osztályos gimnázium (7. osztályba lépőknek)",
        teljes=(
            "Felvételi feladatsorok 6 osztályos gimnáziumba – "
            "a 7. osztályba lépő tanulóknak"
        ),
    ),
    KategoriaKulcs.OSZTALY_8: KategoriaNevezektan(
        iskola_tipusa="8 osztályos gimnázium",
        cel_evfolyam="5. osztály",
        szint_ertek="8 osztályos",
        cli_kulcs="8",
        rovid="8 osztályos gimnázium (5. osztályba lépőknek)",
        teljes=(
            "Felvételi feladatsorok 8 osztályos gimnáziumba – "
            "az 5. osztályba lépő tanulóknak"
        ),
    ),
    KategoriaKulcs.EVFOLYAM_9: KategoriaNevezektan(
        iskola_tipusa="4 osztályos gimnázium",
        cel_evfolyam="9. évfolyam",
        szint_ertek="4 osztályos",
        cli_kulcs="4",
        rovid="4 osztályos gimnázium (9. évfolyamra lépőknek)",
        teljes=(
            "Felvételi feladatsorok a 9. évfolyamra – "
            "4 osztályos gimnáziumba felvételizőknek"
        ),
    ),
}
