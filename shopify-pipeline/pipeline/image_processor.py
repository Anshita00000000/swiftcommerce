"""
image_processor.py

Takes raw downloaded images for one product and produces processed versions
ready for Shopify upload.

Output path
───────────
  outputs/{brand}/listing/{timestamp}/images/{handle}/processed/

Filename convention: {sku}_{n}.webp  (n is 1-indexed)
If no sku is supplied, handle is used instead.

Processing rules (applied to every image)
──────────────────────────────────────────
  Transparent image (cutout):
    • Detect: mode == "RGBA" and minimum alpha value < 255
    • Place on a white 700×700 canvas
    • Scale image to fit within 630×630 (90 % of canvas), centred

  Opaque image (lifestyle / solid):
    • Resize to fit within 700×700 maintaining aspect ratio
    • No padding, no cropping

  Both cases:
    • Convert to WebP at 90 % quality

Error handling
──────────────
  A single image failure logs a warning and is skipped — the pipeline
  continues with the remaining images.
  Missing Pillow raises ImportError with install instructions.

Public API
──────────
process_images(raw_paths, handle, ctx, sku="") -> list[str]
    Returns list of paths to successfully processed images.
"""

import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# Canvas dimensions used for all output images
_CANVAS_SIZE   = 700
_CUTOUT_SIZE   = int(_CANVAS_SIZE * 0.9)   # 630 px — cutout image max dimension

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


def process_images(
    raw_paths: List[str],
    handle: str,
    ctx,
    sku: str = "",
) -> List[str]:
    """
    Process raw downloaded images and write WebP output files.

    Args:
        raw_paths: Local file paths returned by image_downloader.download_images().
        handle:    Shopify product handle — used to locate the processed/ folder
                   via ctx.images_path(handle).
        ctx:       RunContext instance; provides ctx.images_path(handle).
        sku:       Product SKU used as the filename prefix.  Falls back to handle
                   when empty.

    Returns:
        List of absolute paths to successfully written processed images.
    """
    if not _PIL_AVAILABLE:
        raise ImportError(
            "Pillow is required for image processing. "
            "Install it with: pip install Pillow"
        )

    if not raw_paths:
        return []

    name_prefix = sku.strip() if sku.strip() else handle

    processed_dir: Path = ctx.images_path(handle) / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    output_paths: List[str] = []
    n = 1  # 1-indexed output counter (only increments on success)

    for raw_path in raw_paths:
        try:
            out_path = processed_dir / f"{name_prefix}_{n}.webp"
            img      = _load_and_process(raw_path)
            img.save(out_path, "WEBP", quality=90)
            output_paths.append(str(out_path))
            logger.debug("  [proc] %s -> %s", Path(raw_path).name, out_path.name)
            n += 1
        except Exception as e:
            logger.warning("  [proc] Failed to process %s: %s", raw_path, e)

    return output_paths


# ---------------------------------------------------------------------------
# Processing logic
# ---------------------------------------------------------------------------

def _load_and_process(raw_path: str) -> "Image.Image":
    """
    Open one raw image, apply the appropriate processing rule, and return
    a 700×700 (or smaller) RGB/RGBA Pillow image ready to save as WebP.
    """
    img = Image.open(raw_path)

    if _is_transparent(img):
        return _process_cutout(img)
    else:
        return _process_opaque(img)


def _is_transparent(img: "Image.Image") -> bool:
    """
    Return True when the image has an alpha channel and at least one pixel
    with alpha < 255 (i.e. it is a cutout / transparent PNG).
    """
    if img.mode != "RGBA":
        return False
    # getextrema() on the alpha channel returns (min, max)
    alpha_min = img.split()[3].getextrema()[0]
    return alpha_min < 255


def _process_cutout(img: "Image.Image") -> "Image.Image":
    """
    Place a transparent (cutout) image onto a white 700×700 canvas.
    The image is scaled to fit within 630×630 (90 %), centred.
    """
    # Ensure RGBA so we can use the alpha channel for compositing
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Scale to fit within _CUTOUT_SIZE × _CUTOUT_SIZE
    img.thumbnail((_CUTOUT_SIZE, _CUTOUT_SIZE), Image.LANCZOS)

    canvas = Image.new("RGBA", (_CANVAS_SIZE, _CANVAS_SIZE), (255, 255, 255, 255))
    x = (_CANVAS_SIZE - img.width)  // 2
    y = (_CANVAS_SIZE - img.height) // 2
    canvas.paste(img, (x, y), mask=img)

    return canvas.convert("RGB")


def _process_opaque(img: "Image.Image") -> "Image.Image":
    """
    Resize an opaque (lifestyle / solid) image to fit within 700×700,
    maintaining aspect ratio with no padding or cropping.
    """
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    img.thumbnail((_CANVAS_SIZE, _CANVAS_SIZE), Image.LANCZOS)
    return img
