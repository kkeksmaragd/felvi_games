"""Feladat extractor: PDF → text → GPT → Feladat objects → DB.

Pipeline
--------
1. pdf_to_text()          – pdftotext → one string per PDF
2. extract_feladatok()    – GPT parses text pair (feladatlap + útmutató)
                            and returns a list of Feladat objects
3. review_feladatok()     – interactive CLI review (accept / edit / skip)
4. CLI main()             – glues everything together, upserts into DB

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

import pdftotext
from dotenv import load_dotenv
from openai import OpenAI

from felvi_games.db import FeladatRepository
from felvi_games.models import Feladat

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXAMS_DIR = Path(__file__).parent.parent.parent / "exams"
_DB_PATH = Path(__file__).parent.parent.parent / "data" / "felvi.db"

# Filename prefix → subject name
_TARGY_MAP: dict[str, str] = {"A": "magyar", "M": "matek"}

# Difficulty descriptions passed to GPT
_NEH_SCALE = (
    "1 = könnyű (alapszintű számolás / szóértés, egyértelmű válasz), "
    "2 = közepes (több lépés / következtetés kell), "
    "3 = nehéz (komplex, ritka tudás vagy kreativitás kell)"
)

# ---------------------------------------------------------------------------
# Step 1 – PDF → text
# ---------------------------------------------------------------------------


def pdf_to_text(path: Path) -> str:
    """Extract all pages from *path* and return them joined as one string."""
    with open(path, "rb") as fh:
        pages = list(pdftotext.PDF(fh))
    return "\n\n".join(pages)


# ---------------------------------------------------------------------------
# Step 2 – text pair → Feladat list (via GPT)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Felvételi feladatsor elemző vagy. Feladatlapot és javítási útmutatót kapod.
Minden egyes részfeladatból (pl. 1a, 1b, 2a…) ONE JSON objektumot generálj.
Komplex számítási/rajz/táblázat feladatokat egyszerűsítsd: a kérdést úgy fogalmazd,
hogy szövegesen (egy válasszal) megválaszolható legyen, és add meg a helyes választ is.
"""

_USER_TEMPLATE = """\
## Sorozat adatok
- Tantárgy: {targy}
- Forrás PDF: {pdf_source}
- Évfolyam: 9 osztályos

## Feladatlap szövege
{fl_text}

## Javítási útmutató szövege
{ut_text}

---

Generálj egy JSON objektumot, amely egyetlen "feladatok" kulcsot tartalmaz.
Az érték egy lista; minden elem tartalmazza:
- "id": string – egyedi azonosító, formátum: "{id_prefix}_<feladat_szam>_<betű>"
  például "{id_prefix}_1_a", "{id_prefix}_2_b"
- "kerdes": string – a részfeladat kérdése, teljes mondatban (max 3 mondat)
- "helyes_valasz": string – a helyes válasz, rövid, tömör (max 1-2 mondat)
- "hint": string – egy segítő tipp a megoldáshoz (max 1 mondat)
- "magyarazat": string – rövid magyarázat miért helyes (max 2 mondat)
- "neh": int – nehézség 1–3 ({neh_scale})
- "szint": "9 osztályos"

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

    raw = json.loads(response.choices[0].message.content)
    items: list[dict] = raw.get("feladatok", [])

    feladatok: list[Feladat] = []
    for item in items:
        try:
            item["targy"] = targy
            item["pdf_source"] = pdf_source
            item["ut_source"] = ut_source
            item.setdefault("ev", meta["ev"])
            item.setdefault("valtozat", meta["valtozat"])
            feladatok.append(_dict_to_feladat(item))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Skipping invalid item %s: %s", item.get("id"), exc)

    logger.info("Extracted %d feladatok from %s", len(feladatok), pdf_source)
    return feladatok


def _id_prefix_from_source(pdf_source: str, targy: str) -> str:
    """'M8_2025_1_fl.pdf' → 'mat_2025_1'."""
    meta = parse_filename_meta(pdf_source)
    year = str(meta["ev"]) if meta["ev"] else "xx"
    seq = str(meta["valtozat"]) if meta["valtozat"] else "1"
    short = "mat" if targy == "matek" else "mag"
    return f"{short}_{year}_{seq}"


def parse_filename_meta(filename: str) -> dict:
    """Extract structured metadata from a felvételi PDF filename.

    'M8_2025_1_fl.pdf' → {'ev': 2025, 'valtozat': 1, 'kind': 'fl', 'targy': 'matek'}
    'A8_2024_2_ut.pdf' → {'ev': 2024, 'valtozat': 2, 'kind': 'ut', 'targy': 'magyar'}
    Returns None values for any field that cannot be parsed.
    """
    m = re.match(
        r"^([AM])8_(\d{4})_(\d+)_(fl|ut)\.pdf$",
        Path(filename).name,
        re.IGNORECASE,
    )
    if not m:
        return {"ev": None, "valtozat": None, "kind": None, "targy": None}
    prefix, year, seq, kind = m.groups()
    return {
        "ev": int(year),
        "valtozat": int(seq),
        "kind": kind.lower(),
        "targy": _TARGY_MAP.get(prefix.upper()),
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
    return Feladat(
        id=str(d["id"]),
        neh=neh,
        szint=str(d["szint"]),
        kerdes=str(d["kerdes"]),
        helyes_valasz=str(d["helyes_valasz"]),
        hint=str(d["hint"]),
        magyarazat=str(d["magyarazat"]),
        targy=str(d.get("targy", "")),
        pdf_source=str(d.get("pdf_source", "")) or None,
        ut_source=str(d.get("ut_source", "")) or None,
        ev=int(ev_raw) if ev_raw is not None else None,
        valtozat=int(val_raw) if val_raw is not None else None,
        feladat_sorszam=str(d["feladat_sorszam"]) if d.get("feladat_sorszam") else None,
    )


# ---------------------------------------------------------------------------
# Step 3 – interactive review
# ---------------------------------------------------------------------------

_REVIEW_HELP = """
Commands:
  [Enter] / a  – accept as-is
  e            – edit field(s) interactively
  s            – skip (discard this feladat)
  q            – quit review (keep accepted so far)
"""


def review_feladatok(feladatok: list[Feladat]) -> list[Feladat]:
    """Interactive CLI review.  Returns only the accepted (possibly edited) feladatok."""
    if not feladatok:
        print("Nincs extrahált feladat.")
        return []

    print(_REVIEW_HELP)
    accepted: list[Feladat] = []

    for i, f in enumerate(feladatok, 1):
        print(f"\n{'='*60}")
        print(f"  [{i}/{len(feladatok)}]  {f.id}  |  {f.targy}  |  neh={f.neh}  |  {f.szint}")
        print(f"{'='*60}")
        _print_feladat(f)

        while True:
            cmd = input("\n  > Accept / Edit / Skip / Quit [a/e/s/q]: ").strip().lower()
            if cmd in ("", "a"):
                accepted.append(f)
                print("  ✓ Elfogadva.")
                break
            elif cmd == "e":
                f = _edit_feladat(f)
                _print_feladat(f)
            elif cmd == "s":
                print("  – Kihagyva.")
                break
            elif cmd == "q":
                print(f"\nReview leállítva. Elfogadva: {len(accepted)} feladat.")
                return accepted
            else:
                print("  Érvénytelen parancs. Használj: a / e / s / q")

    print(f"\nReview kész. Elfogadva: {len(accepted)}/{len(feladatok)} feladat.")
    return accepted


def _print_feladat(f: Feladat) -> None:
    fields = [
        ("id", f.id),
        ("feladat_sorszam", f.feladat_sorszam or "-"),
        ("ev / valtozat", f"{f.ev or '-'} / {f.valtozat or '-'}"),
        ("pdf_source", f.pdf_source or "-"),
        ("ut_source", f.ut_source or "-"),
        ("kerdes", f.kerdes),
        ("helyes_valasz", f.helyes_valasz),
        ("hint", f.hint),
        ("magyarazat", f.magyarazat),
        ("neh", str(f.neh)),
        ("szint", f.szint),
    ]
    for name, value in fields:
        print(f"  {name:15s}: {value}")


def _edit_feladat(f: Feladat) -> Feladat:
    """Prompt the user to edit individual fields.  Returns new (frozen) Feladat."""
    editable = ["kerdes", "helyes_valasz", "hint", "magyarazat", "neh", "szint"]
    print(f"\n  Szerkeszthető mezők: {', '.join(editable)}")
    print("  (Üres Enter = mező megtartása)")

    updates: dict = {}
    for field in editable:
        current = getattr(f, field)
        val = input(f"  {field} [{current}]: ").strip()
        if val:
            updates[field] = val

    if not updates:
        return f

    # Build a new Feladat with updated fields
    d = {
        "id": f.id, "neh": updates.get("neh", f.neh),
        "szint": updates.get("szint", f.szint),
        "kerdes": updates.get("kerdes", f.kerdes),
        "helyes_valasz": updates.get("helyes_valasz", f.helyes_valasz),
        "hint": updates.get("hint", f.hint),
        "magyarazat": updates.get("magyarazat", f.magyarazat),
        "targy": f.targy, "pdf_source": f.pdf_source,
        "ut_source": f.ut_source,
        "ev": f.ev, "valtozat": f.valtozat,
        "feladat_sorszam": f.feladat_sorszam,
    }
    try:
        return _dict_to_feladat(d)
    except (KeyError, ValueError) as exc:
        print(f"  Szerkesztés sikertelen: {exc}. Eredeti megtartva.")
        return f


# ---------------------------------------------------------------------------
# Pair discovery
# ---------------------------------------------------------------------------


def find_exam_pairs(exams_dir: Path = _EXAMS_DIR) -> Iterator[tuple[Path, Path, str]]:
    """Yield (fl_path, ut_path, targy) for every matched feladatlap+útmutató pair."""
    pattern = re.compile(r"^([AM])8_\d{4}_\d+_fl\.pdf$", re.IGNORECASE)

    for fl_path in sorted(exams_dir.rglob("*_fl.pdf")):
        m = pattern.match(fl_path.name)
        if not m:
            continue
        prefix_letter = m.group(1).upper()
        targy = _TARGY_MAP.get(prefix_letter)
        if targy is None:
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
) -> list[Feladat]:
    """Full pipeline for one exam pair: pdf→text→GPT→Feladat list (no review, no DB)."""
    fl_text = pdf_to_text(fl_path)
    ut_text = pdf_to_text(ut_path)
    return extract_feladatok(
        fl_text, ut_text, targy,
        pdf_source=fl_path.name,
        ut_source=ut_path.name,
        model=model,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:  # noqa: C901
    """CLI entry point: felvi-parse

    Usage:
      felvi-parse                        # process all unprocessed pairs
      felvi-parse --year 2025            # only exams from 2025
      felvi-parse --targy matek          # only one subject
      felvi-parse --dry-run              # extract + review, but do NOT save to DB
      felvi-parse --no-review            # skip interactive review (accept all)
      felvi-parse --model gpt-4o-mini    # override LLM model
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="felvi-parse",
        description="Extract felvételi feladatok from PDF pairs into the DB.",
    )
    parser.add_argument("--year", type=int, help="Only process exams from this year")
    parser.add_argument("--targy", choices=["matek", "magyar"], help="Subject filter")
    parser.add_argument("--dry-run", action="store_true", help="Do not save to DB")
    parser.add_argument("--no-review", action="store_true", help="Accept all without review")
    parser.add_argument("--model", default=None, help="Override LLM model name")
    parser.add_argument("--exams-dir", default=str(_EXAMS_DIR), help="Path to exams folder")
    parser.add_argument("--limit", type=int, default=0, help="Max exam pairs to process")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    repo = FeladatRepository(db_path=_DB_PATH) if not args.dry_run else None

    # Build set of already-processed pdf_sources to skip
    already_done: set[str] = set()
    if repo:
        for f in repo.all():
            if f.pdf_source:
                already_done.add(f.pdf_source)

    exams_dir = Path(args.exams_dir)
    pairs = list(find_exam_pairs(exams_dir))

    # Apply filters
    if args.year:
        pairs = [(fl, ut, t) for fl, ut, t in pairs if str(args.year) in fl.parts]
    if args.targy:
        pairs = [(fl, ut, t) for fl, ut, t in pairs if t == args.targy]

    # Skip already processed
    pairs = [(fl, ut, t) for fl, ut, t in pairs if fl.name not in already_done]

    if args.limit:
        pairs = pairs[: args.limit]

    if not pairs:
        print("Nincs feldolgozandó PDF pár.")
        return

    print(f"Feldolgozandó PDF párok: {len(pairs)}")
    total_saved = 0

    for fl_path, ut_path, targy in pairs:
        print(f"\n{'─'*60}")
        print(f"  Feladatlap : {fl_path}")
        print(f"  Útmutató   : {ut_path}")
        print(f"  Tantárgy   : {targy}")
        print(f"{'─'*60}")

        try:
            feladatok = parse_exam(fl_path, ut_path, targy, model=args.model)
        except Exception as exc:
            logger.error("Extraction failed for %s: %s", fl_path.name, exc)
            continue

        if not feladatok:
            print("  Nem sikerült feladatot kinyerni.")
            continue

        print(f"  Extrahált feladatok: {len(feladatok)}")

        if not args.no_review:
            feladatok = review_feladatok(feladatok)

        if not feladatok:
            continue

        if repo:
            repo.upsert_many(feladatok)
            print(f"  Mentve: {len(feladatok)} feladat → DB")
        else:
            print(f"  [dry-run] Mentett volna: {len(feladatok)} feladat")
            for f in feladatok:
                _print_feladat(f)

        total_saved += len(feladatok)

    print(f"\nKész. Összesen mentett feladatok: {total_saved}")


if __name__ == "__main__":
    main()
