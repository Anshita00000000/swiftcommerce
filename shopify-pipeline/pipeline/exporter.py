"""
exporter.py
Writes normalized products to a Shopify-compatible CSV.
Handles multi-image rows correctly: one row per image,
with most fields blank on continuation rows.
"""

import csv
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SHOPIFY_COLUMNS = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Variant SKU",
    "Variant Price",
    "Image Src",
    "Image Position",
    "Variant Metafield: custom.source_url",
]


def export_csv(
    products: List[Dict[str, Any]],
    output_path: str,
) -> None:
    """
    Write products to a Shopify CSV file.

    Multi-image products generate multiple rows:
    - Row 1: all fields populated + first image
    - Row N: only Handle + Image Src + Image Position populated

    Args:
        products: List of normalized product dicts.
        output_path: Full path to output CSV file.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for product in products:
        product_rows = _product_to_rows(product)
        rows.extend(product_rows)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHOPIFY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Exported {len(products)} products ({len(rows)} rows) to {output_path}")


def _product_to_rows(product: dict) -> List[Dict[str, str]]:
    """Convert one product dict to one or more CSV row dicts."""
    handle = product.get("handle", "")
    images = product.get("images", [])

    # Build the primary row
    primary = {
        "Handle": handle,
        "Title": product.get("title", ""),
        "Body (HTML)": product.get("body_html", ""),
        "Vendor": product.get("vendor", ""),
        "Type": product.get("type", ""),
        "Tags": product.get("tags", ""),
        "Published": product.get("published", "TRUE"),
        "Option1 Name": product.get("option1_name", "Title"),
        "Option1 Value": product.get("option1_value", "Default Title"),
        "Variant SKU": product.get("sku", ""),
        "Variant Price": product.get("price", ""),
        "Image Src": images[0] if images else "",
        "Image Position": "1" if images else "",
        "Variant Metafield: custom.source_url": product.get("source_url", ""),
    }

    rows = [primary]

    # Additional image rows — only Handle, Image Src, Image Position
    for i, img_src in enumerate(images[1:], 2):
        continuation = {col: "" for col in SHOPIFY_COLUMNS}
        continuation["Handle"] = handle
        continuation["Image Src"] = img_src
        continuation["Image Position"] = str(i)
        rows.append(continuation)

    return rows
