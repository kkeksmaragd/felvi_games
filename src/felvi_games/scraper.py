"""
scraper.py
----------
Letölti az összes felvételi feladatsort és javítási útmutatót az
oktatas.hu-ról. PDFeket és egyéb mellékleteket rendezett mappastruktúrában
menti el.

Mappastruktúra:
  exams/
    6_osztaly/
      2026/
        matek_feladatlap.pdf
        matek_megoldas.pdf
        magyar_feladatlap.pdf
        magyar_megoldas.pdf
    8_osztaly/
      ...
    9_evfolyam/
      ...

Használat (telepítés után):
  felvi-scraper

  # Csak az utolsó N év:
  felvi-scraper --years 3

  # Csak egy kategória:
  felvi-scraper --only 6

  # Száraz futás (csak listázza, nem tölt le):
  felvi-scraper --dry-run

Közvetlen futtatás (fejlesztéshez):
  python -m felvi_games.scraper
"""

import argparse
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from felvi_games.models import KATEGORIA_INFO, KategoriaKulcs, KategoriaNevezektan  # noqa: F401

# ---------------------------------------------------------------------------
# Konstansok
# ---------------------------------------------------------------------------

BASE_URL = "https://www.oktatas.hu"
INDEX_URL = (
    "https://www.oktatas.hu"
    "/kozneveles/kozepfoku_felveteli_eljaras/kozponti_feladatsorok"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
}

# Project root = src/felvi_games/../../.. → three levels up from this file
_PROJECT_ROOT = Path(__file__).parent.parent.parent
OUTPUT_DIR = _PROJECT_ROOT / "exams"

# Delay letöltések között (másodperc) – udvariasság a szervernek
REQUEST_DELAY = 0.8

# Bulk ZIP letöltési URL-ek évfolyamonként
BULK_ZIPS = {
    "4": (
        "https://www.oktatas.hu/pub_bin/dload/kozoktatas/beiskolazas/"
        "feladatsorok/felveteli_feladatsorok_9_evfolyamra.zip",
        "9_evfolyam",
    ),
    "8": (
        "https://www.oktatas.hu/pub_bin/dload/kozoktatas/beiskolazas/"
        "feladatsorok/felveteli_feladatsorok_8_osztalyos_gimnaziumba.zip",
        "8_osztaly",
    ),
    "6": (
        "https://www.oktatas.hu/pub_bin/dload/kozoktatas/beiskolazas/"
        "feladatsorok/felveteli_feladatsorok_6_osztalyos_gimnaziumba.zip",
        "6_osztaly",
    ),
}

# CLI kulcs → mappa neve (KATEGORIA_INFO-ból vezetve le)
_CLI_TO_MAPPA: dict[str, str] = {
    info.cli_kulcs: kulcs.value for kulcs, info in KATEGORIA_INFO.items()
}
_CLI_KULCSOK: list[str] = list(_CLI_TO_MAPPA.keys())

# ---------------------------------------------------------------------------
# Segédfüggvények
# ---------------------------------------------------------------------------


def download_and_extract_zip(url: str, dest_dir: Path, dry_run: bool = False) -> int:
    """
    Letölti a ZIP-et és kicsomagolja dest_dir-be.
    Visszatér a kicsomagolt fájlok számával.
    """
    if dry_run:
        print(f"  [DRY] ZIP letöltés: {url} → {dest_dir}")
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "_download.zip"

    print(f"  Letöltés: {url}")
    try:
        resp = session.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
        size_mb = downloaded / 1_048_576
        print(f"  Letöltve: {size_mb:.1f} MB")
    except Exception as e:
        print(f"  [HIBA] Letöltés: {e}")
        return 0

    print(f"  Kicsomagolás: {dest_dir}")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            zf.extractall(dest_dir)
        zip_path.unlink()  # takarítás
        print(f"  [OK] {len(members)} fájl kicsomagolva")
        return len(members)
    except zipfile.BadZipFile as e:
        print(f"  [HIBA] Kicsomagolás: {e}")
        zip_path.unlink(missing_ok=True)
        return 0

session = requests.Session()
session.headers.update(HEADERS)


def get_soup(url: str) -> BeautifulSoup:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return BeautifulSoup(resp.content, "html.parser")


def safe_filename(name: str) -> str:
    """Biztonságos fájlnevet készít egy URL-ből vagy szövegből."""
    name = re.sub(r"[^\w.\-]", "_", name)
    return name[:200]


def kategoria_mappa(href: str) -> str:
    """URL alapján meghatározza a kategória mappáját."""
    h = href.lower()
    if "9_evfolyam" in h or "9evfolyam" in h or "9._evfolyam" in h:
        return "9_evfolyam"
    if "8_osztaly" in h or "8osztalyos" in h or "8_evfolyam" in h:
        return "8_osztaly"
    return "6_osztaly"


def ev_szam(href: str) -> str | None:
    """Kinyeri az évet a href-ből vagy szövegből."""
    m = re.search(r"[_/\-](\d{4})[_/\-]", href)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4})", href)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 1. lépés: Főoldal – évenkénti linkek összegyűjtése
# ---------------------------------------------------------------------------

def scrape_year_links() -> list[dict]:
    """
    Visszaad egy listát:
    [{"year": "2026", "kategoria": "6_osztaly", "url": "https://..."}]
    """
    print(f"Főoldal scraping: {INDEX_URL}")
    soup = get_soup(INDEX_URL)

    results = []

    # A táblázatban vannak az évenkénti linkek
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue

            # Első cella: év (pl. "2026.")
            year_text = cells[0].get_text(strip=True).rstrip(".")
            if not re.match(r"^\d{4}$", year_text):
                continue

            # Többi cella: kategória linkek
            for cell in cells[1:]:
                for a in cell.find_all("a", href=True):
                    href = a["href"]
                    full_url = urljoin(BASE_URL, href)
                    kat = kategoria_mappa(href)
                    results.append(
                        {"year": year_text, "kategoria": kat, "url": full_url}
                    )

    # Deduplikáció
    seen = set()
    unique = []
    for r in results:
        key = r["url"]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    print(f"  → {len(unique)} év/kategória oldal találva")
    return unique


# ---------------------------------------------------------------------------
# 2. lépés: Éves aloldalak – PDF linkek összegyűjtése
# ---------------------------------------------------------------------------

def scrape_pdf_links(page_url: str) -> list[dict]:
    """
    Adott éves aloldalon megkeresi az összes PDF (és egyéb dokumentum) linket.
    Visszaad: [{"url": "...", "filename": "..."}]
    """
    try:
        soup = get_soup(page_url)
    except Exception as e:
        print(f"    [HIBA] {page_url}: {e}")
        return []

    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Csak fájl letöltő linkek (pub_bin/dload vagy direkt PDF)
        if "pub_bin/dload" not in href and not href.lower().endswith(
            (".pdf", ".doc", ".docx", ".zip")
        ):
            continue

        full_url = urljoin(BASE_URL, href)

        # Fájlnév kinyerése URL-ből
        raw_name = urlparse(full_url).path.split("/")[-1]
        filename = safe_filename(raw_name) if raw_name else safe_filename(href[-50:])

        # Tipus jelölő a linkszövegből
        link_text = a.get_text(strip=True).lower()
        targy = "ismeretlen"
        if "matem" in link_text or "matek" in link_text:
            targy = "matek"
        elif "magyar" in link_text:
            targy = "magyar"

        javitas = "javitas" if any(
            x in link_text for x in ["javít", "megold", "útmutató", "utmutato"]
        ) else "feladatlap"

        found.append(
            {
                "url": full_url,
                "filename": filename,
                "targy": targy,
                "tipus": javitas,
                "link_szoveg": a.get_text(strip=True),
            }
        )

    return found


# ---------------------------------------------------------------------------
# 3. lépés: Letöltés
# ---------------------------------------------------------------------------

def download_file(url: str, dest: Path) -> bool:
    """Fájl letöltése. True ha sikeres, False ha kihagyta (már létezik)."""
    if dest.exists():
        print(f"    [KÉSZ] {dest.name}")
        return False

    try:
        resp = session.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        time.sleep(REQUEST_DELAY)
        print(f"    [OK] {dest.name}")
        return True
    except Exception as e:
        print(f"    [HIBA] {dest.name}: {e}")
        return False


# ---------------------------------------------------------------------------
# Főprogram
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="oktatas.hu felvételi feladatsor letöltő"
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help=(
            "Bulk ZIP letöltés (gyors, minden évet egyszerre). "
            "Kombináld --only-val a kategória szűréséhez."
        ),
    )
    parser.add_argument(
        "--years",
        type=int,
        default=0,
        help="Csak az utolsó N évet töltse le (0 = mind, csak PDF módban)",
    )
    parser.add_argument(
        "--only",
        choices=_CLI_KULCSOK,
        default=None,
        help="Csak egy kategória: 4 (4 oszt.), 6 (6 oszt.) vagy 8 (8 oszt.)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Csak listázza a találtakat, nem tölt le semmit",
    )
    parser.add_argument(
        "--output",
        default=str(_PROJECT_ROOT / "exams"),
        help="Kimeneti mappa (alap: <project_root>/exams)",
    )
    args = parser.parse_args()

    global OUTPUT_DIR
    OUTPUT_DIR = Path(args.output)

    # -----------------------------------------------------------------------
    # ZIP mód: bulk letöltés és kicsomagolás
    # -----------------------------------------------------------------------
    if args.zip:
        targets = (
            {args.only: BULK_ZIPS[args.only]}
            if args.only
            else BULK_ZIPS
        )
        for evfolyam, (url, subdir) in targets.items():
            print(f"\n[{evfolyam}. évfolyam] {url}")
            download_and_extract_zip(
                url, OUTPUT_DIR / subdir, dry_run=args.dry_run
            )
        if not args.dry_run:
            print(f"\n✓ Kész! Fájlok helye: {OUTPUT_DIR.resolve()}")
        return

    # 1. Évenkénti linkek
    year_links = scrape_year_links()

    # Szűrés kategóriára
    if args.only:
        kat_filter = _CLI_TO_MAPPA[args.only]
        year_links = [l for l in year_links if l["kategoria"] == kat_filter]

    # Szűrés évre
    if args.years > 0:
        all_years = sorted(
            {l["year"] for l in year_links}, reverse=True
        )[: args.years]
        year_links = [l for l in year_links if l["year"] in all_years]

    year_links.sort(key=lambda x: (x["year"], x["kategoria"]), reverse=True)

    total_downloaded = 0
    total_skipped = 0
    total_errors = 0

    for entry in year_links:
        year = entry["year"]
        kat = entry["kategoria"]
        url = entry["url"]

        print(f"\n[{year}] {kat} — {url}")

        pdf_links = scrape_pdf_links(url)

        if not pdf_links:
            print("  (nem találtunk letölthető fájlt ezen az oldalon)")
            continue

        for item in pdf_links:
            dest_dir = OUTPUT_DIR / kat / year
            dest = dest_dir / item["filename"]

            if args.dry_run:
                print(f"  [DRY] {dest} ← {item['url']}")
                continue

            ok = download_file(item["url"], dest)
            if ok:
                total_downloaded += 1
            else:
                total_skipped += 1

    if not args.dry_run:
        print(
            f"\n✓ Kész! Letöltve: {total_downloaded}, "
            f"Kihagyva (már megvolt): {total_skipped}, "
            f"Hiba: {total_errors}"
        )
        print(f"Fájlok helye: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
