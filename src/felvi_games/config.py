"""
config.py
---------
Központi konfiguráció env változókból.

Env változók:
  FELVI_DB      – SQLite adatbázis elérési útja
                  alap: <project_root>/data/felvi.db
  FELVI_ASSETS  – Asset (TTS MP3) mappa gyökere
                  alap: <project_root>/data/assets
                  Ha relatív, a FELVI_DB szülőkönyvtárához képest értendő.

Az asset fájlok struktúrája (egy szint mélység):
  <assets_dir>/<mappa_nev>/<feladat_id>_kerdes.mp3
  <assets_dir>/<mappa_nev>/<feladat_id>_magyarazat.mp3

ahol <mappa_nev> = <szint>_<ev>_v<valtozat>  (pl. "6_osztaly_2025_v1")
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_db_path() -> Path:
    raw = os.environ.get("FELVI_DB", "")
    if raw:
        return Path(raw)
    return _PROJECT_ROOT / "data" / "felvi.db"


def get_assets_dir() -> Path:
    raw = os.environ.get("FELVI_ASSETS", "")
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = get_db_path().parent / p
        return p
    return get_db_path().parent / "assets"


# ---------------------------------------------------------------------------
# Asset path helpers
# ---------------------------------------------------------------------------

def asset_subfolder(szint: str, ev: int | None, valtozat: int | None) -> str:
    """
    Egy szint mélységű mappa neve az exam forrásból.
    Példa: "6_osztaly_2025_v1"  |  "8_osztaly_unknown"
    """
    ev_str = str(ev) if ev is not None else "unknown"
    val_str = f"v{valtozat}" if valtozat is not None else "v0"
    # szint értéke pl. "6 osztályos" → "6_osztályos"
    szint_slug = szint.replace(" ", "_")
    return f"{szint_slug}_{ev_str}_{val_str}"


def asset_path(
    feladat_id: str,
    kind: str,          # "kerdes" | "magyarazat"
    szint: str,
    ev: int | None,
    valtozat: int | None,
) -> Path:
    """Abszolút elérési út egy TTS asset fájlhoz."""
    subfolder = asset_subfolder(szint, ev, valtozat)
    return get_assets_dir() / subfolder / f"{feladat_id}_{kind}.mp3"


def relative_asset_path(
    feladat_id: str,
    kind: str,
    szint: str,
    ev: int | None,
    valtozat: int | None,
) -> str:
    """
    DB-be mentendő relatív elérési út (az assets_dir gyökeréhez képest).
    Példa: "6_osztályos_2025_v1/m001_kerdes.mp3"
    """
    subfolder = asset_subfolder(szint, ev, valtozat)
    return f"{subfolder}/{feladat_id}_{kind}.mp3"


def resolve_asset(relative_path: str) -> Path:
    """Relatív asset útvonalból abszolút Path."""
    return get_assets_dir() / relative_path


# ---------------------------------------------------------------------------
# Text cache helpers (intermediate PDF extraction results)
# ---------------------------------------------------------------------------

def text_cache_path(pdf_stem: str) -> Path:
    """
    Abszolút elérési út a PDF szöveg cache fájlhoz.
    Példa: <assets_dir>/text/M8_2025_1_fl.txt
    """
    return get_assets_dir() / "text" / f"{pdf_stem}.txt"


def relative_text_path(pdf_stem: str) -> str:
    """
    DB-be / modellbe mentendő relatív elérési út a szöveg cache-hez.
    Példa: "text/M8_2025_1_fl.txt"
    """
    return f"text/{pdf_stem}.txt"
