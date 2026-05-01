"""Data models for the felvételi quiz application."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from felvi_games.db import FeladatCsoportRecord, FeladatRecord


class Fazis(str, Enum):
    VALASZTAS = "valasztas"
    KERDES = "kerdes"
    EREDMENY = "eredmeny"


class InterakcioTipus(str, Enum):
    """Fine-grained player behaviour events written to the interaction log."""
    MENET_INDUL = "menet_indul"
    MENET_VEGZETT = "menet_vegzett"
    HELYES_VALASZ = "helyes_valasz"
    RESZLEGES_VALASZ = "reszleges_valasz"
    HELYTELEN_VALASZ = "helytelen_valasz"
    SEGITSEG_KERT = "segitseg_kert"
    HIBAJELEZES = "hibajelezes"
    TARGY_VALTAS = "targy_valtas"
    SZINT_VALTAS = "szint_valtas"
    TTS_LEJATSZO = "tts_lejatszo"
    FELADAT_KIHAGYAS = "feladat_kihagyas"
    UJRAERTEKELES = "ujraertekeles"
    UJRAERTEKELES_JUTALOM = "ujraertekeles_jutalom"


class FeladatTipus(str, Enum):
    """Feladat típusok az értékelési logika számára."""
    NYILT_VALASZ = "nyilt_valasz"       # szabad szöveges válasz
    TOBBVALASZTOS = "tobbvalasztos"     # felkínált opciókból kell választani
    PAROSITAS = "parositas"             # elemeket kell összepárosítani
    IGAZ_HAMIS = "igaz_hamis"           # igaz/hamis döntés
    FOGALMAZAS = "fogalmazas"           # hosszabb írásbeli szöveg (rubric-alapú)
    KITOLTES = "kitoltes"               # hiányos szöveg kiegészítése


class FeladatStatusz(str, Enum):
    """Feladat életciklus-állapot."""
    AKTIV = "aktiv"          # aktuális verzió, megjelenik a játékban
    ARCHIVALT = "archivalt"  # korai verzió, régi hivatkozások számára megőrzve


# ---------------------------------------------------------------------------
# Module-level helpers (used by Feladat.from_dict / from_record)
# ---------------------------------------------------------------------------

def _parse_str_list(value: object) -> list[str] | None:
    """Convert GPT output or dict value to list[str] | None.

    Accepts:
    - None / missing → None
    - list            → list[str] (elements coerced to str)
    - str (JSON)      → parsed list[str]
    - str (plain)     → [str] (single-element list)
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value] if value else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                return [str(v) for v in parsed] if parsed else None
            except (json.JSONDecodeError, TypeError):
                pass
        return [stripped]
    return None


def _json_to_list(value: str | None) -> list[str] | None:
    """Deserialize a JSON-encoded list stored in a DB Text column."""
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return [str(v) for v in parsed] if parsed else None
    except (json.JSONDecodeError, TypeError):
        return None


def _list_to_json(value: list[str] | None) -> str | None:
    """Serialize a list[str] to a JSON string for DB storage."""
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class FeladatCsoport:
    """Összetartozó részfeladatok (pl. 3a, 3b, 3c) csoportja.

    A kinyerés flat marad – a csoportosítás post-processing lépés.
    A csoport tartalmazza a közös kontextust és az összpontszámot.
    """
    id: str                              # pl. "mat4_2025_1_3"
    targy: str
    szint: str
    feladat_sorszam: str                 # főfeladat száma, pl. "3"
    ev: int | None = None
    valtozat: int | None = None
    kontextus: str | None = None         # közös bevezető szöveg/ábraleírás
    abra_van: bool = False
    feladat_oldal: int | None = None
    fl_pdf_path: str | None = None
    ut_pdf_path: str | None = None
    fl_szoveg_path: str | None = None
    ut_szoveg_path: str | None = None
    sorrend_kotelezo: bool = False       # ha True, a részek sorban oldandók meg
    max_pont_ossz: int = 1               # csoport összpontszáma

    @classmethod
    def from_record(cls, r: "FeladatCsoportRecord") -> "FeladatCsoport":
        return cls(
            id=r.id,
            targy=r.targy,
            szint=r.szint,
            feladat_sorszam=r.feladat_sorszam,
            ev=r.ev,
            valtozat=r.valtozat,
            kontextus=r.kontextus,
            abra_van=r.abra_van,
            feladat_oldal=r.feladat_oldal,
            fl_pdf_path=r.fl_pdf_path,
            ut_pdf_path=r.ut_pdf_path,
            fl_szoveg_path=r.fl_szoveg_path,
            ut_szoveg_path=r.ut_szoveg_path,
            sorrend_kotelezo=r.sorrend_kotelezo,
            max_pont_ossz=r.max_pont_ossz,
        )


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
    ev: int | None = None                # exam year (e.g. 2025)
    valtozat: int | None = None          # variant within year (1 or 2)
    feladat_sorszam: str | None = None   # position in exam (e.g. "1a", "2b", "3")
    # --- group membership ---
    csoport_id: str | None = None        # FK to FeladatCsoport.id
    csoport_sorrend: int | None = None   # order within the group (1, 2, 3 …)
    # --- task type & scoring ---
    feladat_tipus: str | None = None     # FeladatTipus value
    elfogadott_valaszok: list[str] | None = None   # all accepted correct answers
    valaszlehetosegek: list[str] | None = None     # offered options (multiple-choice / matching)
    max_pont: int = 1                    # max points for this sub-task
    reszpontozas: str | None = None      # partial scoring rule, e.g. "6/6=3p, 5/6=2p"
    ertekeles_megjegyzes: str | None = None  # grader notes, exceptions, special rules
    # --- compiled assets (optional, cached after first use) ---
    tts_kerdes_path: str | None = None      # relative path to TTS MP3 for the question
    tts_magyarazat_path: str | None = None  # relative path to TTS MP3 for the explanation
    tts_kerdes_szoveg: str | None = None    # LLM-processed spoken text stored after TTS generation
    tts_kerdes_bemenet_hash: str | None = None  # SHA256[:12] of the raw markdown input used to generate the TTS
    # --- extraction context ---
    kontextus: str | None = None            # shared preamble/table/figure text (for standalone tasks)
    abra_van: bool = False                  # True if task references a figure/graph
    feladat_oldal: int | None = None        # PDF page number where the task appears
    fl_szoveg_path: str | None = None       # relative path to cached feladatlap plain text
    ut_szoveg_path: str | None = None       # relative path to cached útmutató plain text
    fl_pdf_path: str | None = None          # relative path to feladatlap PDF (under exams dir)
    ut_pdf_path: str | None = None          # relative path to útmutató PDF (under exams dir)
    review_elvegezve: bool = False          # True after a human/AI review pass
    review_megjegyzes: str | None = None    # free-text reviewer comment
    # --- versioning ---
    verzio: int = 1                                           # version counter (1 = first)
    statusz: str = FeladatStatusz.AKTIV                       # aktiv | archivalt
    elozmeny_feladat_id: str | None = None                    # ID of the previous version

    @property
    def pdf_source(self) -> str | None:
        """Feladatlap PDF filename, derived from fl_pdf_path."""
        return Path(self.fl_pdf_path).name if self.fl_pdf_path else None

    @property
    def ut_source(self) -> str | None:
        """Útmutató PDF filename, derived from ut_pdf_path."""
        return Path(self.ut_pdf_path).name if self.ut_pdf_path else None

    @classmethod
    def from_dict(cls, d: dict, targy: str = "") -> "Feladat":
        ev_raw = d.get("ev")
        val_raw = d.get("valtozat")
        max_pont_raw = d.get("max_pont", 1)
        return cls(
            id=d["id"],
            neh=d["neh"],
            szint=d["szint"],
            kerdes=d["kerdes"],
            helyes_valasz=d["helyes_valasz"],
            hint=d["hint"],
            magyarazat=d["magyarazat"],
            targy=targy or d.get("targy", ""),
            ev=int(ev_raw) if ev_raw is not None else None,
            valtozat=int(val_raw) if val_raw is not None else None,
            feladat_sorszam=d.get("feladat_sorszam"),
            csoport_id=d.get("csoport_id"),
            csoport_sorrend=int(d["csoport_sorrend"]) if d.get("csoport_sorrend") is not None else None,
            feladat_tipus=d.get("feladat_tipus"),
            elfogadott_valaszok=_parse_str_list(d.get("elfogadott_valaszok")),
            valaszlehetosegek=_parse_str_list(d.get("valaszlehetosegek")),
            max_pont=int(max_pont_raw) if max_pont_raw is not None else 1,
            reszpontozas=d.get("reszpontozas"),
            ertekeles_megjegyzes=d.get("ertekeles_megjegyzes"),
            kontextus=d.get("kontextus"),
            abra_van=bool(d.get("abra_van", False)),
            feladat_oldal=int(d["feladat_oldal"]) if d.get("feladat_oldal") else None,
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
            ev=r.ev,
            valtozat=r.valtozat,
            feladat_sorszam=r.feladat_sorszam,
            csoport_id=r.csoport_id,
            csoport_sorrend=r.csoport_sorrend,
            feladat_tipus=r.feladat_tipus,
            elfogadott_valaszok=_json_to_list(r.elfogadott_valaszok),
            valaszlehetosegek=_json_to_list(r.valaszlehetosegek),
            max_pont=r.max_pont if r.max_pont is not None else 1,
            reszpontozas=r.reszpontozas,
            ertekeles_megjegyzes=r.ertekeles_megjegyzes,
            tts_kerdes_path=r.tts_kerdes_path,
            tts_magyarazat_path=r.tts_magyarazat_path,
            tts_kerdes_szoveg=r.tts_kerdes_szoveg,
            tts_kerdes_bemenet_hash=getattr(r, "tts_kerdes_bemenet_hash", None),
            kontextus=r.kontextus,
            abra_van=r.abra_van,
            feladat_oldal=r.feladat_oldal,
            fl_szoveg_path=r.fl_szoveg_path,
            ut_szoveg_path=r.ut_szoveg_path,
            fl_pdf_path=r.fl_pdf_path,
            ut_pdf_path=r.ut_pdf_path,
            review_elvegezve=r.review_elvegezve,
            review_megjegyzes=r.review_megjegyzes,
            verzio=r.verzio if r.verzio is not None else 1,
            statusz=r.statusz if r.statusz is not None else FeladatStatusz.AKTIV,
            elozmeny_feladat_id=r.elozmeny_feladat_id,
        )

    def with_assets(
        self,
        tts_kerdes_path: str | None = None,
        tts_magyarazat_path: str | None = None,
        tts_kerdes_szoveg: str | None = None,
        tts_kerdes_bemenet_hash: str | None = None,
    ) -> "Feladat":
        """Return a new Feladat with updated asset path fields (frozen → copy)."""
        return dataclasses.replace(
            self,
            tts_kerdes_path=tts_kerdes_path if tts_kerdes_path is not None else self.tts_kerdes_path,
            tts_magyarazat_path=tts_magyarazat_path if tts_magyarazat_path is not None else self.tts_magyarazat_path,
            tts_kerdes_szoveg=tts_kerdes_szoveg if tts_kerdes_szoveg is not None else self.tts_kerdes_szoveg,
            tts_kerdes_bemenet_hash=tts_kerdes_bemenet_hash if tts_kerdes_bemenet_hash is not None else self.tts_kerdes_bemenet_hash,
        )

    def elfogadott_valaszok_vagy_helyes(self) -> list[str]:
        """Return the canonical accepted-answer list, falling back to helyes_valasz."""
        if self.elfogadott_valaszok:
            return self.elfogadott_valaszok
        return [self.helyes_valasz]

    def neh_csillag(self) -> str:
        return "⭐" * self.neh + "☆" * (3 - self.neh)

    def tts_szoveg(self) -> str:
        """LLM-processed spoken text for TTS, or raw kerdes if not yet generated."""
        return self.tts_kerdes_szoveg or self.kerdes

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


@dataclass(frozen=True)
class Menet:
    """A single playing session for one user."""
    id: int
    felhasznalo: str
    targy: str
    szint: str
    feladat_limit: int        # planned task count
    megoldott: int            # completed tasks
    pont: int                 # total score in session
    started_at: datetime
    ended_at: datetime | None = None

    @property
    def lezart(self) -> bool:
        return self.ended_at is not None or self.megoldott >= self.feladat_limit

    @property
    def idotartam_perc(self) -> str:
        """Duration as M:SS string (handles tz-naive/aware mismatch)."""
        end = self.ended_at or datetime.now(timezone.utc)
        s = self.started_at.replace(tzinfo=None) if self.started_at.tzinfo else self.started_at
        e = end.replace(tzinfo=None) if end.tzinfo else end
        secs = int((e - s).total_seconds())
        m, sec = divmod(abs(secs), 60)
        return f"{m}:{sec:02d}"


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
    utolso_valasz: str = ""      # the answer submitted for the last question
    ertekeles: Ertekeles | None = None
    tts_audio: bytes | None = None
    # --- user & session tracking ---
    felhasznalo: str = ""
    menet_id: int | None = None
    menet_cel: int = 10
    menet_megoldott: int = 0       # answers in current session
    kerdes_kezdete: datetime | None = None
    segitseg_kert: bool = False    # hint used on current question
    hibajelezes: bool = False      # error flagged on current question
    feladat_sor: list[str] = field(default_factory=list)  # ordered queue of feladat IDs

    def record_answer(self, feladat: Feladat, ertekeles: Ertekeles) -> None:
        self.megoldott_ids.add(feladat.id)
        self.menet_megoldott += 1
        self.ertekeles = ertekeles
        if ertekeles.pont > 0:
            self.pont += ertekeles.pont
        if ertekeles.helyes:
            self.streak += 1
            self.max_streak = max(self.streak, self.max_streak)
        elif ertekeles.pont == 0:
            self.streak = 0  # full miss resets streak; partial keeps it
        self.kerdes_kezdete = None

    def reset(self) -> None:
        """Full reset, keeps the current user logged in."""
        nev = self.felhasznalo
        self.__init__()  # type: ignore[misc]
        self.felhasznalo = nev

    def uj_menet(self) -> None:
        """Start a fresh session, keeping user, targy, szint and menet_cel."""
        nev = self.felhasznalo
        cel = self.menet_cel
        targy = self.targy
        szint = self.szint
        self.__init__()  # type: ignore[misc]
        self.felhasznalo = nev
        self.menet_cel = cel
        self.targy = targy
        self.szint = szint


# ---------------------------------------------------------------------------
# Gamification – medals / achievements
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Erem:
    """Static medal/achievement definition (catalog entry, not persisted)."""
    id: str                         # unique slug, e.g. "elso_menet"
    nev: str                        # Hungarian display name
    leiras: str                     # description shown to the user
    ikon: str                       # emoji (or URL to image/SVG)
    kategoria: str                  # "teljesitmeny" | "kitartas" | "rendszeresseg" | "felfedezes" | "merfoldko"
    ideiglenes: bool = False        # True → award expires
    ervenyes_napig: int | None = None  # days valid when ideiglenes=True
    ismetelheto: bool = False       # True → can be earned multiple times
    # --- rich media assets (relative path under assets/eremek/<id>/ OR external URL) ---
    kep_url: str | None = None      # static PNG/JPEG image
    hang_url: str | None = None     # MP3 award fanfare / sound effect
    gif_url: str | None = None      # animated GIF (user-supplied or external URL)
    # --- visibility ---
    privat: bool = False                    # True → only visible / earnable by cel_felhasznalo
    cel_felhasznalo: str | None = None      # specific user this private medal targets
    # --- dynamic condition (LLM-generated, machine-evaluable) ---
    condition: dict | None = None           # structured condition dict; see achievements._eval_dynamic_condition
    condition_valid_from: "datetime | None" = None  # anchor: count only events AFTER this timestamp


@dataclass(frozen=True)
class FelhasznaloErem:
    """One earned-medal record for a user (domain model for FelhasznaloEremRecord)."""
    id: int
    felhasznalo: str
    erem_id: str
    szerzett: datetime
    lejarat: datetime | None
    szamlalo: int = 1               # how many times earned (for repeatable medals)

    @property
    def aktiv(self) -> bool:
        """False only for temporary medals that have expired."""
        if self.lejarat is None:
            return True
        now = datetime.now(timezone.utc)
        exp = self.lejarat.replace(tzinfo=timezone.utc) if self.lejarat.tzinfo is None else self.lejarat
        return now < exp


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
