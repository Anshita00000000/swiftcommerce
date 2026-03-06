"""
image_downloader.py
Downloads product images to outputs/{brand}/images/{handle}/.
Re-run safe: skips already-downloaded images.
Normalizes URLs to https:// before deduplication.
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
    brand: str,
    output_root: str = "outputs",
    delay: float = 0.5,
) -> List[str]:
    """
    Download a list of image URLs for one product.

    Args:
        image_urls: List of image URLs (already normalized to https://).
        handle: Shopify product handle (used as folder name).
        brand: Brand name (used in output path).
        output_root: Base output directory.
        delay: Seconds to wait between downloads.

    Returns:
        List of local file paths for successfully downloaded images.
    """
    if not image_urls:
        return []

    img_dir = Path(output_root) / brand / "images" / handle
    img_dir.mkdir(parents=True, exist_ok=True)

    local_paths = []

    for i, url in enumerate(image_urls, 1):
        try:
            filename = _url_to_filename(url, i)
            local_path = img_dir / filename

            if local_path.exists():
                logger.debug(f"  [img] Already exists, skipping: {filename}")
                local_paths.append(str(local_path))
                continue

            logger.debug(f"  [img] Downloading {i}: {url}")
            resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
            resp.raise_for_status()

            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            local_paths.append(str(local_path))
            logger.debug(f"  [img] Saved: {filename}")

            if delay:
                time.sleep(delay)

        except Exception as e:
            logger.warning(f"  [img] Failed to download {url}: {e}")

    return local_paths


def _url_to_filename(url: str, index: int) -> str:
    """
    Derive a safe filename from an image URL.
    Falls back to '{index}.jpg' if the URL has no clear filename.
    """
    try:
        parsed = urlparse(url)
        basename = os.path.basename(parsed.path)
        # Strip query strings that may have been included in the path
        basename = basename.split("?")[0]
        # Only keep the filename if it has a recognizable extension
        if basename and "." in basename and len(basename) < 200:
            # Sanitize
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in basename)
            return safe
    except Exception:
        pass

    return f"image_{index:03d}.jpg"
