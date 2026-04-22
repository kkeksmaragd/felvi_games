"""Feladat extractor: PDF → text → GPT → Feladat objects → DB.

Pipeline
--------
1. pdf_to_text()          – pdftotext → one string per PDF
2. extract_feladatok()    – GPT parses text pair (feladatlap + útmutató)
                            and returns a list of Feladat objects
3. CLI main()             – glues everything together, upserts into DB
                            (optional: --review to run interactive CLI review)

Filename convention
-------------------
  A8_YYYY_N_fl.pdf  → Magyar feladatlap (Anyanyelv)
  M8_YYYY_N_fl.pdf  → Matek feladatlap
  *_ut.pdf          → Javítási útmutató (answer key) – paired with its _fl
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Iterator
import dataclasses

import pdftotext
from dotenv import load_dotenv
from openai import OpenAI

from felvi_games.config import get_db_path, get_exams_dir, relative_text_path, text_cache_path
from felvi_games.db import FeladatRepository
from felvi_games.models import Feladat, FeladatCsoport, _parse_str_list
from felvi_games.review import print_csoport, print_feladat, review_feladatok

load_dotenv(Path(__file__).parent.parent.parent / ".env", override=True)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Filename prefix → subject name
_TARGY_MAP: dict[str, str] = {"A": "magyar", "M": "matek"}

# Filename gym-type number → szint_ertek (matches models.KATEGORIA_INFO)
# A fájlnévben lévő szám a TANULÓ jelenlegi évfolyamát jelöli:
# A8_ / M8_ → 8. osztályos tanuló → 4 osztályos gimnázium felvételi
# A6_ / M6_ → 6. osztályos tanuló → 6 osztályos gimnázium felvételi
# A4_ / M4_ → 4. osztályos tanuló → 8 osztályos gimnázium felvételi
_SZINT_MAP: dict[int, str] = {8: "4 osztályos", 6: "6 osztályos", 4: "8 osztályos"}

# CLI --szint érték (gimnázium osztályszám) → szint label
_CLI_SZINT_MAP: dict[str, str] = {"4": "4 osztályos", "6": "6 osztályos", "8": "8 osztályos"}

# Difficulty descriptions passed to GPT
_NEH_SCALE = (
    "1 = könnyű (alapszintű számolás / szóértés, egyértelmű válasz), "
    "2 = közepes (több lépés / következtetés kell), "
    "3 = nehéz (komplex, ritka tudás vagy kreativitás kell)"
)

# Regexp for task block boundaries: matches "1.   ", "10.   " at line start
_TASK_BLOCK_RE = re.compile(r"^\s{0,4}(\d{1,2})\.\s{3,}")
# Matches [Oldal N] page markers emitted by pdf_to_text
_PAGE_MARKER_RE = re.compile(r"^\[Oldal (\d+)\]")


@dataclasses.dataclass(frozen=True)
class TaskBlock:
    """One main task's raw text extracted from a feladatlap or útmutató."""

    sorszam: int      # task number as printed (1-based)
    oldal_start: int  # PDF page where the task header was found
    sor_start: int    # 1-based global line number of the task header line
    raw_text: str     # full block text (header + body), stripped


# ---------------------------------------------------------------------------
# Step 1 – PDF → text
# ---------------------------------------------------------------------------


def pdf_to_text(path: Path) -> str:
    """Extract all pages from *path* and return them joined as one string.
    Each page is prefixed with [Oldal N] so GPT can identify page numbers."""
    with open(path, "rb") as fh:
        pages = list(pdftotext.PDF(fh))
    return "\n\n".join(f"[Oldal {i + 1}]\n{page}" for i, page in enumerate(pages))


# ---------------------------------------------------------------------------
# Step 1b – regexp-based task block splitting
# ---------------------------------------------------------------------------


def split_into_task_blocks(text: str) -> list[TaskBlock]:
    """Split feladatlap/útmutató text into per-task blocks using a regexp.

    Detects main task headers matching ``r'^\\s{0,4}(\\d{1,2})\\.\\s{3,}'``
    (e.g. ``"1.   Feladat szövege"``).  ``[Oldal N]`` markers are tracked so
    every block carries the PDF page where it starts.

    Sub-task lines (``a)``, ``b)`` …) that follow a task header but precede
    the next main-task header are included in the current block's text.

    Returns blocks sorted by ``sorszam``.  Returns an empty list if no task
    headers are found (caller should fall back to the single-batch path).
    """
    lines = text.splitlines()
    blocks: list[TaskBlock] = []

    current_sorszam: int | None = None
    current_oldal: int = 1
    current_oldal_start: int = 1
    current_sor_start: int = 1
    current_lines: list[str] = []

    for line_num, line in enumerate(lines, start=1):
        page_m = _PAGE_MARKER_RE.match(line)
        if page_m:
            current_oldal = int(page_m.group(1))

        task_m = _TASK_BLOCK_RE.match(line)
        if task_m:
            # Close previous block before opening a new one
            if current_sorszam is not None:
                blocks.append(TaskBlock(
                    sorszam=current_sorszam,
                    oldal_start=current_oldal_start,
                    sor_start=current_sor_start,
                    raw_text="\n".join(current_lines).strip(),
                ))
            current_sorszam = int(task_m.group(1))
            current_oldal_start = current_oldal
            current_sor_start = line_num
            current_lines = [line]
        else:
            current_lines.append(line)

    # Flush last block
    if current_sorszam is not None:
        blocks.append(TaskBlock(
            sorszam=current_sorszam,
            oldal_start=current_oldal_start,
            sor_start=current_sor_start,
            raw_text="\n".join(current_lines).strip(),
        ))

    return sorted(blocks, key=lambda b: b.sorszam)


def annotate_block(block: TaskBlock) -> str:
    """Prepend a machine-readable metadata header to a block's raw text.

    Format: ``## [Feladat N | Oldal X, sor Y]``
    GPT is instructed to read ``feladat_oldal`` from this header.
    """
    return (
        f"## [Feladat {block.sorszam} | Oldal {block.oldal_start}, sor {block.sor_start}]\n"
        f"{block.raw_text}"
    )


def match_fl_ut_blocks(
    fl_blocks: list[TaskBlock],
    ut_blocks: list[TaskBlock],
) -> list[tuple[TaskBlock, TaskBlock | None]]:
    """Pair fl and ut blocks by ``sorszam``.

    For each fl block, the matching ut block (same sorszam) is found.
    If the útmutató omits a task, the second element is ``None``.
    Order follows *fl_blocks*.
    """
    ut_map: dict[int, TaskBlock] = {b.sorszam: b for b in ut_blocks}
    return [(fl_b, ut_map.get(fl_b.sorszam)) for fl_b in fl_blocks]


# ---------------------------------------------------------------------------
# Step 2 – text pair → Feladat list (via GPT)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Felvételi feladatsor elemző vagy. Feladatlapot és javítási útmutatót kapod.
Minden egyes részfeladatból (pl. 1a, 1b, 2a…) ONE JSON objektumot generálj.
Komplex számítási/rajz/táblázat feladatokat egyszerűsítsd: a kérdést úgy fogalmazd,
hogy szövegesen (egy válasszal) megválaszolható legyen, és add meg a helyes választ is.
Fontos: ha több részfeladat egyazon bevezető szövegre, ábrára vagy táblázatra hivatkozik,
minden érintett feladat kontextus mezőjébe másold be a teljes közös részt.
A feladat típusát és az elfogadott válaszok listáját mindig az útmutatóból olvasd ki.
Fogalmazás- vagy esszéírást igénylő feladatokat (amelyekhez nincs rövid, egyértelmű
helyes válasz, pl. összefüggő szöveg írása) NE generálj – hagyd ki.
Ha a feladat képre hivatkozik és az útmutatóból a helyes válasz kiolvasható,
vedd be (az elfogadott_valaszok mezőbe is sorold fel); ha az útmutatóból sem
olvasható ki, hagyd ki a feladatot.
Ha a helyes válasz több elem összessége (pl. „A, B, E"), a helyes_valasz mezőbe
vesszővel elválasztva írd; az elfogadott_valaszok mezőbe az összes elfogadható
variációt sorold fel.
A feladat_oldal értékét a feladatblokk fejlécéből olvasd ki:
„## [Feladat N | Oldal X, sor Y]" → X az oldalszám.
Ha ilyen fejléc nincs, becsüld meg a [Oldal N] markerekből.
Formázás: a szöveges mezőkben (kerdes, helyes_valasz, magyarazat, hint, kontextus)
használj Markdown formázást (pl. **félkövér**, listák). Matematikai képleteket és
kifejezéseket LaTeX jelöléssel írd: inline képleteknél $...$, önálló sorban lévő
(display) képleteknél $$...$$. Például: „A kerület $2r\\pi$." vagy „$$\\frac{a}{b} = c$$"."""

_USER_TEMPLATE = """\
## Sorozat adatok
- Tantárgy: {targy}
- Forrás PDF: {pdf_source}
- Szint: {szint}

## Feladatlap szövege
{fl_text}

## Javítási útmutató szövege
{ut_text}

---

Generálj egy JSON objektumot, amely egyetlen "feladatok" kulcsot tartalmaz.
Az érték egy lista; minden elem tartalmazza:
- "id": string – egyedi azonosító, formátum: "{id_prefix}_<feladat_szam>_<betű>"
  például "{id_prefix}_1_a", "{id_prefix}_2_b"
- "kerdes": string – a részfeladat kérdése, teljes mondatban (max 3 mondat);
  Markdown formázás megengedett; matematikai kifejezéseket LaTeX jelöléssel ($...$)
- "helyes_valasz": string – a helyes válasz, rövid, tömör (max 1-2 mondat);
  matematikai kifejezéseket LaTeX jelöléssel
- "hint": string – egy segítő tipp a megoldáshoz (max 1 mondat)
- "magyarazat": string – rövid magyarázat miért helyes (max 2 mondat);
  Markdown + LaTeX math megengedett
- "neh": int – nehézség 1–3 ({neh_scale})
- "szint": "{szint}"
- "kontextus": string | null – ha a feladat egy közös bevezető szövegre, ábrára vagy
  táblázatra hivatkozik, ide másold be a teljes közös szöveget; egyébként null;
  Markdown + LaTeX math formázás megengedett
- "abra_van": bool – true ha a feladat szövege ábrára, grafikonra vagy rajzra hivatkozik
- "feladat_oldal": int | null – a feladatblokk fejlécéből olvasd ki: „## [Feladat N |
  Oldal X, sor Y]" → X az oldalszám; ha ilyen fejléc nincs, becsüld a [Oldal N]
  markerek alapján; ha egyáltalán nem azonosítható, null
- "feladat_tipus": string | null – a feladat típusa az alábbiak egyike:
  "nyilt_valasz" (szabad szöveges válasz),
  "tobbvalasztos" (felkínált opciókból kell választani),
  "parositas" (elemeket kell összepárosítani),
  "igaz_hamis" (igaz/hamis döntés),
  "fogalmazas" (hosszabb írásbeli szöveg),
  "kitoltes" (hiányos szöveg kiegészítése);
  ha nem egyértelmű, null
- "elfogadott_valaszok": list[string] | null – az útmutatóból kiolvasott összes
  elfogadható helyes válasz listában (pl. ["0,6", "3/5", "0.6"]); ha csak egy van,
  akkor is listában; null ha nem ismert
- "valaszlehetosegek": list[string] | null – a feladatlapban felkínált válaszlehetőségek
  listában (többválasztós és párosítás feladatoknál); egyébként null
- "max_pont": int – az útmutatóból kiolvasott maximális pontszám erre a részfeladatra
  (alapértelmezett: 1)
- "reszpontozas": string | null – részpontozási szabály szövegesen, ha az útmutató
  megad ilyet (pl. "6/6=3p, 5/6=2p, 3-4/6=1p"); egyébként null
- "ertekeles_megjegyzes": string | null – fontos javítói megjegyzések, kivételek,
  elfogadási feltételek (pl. "Csak akkor adható pont, ha..."); egyébként null

A szöveg magyar; hagyj minden szaktermint, nevet, számot magyarul.
Ne generálj feladatot, ha a szövegből nem olvasható ki egyértelműen a helyes válasz.
"""


def _make_openai_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


def extract_feladatok(
    fl_text: str,
    ut_text: str,
    targy: str,
    pdf_source: str,
    ut_source: str = "",
    *,
    model: str | None = None,
) -> list[Feladat]:
    """Call GPT with the feladatlap + útmutató texts.

    Returns a list of *validated* Feladat objects.  Items that fail validation
    are logged and skipped (never raise to the caller).
    """
    client = _make_openai_client()
    model = model or os.getenv("LLM_MODEL", "gpt-4o")

    meta = parse_filename_meta(pdf_source)
    id_prefix = _id_prefix_from_source(pdf_source, targy)

    szint = meta.get("szint") or "(ismeretlen szint)"
    prompt = _USER_TEMPLATE.format(
        targy=targy,
        pdf_source=pdf_source,
        ut_source=ut_source or "(ismeretlen)",
        ev=meta["ev"] or "(ismeretlen)",
        valtozat=meta["valtozat"] or "(ismeretlen)",
        fl_text=fl_text[:12_000],   # keep within token budget
        ut_text=ut_text[:6_000],
        id_prefix=id_prefix,
        neh_scale=_NEH_SCALE,
        szint=szint,
    )

    logger.info("Calling GPT for %s …", pdf_source)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    raw = json.loads(response.choices[0].message.content or "{}")
    items: list[dict] = raw.get("feladatok", [])

    feladatok: list[Feladat] = []
    for item in items:
        try:
            item["targy"] = targy
            item.setdefault("ev", meta["ev"])
            item.setdefault("valtozat", meta["valtozat"])
            feladatok.append(_dict_to_feladat(item))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Skipping invalid item %s: %s", item.get("id"), exc)

    logger.info("Extracted %d feladatok from %s", len(feladatok), pdf_source)
    return feladatok


def extract_feladatok_batched(
    matched_blocks: list[tuple[TaskBlock, TaskBlock | None]],
    targy: str,
    pdf_source: str,
    ut_source: str = "",
    *,
    model: str | None = None,
    batch_size: int = 4,
) -> list[Feladat]:
    """Extract Feladat objects from pre-split task blocks via batched GPT calls.

    Sends *batch_size* (fl, ut) block pairs per GPT call, each annotated with
    page/line metadata so the model can fill ``feladat_oldal`` reliably and
    never loses task content due to hard char-count truncation.

    Args:
        matched_blocks: paired (fl_block, ut_block | None) list from
            :func:`match_fl_ut_blocks`.
        targy: subject (\"matek\" or \"magyar\").
        pdf_source: fl PDF filename (used for id prefix and metadata).
        ut_source: ut PDF filename (informational only).
        model: override GPT model name.
        batch_size: task pairs per GPT call (default 4).

    Returns:
        Flat list of validated :class:`Feladat` objects.
    """
    if not matched_blocks:
        return []

    client = _make_openai_client()
    model = model or os.getenv("LLM_MODEL", "gpt-4o")

    meta = parse_filename_meta(pdf_source)
    id_prefix = _id_prefix_from_source(pdf_source, targy)
    szint = meta.get("szint") or "(ismeretlen szint)"

    all_feladatok: list[Feladat] = []

    for batch_start in range(0, len(matched_blocks), batch_size):
        batch = matched_blocks[batch_start : batch_start + batch_size]
        batch_nums = [fl_b.sorszam for fl_b, _ in batch]

        fl_text = "\n\n".join(annotate_block(fl_b) for fl_b, _ in batch)
        ut_text = "\n\n".join(
            annotate_block(ut_b)
            if ut_b is not None
            else (
                f"## [Feladat {fl_b.sorszam} | útmutató nincs]\n"
                "(nincs útmutató ehhez a feladathoz)"
            )
            for fl_b, ut_b in batch
        )

        prompt = _USER_TEMPLATE.format(
            targy=targy,
            pdf_source=pdf_source,
            ut_source=ut_source or "(ismeretlen)",
            ev=meta["ev"] or "(ismeretlen)",
            valtozat=meta["valtozat"] or "(ismeretlen)",
            fl_text=fl_text,
            ut_text=ut_text,
            id_prefix=id_prefix,
            neh_scale=_NEH_SCALE,
            szint=szint,
        )

        logger.info("Calling GPT for %s, feladatok %s …", pdf_source, batch_nums)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            raw = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:
            logger.error("GPT call failed for batch %s: %s", batch_nums, exc)
            continue

        items: list[dict] = raw.get("feladatok", [])
        for item in items:
            try:
                item["targy"] = targy
                item.setdefault("ev", meta["ev"])
                item.setdefault("valtozat", meta["valtozat"])
                all_feladatok.append(_dict_to_feladat(item))
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Skipping invalid item %s: %s", item.get("id"), exc)

    n_calls = -(-len(matched_blocks) // batch_size)  # ceiling division
    logger.info(
        "Extracted %d feladatok from %s (batched, %d GPT call(s))",
        len(all_feladatok), pdf_source, n_calls,
    )
    return all_feladatok


def _id_prefix_from_source(pdf_source: str, targy: str) -> str:
    """'M8_2025_1_fl.pdf' → 'mat4_2025_1' / 'mat8_2025_1'."""
    meta = parse_filename_meta(pdf_source)
    year = str(meta["ev"]) if meta["ev"] else "xx"
    seq = str(meta["valtozat"]) if meta["valtozat"] else "1"
    szint = meta.get("szint") or ""
    gym_num = szint.split()[0] if szint else ""   # "4", "6", "8" or ""
    short = "mat" if targy == "matek" else "mag"
    return f"{short}{gym_num}_{year}_{seq}"


def parse_filename_meta(filename: str) -> dict:
    """Extract structured metadata from a felvételi PDF filename.

    'M8_2025_1_fl.pdf' → {'ev': 2025, 'valtozat': 1, 'kind': 'fl', 'targy': 'matek', 'szint': '8 osztályos'}
    'A4_2025_2_ut.pdf' → {'ev': 2025, 'valtozat': 2, 'kind': 'ut', 'targy': 'magyar', 'szint': '4 osztályos'}
    Returns None values for any field that cannot be parsed.
    """
    m = re.match(
        r"^([AM])(\d+)_(\d{4})_(\d+)_(fl|ut)\.pdf$",
        Path(filename).name,
        re.IGNORECASE,
    )
    if not m:
        return {"ev": None, "valtozat": None, "kind": None, "targy": None, "szint": None}
    prefix, gym_num, year, seq, kind = m.groups()
    return {
        "ev": int(year),
        "valtozat": int(seq),
        "kind": kind.lower(),
        "targy": _TARGY_MAP.get(prefix.upper()),
        "szint": _SZINT_MAP.get(int(gym_num)),
    }


def _dict_to_feladat(d: dict) -> Feladat:
    """Convert a raw GPT dict to a Feladat, raising on missing required fields."""
    required = {"id", "kerdes", "helyes_valasz", "hint", "magyarazat", "neh", "szint"}
    missing = required - d.keys()
    if missing:
        raise KeyError(f"Missing fields: {missing}")
    neh = int(d["neh"])
    if neh not in (1, 2, 3):
        raise ValueError(f"neh must be 1-3, got {neh!r}")
    ev_raw = d.get("ev")
    val_raw = d.get("valtozat")
    # Derive feladat_sorszam from the id if GPT didn't return it explicitly
    # id format: {prefix}_{year}_{variant}_{num}_{letter}  e.g. mat_2025_1_3_b → "3b"
    raw_sorszam = d.get("feladat_sorszam")
    if not raw_sorszam:
        id_parts = str(d["id"]).split("_")
        if len(id_parts) >= 5:
            raw_sorszam = "".join(id_parts[-2:])   # e.g. "3" + "b" → "3b"
        elif len(id_parts) == 4:
            raw_sorszam = id_parts[-1]              # just the number
    return Feladat(
        id=str(d["id"]),
        neh=neh,
        szint=str(d["szint"]),
        kerdes=str(d["kerdes"]),
        helyes_valasz=str(d["helyes_valasz"]),
        hint=str(d["hint"]),
        magyarazat=str(d["magyarazat"]),
        targy=str(d.get("targy", "")),
        ev=int(ev_raw) if ev_raw is not None else None,
        valtozat=int(val_raw) if val_raw is not None else None,
        feladat_sorszam=str(raw_sorszam) if raw_sorszam else None,
        feladat_tipus=str(d["feladat_tipus"]) if d.get("feladat_tipus") else None,
        elfogadott_valaszok=_parse_str_list(d.get("elfogadott_valaszok")),
        valaszlehetosegek=_parse_str_list(d.get("valaszlehetosegek")),
        max_pont=int(d["max_pont"]) if d.get("max_pont") is not None else 1,
        reszpontozas=str(d["reszpontozas"]) if d.get("reszpontozas") else None,
        ertekeles_megjegyzes=str(d["ertekeles_megjegyzes"]) if d.get("ertekeles_megjegyzes") else None,
        kontextus=str(d["kontextus"]) if d.get("kontextus") else None,
        abra_van=bool(d.get("abra_van", False)),
        feladat_oldal=int(d["feladat_oldal"]) if d.get("feladat_oldal") else None,
    )


# ---------------------------------------------------------------------------
# Post-processing: grouping
# ---------------------------------------------------------------------------

import re as _re


def _group_feladatok(
    feladatok: list[Feladat],
    pdf_source: str,
    ut_source: str = "",
) -> tuple[list[Feladat], list[FeladatCsoport]]:
    """Group flat Feladat list into FeladatCsoport records.

    Grouping key: the numeric prefix of feladat_sorszam
    (e.g. "3a", "3b", "3c" all belong to group "3").
    Tasks without a sorszam are placed in singleton groups.

    Returns:
        (updated_feladatok, csoportok)
        where updated_feladatok have csoport_id and csoport_sorrend set.
    """
    meta = parse_filename_meta(pdf_source)

    # Group by numeric prefix of feladat_sorszam
    from collections import defaultdict
    groups: dict[str, list[tuple[int, Feladat]]] = defaultdict(list)
    for i, f in enumerate(feladatok):
        sorszam = f.feladat_sorszam or str(i)
        num_match = _re.match(r"^(\d+)", sorszam)
        group_key = num_match.group(1) if num_match else sorszam
        groups[group_key].append((i, f))

    updated_feladatok: list[Feladat] = [None] * len(feladatok)  # type: ignore[list-item]
    csoportok: list[FeladatCsoport] = []
    id_prefix = _id_prefix_from_source(pdf_source, feladatok[0].targy if feladatok else "")

    for group_key, members in groups.items():
        csoport_id = f"{id_prefix}_{group_key}"
        # Derive shared fields from the first member (or first with non-null kontextus)
        first_f = members[0][1]
        shared_kontextus = next(
            (f.kontextus for _, f in members if f.kontextus), None
        )
        shared_abra = any(f.abra_van for _, f in members)
        shared_oldal = first_f.feladat_oldal
        max_pont_ossz = sum(f.max_pont for _, f in members)

        csoport = FeladatCsoport(
            id=csoport_id,
            targy=first_f.targy,
            szint=first_f.szint,
            feladat_sorszam=group_key,
            ev=meta.get("ev"),
            valtozat=meta.get("valtozat"),
            kontextus=shared_kontextus,
            abra_van=shared_abra,
            feladat_oldal=shared_oldal,
            fl_pdf_path=first_f.fl_pdf_path,
            ut_pdf_path=first_f.ut_pdf_path,
            fl_szoveg_path=first_f.fl_szoveg_path,
            ut_szoveg_path=first_f.ut_szoveg_path,
            sorrend_kotelezo=False,
            max_pont_ossz=max_pont_ossz,
        )
        csoportok.append(csoport)

        for sorrend, (orig_idx, f) in enumerate(members, start=1):
            updated_feladatok[orig_idx] = dataclasses.replace(
                f,
                csoport_id=csoport_id,
                csoport_sorrend=sorrend,
            )

    return updated_feladatok, csoportok


# ---------------------------------------------------------------------------
# Pair discovery
# ---------------------------------------------------------------------------


def find_exam_pairs(exams_dir: Path | None = None) -> Iterator[tuple[Path, Path, str]]:
    """Yield (fl_path, ut_path, targy) for every matched feladatlap+útmutató pair."""
    if exams_dir is None:
        exams_dir = get_exams_dir()
    pattern = re.compile(r"^([AM])(\d+)_\d{4}_\d+_fl\.pdf$", re.IGNORECASE)

    for fl_path in sorted(exams_dir.rglob("*_fl.pdf")):
        m = pattern.match(fl_path.name)
        if not m:
            continue
        prefix_letter = m.group(1).upper()
        gym_num = int(m.group(2))
        targy = _TARGY_MAP.get(prefix_letter)
        if targy is None:
            continue
        if gym_num not in _SZINT_MAP:
            logger.warning("Ismeretlen évfolyamszám (%s) – kihagyva: %s", gym_num, fl_path.name)
            continue

        ut_name = fl_path.name.replace("_fl.pdf", "_ut.pdf")
        ut_path = fl_path.with_name(ut_name)
        if not ut_path.exists():
            logger.warning("Útmutató not found for %s – skipping pair", fl_path.name)
            continue

        yield fl_path, ut_path, targy


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------


def parse_exam(
    fl_path: Path,
    ut_path: Path,
    targy: str,
    *,
    model: str | None = None,
) -> tuple[list[Feladat], list[FeladatCsoport]]:
    """Full pipeline for one exam pair: pdf→text→blocks→GPT→Feladat + FeladatCsoport.

    Uses regexp-based task block splitting and batched GPT calls when task
    structure is detected in the extracted text.  Falls back to a single GPT
    call (:func:`extract_feladatok`) when no task headers are found.
    """
    fl_text = pdf_to_text(fl_path)
    ut_text = pdf_to_text(ut_path)

    # Persist extracted text for later inspection
    fl_rel = _save_text_cache(fl_text, fl_path.stem)
    ut_rel = _save_text_cache(ut_text, ut_path.stem)

    # Split into per-task blocks; fall back to single-batch if none found
    fl_blocks = split_into_task_blocks(fl_text)
    ut_blocks = split_into_task_blocks(ut_text)

    if fl_blocks:
        matched = match_fl_ut_blocks(fl_blocks, ut_blocks)
        feladatok = extract_feladatok_batched(
            matched, targy,
            pdf_source=fl_path.name,
            ut_source=ut_path.name,
            model=model,
        )
    else:
        logger.warning(
            "No task blocks found in %s – falling back to single-batch extraction",
            fl_path.name,
        )
        feladatok = extract_feladatok(
            fl_text, ut_text, targy,
            pdf_source=fl_path.name,
            ut_source=ut_path.name,
            model=model,
        )
    # Attach text-cache paths and PDF paths to every extracted feladat
    try:
        fl_pdf_rel = str(fl_path.relative_to(get_exams_dir()))
    except ValueError:
        fl_pdf_rel = None

    try:
        ut_pdf_rel = str(ut_path.relative_to(get_exams_dir()))
    except ValueError:
        ut_pdf_rel = None

    feladatok = [
        dataclasses.replace(
            f,
            fl_szoveg_path=fl_rel,
            ut_szoveg_path=ut_rel,
            fl_pdf_path=fl_pdf_rel,
            ut_pdf_path=ut_pdf_rel,
        )
        for f in feladatok
    ]

    if not feladatok:
        return [], []

    feladatok, csoportok = _group_feladatok(feladatok, fl_path.name, ut_path.name)
    return feladatok, csoportok


def _save_text_cache(text: str, pdf_stem: str) -> str:
    """Write extracted plain text to the assets text cache, return relative path."""
    path = text_cache_path(pdf_stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative_text_path(pdf_stem)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(
    year: int | None = None,
    targy: str | None = None,
    szint: str | None = None,
    dry_run: bool = False,
    review: bool = False,
    model: str | None = None,
    exams_dir: Path | None = None,
    limit: int = 0,
) -> None:
    """Feldolgozza a PDF párokat és elmenti a feladatokat a DB-be."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    repo = FeladatRepository(db_path=get_db_path()) if not dry_run else None

    # Build set of already-processed pdf_sources to skip
    already_done: set[str] = set()
    if repo:
        for f in repo.all():
            if f.pdf_source:
                already_done.add(f.pdf_source)

    ed = exams_dir or get_exams_dir()
    pairs = list(find_exam_pairs(ed))

    # Apply filters
    if year:
        pairs = [(fl, ut, t) for fl, ut, t in pairs if str(year) in fl.name]
    if targy:
        pairs = [(fl, ut, t) for fl, ut, t in pairs if t == targy]
    if szint:
        szint_filter = _CLI_SZINT_MAP[szint]
        pairs = [(fl, ut, t) for fl, ut, t in pairs
                 if parse_filename_meta(fl.name).get("szint") == szint_filter]

    # Skip already processed
    pairs = [(fl, ut, t) for fl, ut, t in pairs if fl.name not in already_done]

    if limit:
        pairs = pairs[:limit]

    if not pairs:
        print("Nincs feldolgozandó PDF pár.")
        return

    print(f"Feldolgozandó PDF párok: {len(pairs)}")
    total_saved = 0

    for fl_path, ut_path, targy_val in pairs:
        print(f"\n{'─'*60}")
        print(f"  Feladatlap : {fl_path}")
        print(f"  Útmutató   : {ut_path}")
        print(f"  Tantárgy   : {targy_val}")
        print(f"{'─'*60}")

        try:
            feladatok, csoportok = parse_exam(fl_path, ut_path, targy_val, model=model)
        except Exception as exc:
            logger.error("Extraction failed for %s: %s", fl_path.name, exc)
            continue

        if not feladatok:
            print("  Nem sikerült feladatot kinyerni.")
            continue

        print(f"  Extrahált feladatok: {len(feladatok)}, csoportok: {len(csoportok)}")  # type: ignore[possibly-undefined]

        if review:
            # review_feladatok now only reviews Feladatok; csoportok regenerated after
            feladatok = review_feladatok(feladatok)
            if feladatok:
                feladatok, csoportok = _group_feladatok(feladatok, fl_path.name, ut_path.name)

        if not feladatok:
            continue

        if repo:
            repo.upsert_many_csoportok(csoportok)  # type: ignore[possibly-undefined]
            repo.upsert_many(feladatok)
            print(f"  Mentve: {len(feladatok)} feladat, {len(csoportok)} csoport → DB")  # type: ignore[possibly-undefined]
        else:
            print(f"  [dry-run] Mentett volna: {len(feladatok)} feladat, {len(csoportok)} csoport")  # type: ignore[possibly-undefined]
            for csoport in csoportok:  # type: ignore[possibly-undefined]
                csoport_feladatok = [f for f in feladatok if f.csoport_id == csoport.id]
                print_csoport(csoport, csoport_feladatok)

        total_saved += len(feladatok)

    print(f"\nKész. Összesen mentett feladatok: {total_saved}")


if __name__ == "__main__":
    from felvi_games.cli import app
    app(["parse"], standalone_mode=True)
