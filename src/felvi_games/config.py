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
  FELVI_LOG_DIR – Log fájlok mappája
                  alap: <FELVI_DB szülőkönyvtára>/logs
  FELVI_LOG_LEVEL – Napló szint: DEBUG | INFO | WARNING (alap: INFO)

Az asset fájlok struktúrája (egy szint mélység):
  <assets_dir>/<mappa_nev>/<feladat_id>_kerdes.mp3
  <assets_dir>/<mappa_nev>/<feladat_id>_magyarazat.mp3

ahol <mappa_nev> = <szint>_<ev>_v<valtozat>  (pl. "6_osztaly_2025_v1")
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

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


def get_exams_dir() -> Path:
    """Root folder for downloaded exam PDFs.
    Env var FELVI_EXAMS overrides; default: <project_root>/exams."""
    raw = os.environ.get("FELVI_EXAMS", "")
    if raw:
        return Path(raw)
    return _PROJECT_ROOT / "exams"


def get_log_dir() -> Path:
    raw = os.environ.get("FELVI_LOG_DIR", "")
    if raw:
        return Path(raw)
    return get_db_path().parent / "logs"


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """Configure root + felvi_games logger.

    Call once at startup (app.py main, cli.py app callback).
    Safe to call multiple times — idempotent via handler check.

    Log files (rotating, 5 MB × 5 backups):
      <log_dir>/felvi.log        – INFO+ for all felvi_games.*
      <log_dir>/rewards.log      – DEBUG+ for felvi_games.achievements only
    """
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    level_name = os.environ.get("FELVI_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # --- felvi_games root logger -----------------------------------------
    pkg_logger = logging.getLogger("felvi_games")
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in pkg_logger.handlers):
        return  # already set up

    pkg_logger.setLevel(logging.DEBUG)  # handlers control the effective level

    main_handler = logging.handlers.RotatingFileHandler(
        log_dir / "felvi.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    main_handler.setLevel(level)
    main_handler.setFormatter(fmt)
    pkg_logger.addHandler(main_handler)

    # --- rewards.log: DEBUG-level achievements logger --------------------
    rewards_handler = logging.handlers.RotatingFileHandler(
        log_dir / "rewards.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    rewards_handler.setLevel(logging.DEBUG)
    rewards_handler.setFormatter(fmt)

    rewards_logger = logging.getLogger("felvi_games.achievements")
    rewards_logger.addHandler(rewards_handler)

    # Console: WARNING+ so Streamlit terminal stays quiet
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(fmt)
    pkg_logger.addHandler(console_handler)


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


# ---------------------------------------------------------------------------
# Medal asset helpers
# ---------------------------------------------------------------------------

def medal_asset_dir(erem_id: str) -> Path:
    """Root folder for all assets belonging to one medal.

    Layout::

        <assets_dir>/eremek/<erem_id>/
            kep.png        ← AI-generated or user-supplied image
            hang.mp3       ← TTS / user-supplied award sound
            gif.gif        ← user-supplied or URL-referenced animation

    The directory is created lazily by the writer; readers should use
    ``medal_asset_path(erem_id, kind).exists()`` before reading.
    """
    return get_assets_dir() / "eremek" / erem_id


def medal_asset_path(erem_id: str, kind: str) -> Path:
    """Absolute path to one specific medal asset file.

    Args:
        erem_id: Medal slug, e.g. ``"elso_menet"``.
        kind:    One of ``"kep"`` (PNG), ``"hang"`` (MP3), ``"gif"`` (GIF).
    """
    ext = {"kep": "png", "hang": "mp3", "gif": "gif"}.get(kind, kind)
    return medal_asset_dir(erem_id) / f"{kind}.{ext}"
