"""review.py – Feladat quality-review tooling.

Provides:
  - review_feladatok()      interactive CLI review loop
  - review_feladat_ai()     GPT-assisted single-feladat review
  - print_feladat()         pretty-print a Feladat to stdout
  - edit_feladat_cli()      interactive field editor (CLI)
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os

from felvi_games.models import Feladat, FeladatCsoport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CLI review
# ---------------------------------------------------------------------------

_REVIEW_HELP = """
Parancsok:
  [Enter] / a  – elfogad
  e            – mező szerkesztése
  s            – kihagyás (nem menti)
  q            – leállítás (az eddig elfogadottak megmaradnak)
"""


def review_feladatok(feladatok: list[Feladat]) -> list[Feladat]:
    """Interactive CLI review.  Returns only the accepted (possibly edited) feladatok."""
    if not feladatok:
        print("Nincs extrahált feladat.")
        return []

    print(_REVIEW_HELP)
    accepted: list[Feladat] = []

    for i, f in enumerate(feladatok, 1):
        print(f"\n{'=' * 60}")
        print(f"  [{i}/{len(feladatok)}]  {f.id}  |  {f.targy}  |  neh={f.neh}  |  {f.szint}")
        print(f"{'=' * 60}")
        print_feladat(f)

        while True:
            cmd = input("\n  > Accept / Edit / Skip / Quit [a/e/s/q]: ").strip().lower()
            if cmd in ("", "a"):
                accepted.append(dataclasses.replace(f, review_elvegezve=True))
                print("  ✓ Elfogadva.")
                break
            elif cmd == "e":
                f = edit_feladat_cli(f)
                print_feladat(f)
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


def print_feladat(f: Feladat) -> None:
    """Print all human-readable fields of a Feladat to stdout."""
    fields = [
        ("id", f.id),
        ("feladat_sorszam", f.feladat_sorszam or "-"),
        ("ev / valtozat", f"{f.ev or '-'} / {f.valtozat or '-'}"),
        ("pdf_source", f.pdf_source or "-"),
        ("ut_source", f.ut_source or "-"),
        ("abra_van", str(f.abra_van)),
        ("feladat_oldal", str(f.feladat_oldal) if f.feladat_oldal else "-"),
        ("feladat_tipus", f.feladat_tipus or "-"),
        ("max_pont", str(f.max_pont)),
        ("elfogadott_valaszok", ", ".join(f.elfogadott_valaszok) if f.elfogadott_valaszok else "-"),
        ("valaszlehetosegek", ", ".join(f.valaszlehetosegek) if f.valaszlehetosegek else "-"),
        ("reszpontozas", f.reszpontozas or "-"),
        ("ertekeles_megjegyzes", f.ertekeles_megjegyzes or "-"),
        ("csoport_id", f.csoport_id or "-"),
        ("csoport_sorrend", str(f.csoport_sorrend) if f.csoport_sorrend is not None else "-"),
        ("review", "✓" if f.review_elvegezve else "–"),
        (
            "kontextus",
            (f.kontextus[:120] + "…")
            if f.kontextus and len(f.kontextus) > 120
            else (f.kontextus or "-"),
        ),
        ("kerdes", f.kerdes),
        ("helyes_valasz", f.helyes_valasz),
        ("hint", f.hint),
        ("magyarazat", f.magyarazat),
        ("neh", str(f.neh)),
        ("szint", f.szint),
    ]
    for name, value in fields:
        print(f"  {name:22s}: {value}")


def print_csoport(csoport: FeladatCsoport, feladatok: list[Feladat]) -> None:
    """Print a FeladatCsoport and all its member Feladatok."""
    print(f"\n{'#' * 70}")
    print(f"  CSOPORT  {csoport.id}  |  {csoport.targy}  |  {csoport.szint}")
    print(f"  Feladat sorszám: {csoport.feladat_sorszam}  |  Ev: {csoport.ev or '-'}  |  Változat: {csoport.valtozat or '-'}")
    if csoport.kontextus:
        preview = csoport.kontextus[:120] + ("…" if len(csoport.kontextus) > 120 else "")
        print(f"  Kontextus: {preview}")
    print(f"  Max pont összesen: {csoport.max_pont_ossz}  |  Sorrend kötelező: {csoport.sorrend_kotelezo}")
    print(f"{'#' * 70}")
    for f in feladatok:
        print(f"\n  --- {f.feladat_sorszam or f.id} (sorrend={f.csoport_sorrend}) ---")
        print_feladat(f)


def edit_feladat_cli(f: Feladat) -> Feladat:
    """Prompt the user to edit individual fields. Returns a new (frozen) Feladat."""
    editable = ["kerdes", "helyes_valasz", "hint", "magyarazat", "neh", "szint",
                "feladat_tipus", "max_pont"]
    print(f"\n  Szerkeszthető mezők: {', '.join(editable)}")
    print("  (Üres Enter = mező megtartása)")

    updates: dict = {}
    for field_name in editable:
        current = getattr(f, field_name)
        val = input(f"  {field_name} [{current}]: ").strip()
        if val:
            updates[field_name] = val

    if not updates:
        return f

    try:
        neh = int(updates.get("neh", f.neh))
        if neh not in (1, 2, 3):
            print("  neh érvénytelen (1-3), eredeti megtartva.")
            neh = f.neh
    except (TypeError, ValueError):
        neh = f.neh

    try:
        max_pont = int(updates.get("max_pont", f.max_pont))
        if max_pont < 1:
            print("  max_pont legalább 1 legyen, eredeti megtartva.")
            max_pont = f.max_pont
    except (TypeError, ValueError):
        max_pont = f.max_pont

    return dataclasses.replace(
        f,
        kerdes=updates.get("kerdes", f.kerdes),
        helyes_valasz=updates.get("helyes_valasz", f.helyes_valasz),
        hint=updates.get("hint", f.hint),
        magyarazat=updates.get("magyarazat", f.magyarazat),
        neh=neh,
        szint=updates.get("szint", f.szint),
        feladat_tipus=updates.get("feladat_tipus", f.feladat_tipus),
        max_pont=max_pont,
    )


# ---------------------------------------------------------------------------
# AI review
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = """\
Felvételi feladat minőség-ellenőrző vagy. Megkapod az eredeti PDF szöveg releváns oldalát
és a belőle kinyert feladat rekordot. Ellenőrizd:
1. A kérdés érthető-e és egyértelműen következik-e a szövegből.
2. A helyes válasz korrekt-e a szöveg alapján.
3. Az elfogadott válaszok listája teljes-e (pl. tört és tizedes alak egyaránt szerepel).
4. A feladat típusa helyes-e.
5. A max_pont helyes-e az útmutató alapján.
6. A hint és a magyarázat segítőek és helyesek-e.
7. A nehézségi szint realisális-e.
8. Az abra_van flag helyes-e (valóban hivatkozik-e a feladat ábrára).

Ha javítás szükséges, add meg a javított mezőket. Ha minden rendben, térj vissza üres
"javitasok" objektummal.

Válaszolj JSON-ben:
{
  "ok": bool,
  "megjegyzes": "rövid összefoglaló (1-2 mondat)",
  "javitasok": {
    "kerdes": "...",          // csak ha változott
    "helyes_valasz": "...",   // csak ha változott
    "elfogadott_valaszok": ["..."],  // csak ha változott
    "feladat_tipus": "...",   // csak ha változott
    "max_pont": 1,            // csak ha változott
    "hint": "...",            // csak ha változott
    "magyarazat": "...",      // csak ha változott
    "neh": 1|2|3,             // csak ha változott
    "abra_van": true|false    // csak ha változott
  }
}
"""

_REVIEW_USER_TEMPLATE = """\
## Feladat rekord
{feladat_json}

## Releváns szöveg a PDF-ből (feladatlap {oldal_info})
{szoveg_reszlet}
"""


def _make_openai_client():
    from openai import OpenAI
    return OpenAI(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


def _extract_page(full_text: str, page_no: int | None) -> str:
    """Return the text of the given page from a [Oldal N]-marked string.
    Falls back to the first 3000 characters if page_no is None or not found."""
    if page_no is None:
        return full_text[:3000]
    import re
    pattern = re.compile(
        rf"\[Oldal\s+{page_no}\](.*?)(?=\[Oldal\s+\d+\]|$)", re.DOTALL
    )
    m = pattern.search(full_text)
    return m.group(1).strip() if m else full_text[:3000]


def review_feladat_ai(
    feladat: Feladat,
    fl_full_text: str,
    megjegyzes: str | None = None,
    *,
    model: str | None = None,
) -> Feladat:
    """Run a GPT review pass on a single feladat.

    *fl_full_text* must be the [Oldal N]-marked feladatlap text (as produced by
    pdf_parser.pdf_to_text).  The relevant page is extracted automatically using
    feladat.feladat_oldal.

    Returns a (possibly corrected) Feladat with review_elvegezve=True and
    review_megjegyzes set.
    """
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent.parent.parent / ".env", override=True)

    client = _make_openai_client()
    model = model or os.getenv("LLM_MODEL", "gpt-4o")

    page_text = _extract_page(fl_full_text, feladat.feladat_oldal)
    oldal_info = f"– {feladat.feladat_oldal}. oldal" if feladat.feladat_oldal else "(oldal ismeretlen)"

    feladat_dict = {
        "id": feladat.id,
        "kerdes": feladat.kerdes,
        "helyes_valasz": feladat.helyes_valasz,
        "elfogadott_valaszok": feladat.elfogadott_valaszok,
        "feladat_tipus": feladat.feladat_tipus,
        "max_pont": feladat.max_pont,
        "hint": feladat.hint,
        "magyarazat": feladat.magyarazat,
        "neh": feladat.neh,
        "szint": feladat.szint,
        "abra_van": feladat.abra_van,
        "kontextus": feladat.kontextus,
    }
    if megjegyzes:
        feladat_dict["felhasznalo_megjegyzes"] = megjegyzes

    prompt = _REVIEW_USER_TEMPLATE.format(
        feladat_json=json.dumps(feladat_dict, ensure_ascii=False, indent=2),
        oldal_info=oldal_info,
        szoveg_reszlet=page_text[:4000],
    )

    logger.info("AI review: %s", feladat.id)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, IndexError) as exc:
        logger.warning("AI review parse error for %s: %s", feladat.id, exc)
        return dataclasses.replace(
            feladat,
            review_elvegezve=True,
            review_megjegyzes=megjegyzes or "AI review failed – no changes applied.",
        )

    javitasok: dict = result.get("javitasok") or {}
    ai_megjegyzes: str = result.get("megjegyzes", "")
    final_megjegyzes = f"{megjegyzes}\n\n{ai_megjegyzes}".strip() if megjegyzes else ai_megjegyzes

    # Apply corrections (only permitted fields, with validation)
    updates: dict = {}
    for field_name in ("kerdes", "helyes_valasz", "hint", "magyarazat", "szint", "feladat_tipus"):
        if field_name in javitasok and javitasok[field_name]:
            updates[field_name] = str(javitasok[field_name])
    if "elfogadott_valaszok" in javitasok and isinstance(javitasok["elfogadott_valaszok"], list):
        updates["elfogadott_valaszok"] = [str(v) for v in javitasok["elfogadott_valaszok"]]
    if "max_pont" in javitasok:
        try:
            mp = int(javitasok["max_pont"])
            if mp >= 1:
                updates["max_pont"] = mp
        except (TypeError, ValueError):
            pass
    if "neh" in javitasok:
        try:
            neh = int(javitasok["neh"])
            if neh in (1, 2, 3):
                updates["neh"] = neh
        except (TypeError, ValueError):
            pass
    if "abra_van" in javitasok:
        updates["abra_van"] = bool(javitasok["abra_van"])

    return dataclasses.replace(
        feladat,
        **updates,
        review_elvegezve=True,
        review_megjegyzes=final_megjegyzes or None,
    )
