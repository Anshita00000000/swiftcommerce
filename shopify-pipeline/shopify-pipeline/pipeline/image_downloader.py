"""
image_downloader.py

Downloads product images into the brand-level images folder.

Output path
───────────
  outputs/{brand}/images/raw/{handle}/

Re-run safe: if the file already exists it is skipped without re-downloading.

Public API
──────────
download_images(image_urls, handle, ctx, config) -> list[str]
    Returns list of local file paths for successfully downloaded raw images.
"""

import logging
import os
import time
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def download_images(
    image_urls: List[str],
    handle: str,
    ctx,
    config: dict,
) -> List[str]:
    """
    Download a list of image URLs for one product.

    Args:
        image_urls: List of image URLs (already normalized to https://).
        handle:     Shopify product handle — used as the per-product folder name.
        ctx:        RunContext instance; provides ctx.brand_images_raw_path(handle)
                    for the output directory.
        config:     Adapted config dict; anti_bot.image_delay controls the
                    per-download pause (falls back to anti_bot.page_delay, then 0.5s).

    Returns:
        List of local file paths for successfully downloaded raw images.
    """
    if not image_urls:
        return []

    anti_bot = config.get("anti_bot", {})
    delay = float(
        anti_bot.get("image_delay",
        anti_bot.get("page_delay", 0.5))
    )

    raw_dir: Path = ctx.brand_images_raw_path(handle)

    local_paths: List[str] = []

    for i, url in enumerate(image_urls, 1):
        try:
            filename   = _url_to_filename(url, i)
            local_path = raw_dir / filename

            if local_path.exists():
                logger.debug("  [img] Already exists, skipping: %s", filename)
                local_paths.append(str(local_path))
                continue

            logger.debug("  [img] Downloading %d: %s", i, url)
            resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
            resp.raise_for_status()

            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            local_paths.append(str(local_path))
            logger.debug("  [img] Saved: %s", filename)

            if delay:
                time.sleep(delay)

        except Exception as e:
            logger.warning("  [img] Failed to download %s: %s", url, e)

    return local_paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_to_filename(url: str, index: int) -> str:
    """
    Derive a safe filename from an image URL.
    Falls back to 'image_{index:03d}.jpg' if the URL has no clear filename.
    """
    try:
        parsed   = urlparse(url)
        basename = os.path.basename(parsed.path).split("?")[0]
        if basename and "." in basename and len(basename) < 200:
            return "".join(c if c.isalnum() or c in "._-" else "_" for c in basename)
    except Exception:
        pass
    return f"image_{index:03d}.jpg"
