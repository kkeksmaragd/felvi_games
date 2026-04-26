"""
medal_assets.py
---------------
Lazy asset resolver and generator for medals.

Asset layout (all under <assets_dir>/eremek/<erem_id>/):
    kep.png   – DALL-E 3 generated PNG  (or user-supplied)
    hang.mp3  – TTS award fanfare MP3   (or user-supplied)
    gif.gif   – User-supplied / URL-linked animated GIF

Asset resolution order (for each kind):
    1. Local file at medal_asset_path(erem_id, kind) if it exists  → bytes
    2. Erem.kep_url / hang_url / gif_url if set                    → URL string
    3. None  (caller shows fallback / emoji)

Usage::

    # display in Streamlit
    kep = get_medal_asset(erem, "kep")   # bytes or None
    gif = get_medal_asset(erem, "gif")   # bytes or None
    hang = get_medal_asset(erem, "hang") # bytes or None

    # pre-generate (CLI / background job)
    generate_medal_assets(erem, kinds=("kep", "hang"))
"""
from __future__ import annotations

from pathlib import Path

from felvi_games.config import medal_asset_path
from felvi_games.models import Erem


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_medal_asset(erem: Erem, kind: str) -> bytes | str | None:
    """Return asset bytes (local file), a URL string, or None.

    Args:
        erem: The medal catalog entry.
        kind: ``"kep"``, ``"hang"``, or ``"gif"``.

    Returns:
        - ``bytes`` if a local file exists.
        - ``str`` (URL) if only a URL is catalogued.
        - ``None`` if no asset is available.
    """
    local = medal_asset_path(erem.id, kind)
    if local.exists():
        return local.read_bytes()

    url_map = {"kep": erem.kep_url, "hang": erem.hang_url, "gif": erem.gif_url}
    return url_map.get(kind)


def medal_asset_exists(erem_id: str, kind: str) -> bool:
    """True if a local asset file is present for this medal and kind."""
    return medal_asset_path(erem_id, kind).exists()


# ---------------------------------------------------------------------------
# Write / generate
# ---------------------------------------------------------------------------

def generate_medal_assets(
    erem: Erem,
    kinds: tuple[str, ...] = ("kep", "hang"),
    overwrite: bool = False,
) -> dict[str, Path]:
    """Generate and save local medal assets.

    Calls OpenAI (DALL-E 3 for image, TTS for sound).
    GIF generation is not supported automatically – place ``gif.gif``
    manually or set ``erem.gif_url`` to an external URL.

    Args:
        erem:      Medal catalog entry.
        kinds:     Which asset types to generate.  Defaults to image+sound.
        overwrite: Re-generate even if the file already exists.

    Returns:
        Dict mapping kind → saved absolute Path for each successfully
        generated asset.
    """
    from felvi_games.ai import generate_medal_hang, generate_medal_image

    saved: dict[str, Path] = {}

    for kind in kinds:
        if kind == "gif":
            continue  # cannot auto-generate GIFs

        dest = medal_asset_path(erem.id, kind)
        if dest.exists() and not overwrite:
            saved[kind] = dest
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)

        if kind == "kep":
            data = generate_medal_image(erem.nev, erem.leiras, erem.ikon)
            dest.write_bytes(data)
            saved[kind] = dest

        elif kind == "hang":
            data = generate_medal_hang(erem.nev, erem.leiras)
            dest.write_bytes(data)
            saved[kind] = dest

    return saved
